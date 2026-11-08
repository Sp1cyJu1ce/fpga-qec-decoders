// Measure every component syndrome of the distance-five spatial HDRG core.
// The two component engines are independent. Waiting for both takes the
// longer of their times, so their separate maxima bound every joint input.
#include "Vhdrg_online.h"
#include "distance_five_reference.hpp"
#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <iostream>
#include <map>
#include <stdexcept>

constexpr unsigned distance = 5;
constexpr unsigned side = 2 * distance - 1;
constexpr unsigned checks = distance * (distance - 1);
using CheckSupports = std::array<std::uint64_t, checks>;

CheckSupports check_supports(unsigned row_parity) {
    CheckSupports support{};
    unsigned check = 0;
    for (unsigned r = row_parity; r < side; r += 2) {
        for (unsigned c = 1 - row_parity; c < side; c += 2) {
            unsigned data = 0;
            for (unsigned y = 0; y < side; ++y) {
                for (unsigned x = y % 2; x < side; x += 2, ++data) {
                    const int dr = static_cast<int>(y) - static_cast<int>(r);
                    const int dc = static_cast<int>(x) - static_cast<int>(c);
                    if (dr * dr + dc * dc == 1)
                        support[check] |= std::uint64_t{1} << data;
                }
            }
            ++check;
        }
    }
    return support;
}

unsigned syndrome(std::uint64_t mask, const CheckSupports& supports) {
    unsigned result = 0;
    for (unsigned i = 0; i < checks; ++i)
        result |= (std::popcount(mask & supports[i]) % 2U) << i;
    return result;
}

void tick(Vhdrg_online& dut) {
    dut.clk = 0;
    dut.eval();
    dut.clk = 1;
    dut.eval();
}

// Count rising-edge intervals from acceptance to the first valid correction.
unsigned decode(Vhdrg_online& dut, unsigned z_checks, unsigned x_checks) {
    if (!dut.ready_in)
        throw std::runtime_error("Core busy before acceptance");
    dut.z_checks = z_checks;
    dut.x_checks = x_checks;
    dut.valid_in = 1;
    tick(dut);
    dut.valid_in = 0;
    unsigned cycles = 0;
    while (!dut.valid_out) {
        if (++cycles > 256)
            throw std::runtime_error("No completion within 256 clocks");
        tick(dut);
    }
    return cycles;
}

int main() {
    Vhdrg_online dut;
    dut.reset = 1;
    dut.valid_in = 0;
    tick(dut);
    dut.reset = 0;
    dut.eval();
    const auto z_support = check_supports(0), x_support = check_supports(1);
    std::array<unsigned, 2> worst_input{}, longest{};
    for (unsigned component = 0; component < 2; ++component) {
        std::map<unsigned, unsigned> histogram;
        const DistanceFiveReference reference(component);
        for (unsigned bits = 0; bits < (1U << checks); ++bits) {
            const unsigned z = component == 0 ? bits : 0;
            const unsigned x = component == 1 ? bits : 0;
            const unsigned cycles = decode(dut, z, x);
            const auto expected = reference.decode(bits);
            if (cycles != expected.cycles || dut.x_correction != (component == 0 ? expected.mask : 0) ||
                dut.z_correction != (component == 1 ? expected.mask : 0)) {
                std::cerr << "Clustering, masking or latency mismatch at component=" << component
                          << " syndrome=" << bits << '\n';
                return 1;
            }
            if (syndrome(dut.x_correction, z_support) != z ||
                syndrome(dut.z_correction, x_support) != x) {
                std::cerr << "Wrong parity for z_checks=" << z << " x_checks=" << x << '\n';
                return 1;
            }
            ++histogram[cycles];
            if (cycles > longest[component]) {
                longest[component] = cycles;
                worst_input[component] = bits;
            }
        }
        for (const auto& [latency, count] : histogram)
            std::cout << "component=" << component << " cycles=" << latency << " count=" << count << '\n';
        std::cout << "maximum component=" << component << " cycles=" << longest[component]
                  << " first_syndrome=" << worst_input[component] << std::endl;
    }
    const unsigned joint = decode(dut, worst_input[0], worst_input[1]);
    if (joint != std::max(longest[0], longest[1]) ||
        syndrome(dut.x_correction, z_support) != worst_input[0] ||
        syndrome(dut.z_correction, x_support) != worst_input[1])
        throw std::runtime_error("Combined worst-case input did not reproduce the maximum");
    std::cout << "PASS: 1048576 syndromes per component; combined maximum=" << joint << " cycles\n";
    dut.final();
}

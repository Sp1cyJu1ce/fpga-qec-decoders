// Check the two distance-five component engines against graph searches in software.
#pragma once
#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <cstdlib>
#include <stdexcept>

class DistanceFiveReference {
    static constexpr int sites = 20, side = 9;
    std::array<std::array<unsigned, sites>, 3> neighbours{};
    std::array<std::array<int, 2>, sites> boundary_distance{};
    std::array<std::array<std::uint64_t, 2>, sites> boundary_path{};
    std::array<std::array<std::uint64_t, sites>, sites> pair_path{};

public:
    explicit DistanceFiveReference(int component) {
        std::array<std::array<int, 2>,sites> checks{};
        std::array<std::array<int,side>,side> data_index{};
        int count = 0;
        for (int r = component; r < side; r += 2)
            for (int c = 1-component; c < side; c += 2) checks[count++] = {r, c};
        count = 0;
        for (int r = 0; r < side; ++r)
            for (int c = r%2; c < side; c += 2) data_index[r][c] = count++;
        auto walk = [&](std::array<int,2> start, std::array<int,2> end) {
            std::uint64_t path = 0;
            for (int axis = 0; axis < 2; ++axis) {
                while (start[axis] != end[axis]) {
                    int step = start[axis] < end[axis] ? 1 : -1;
                    start[axis] += step;
                    path |= std::uint64_t{1} << data_index[start[0]][start[1]];
                    start[axis] += step;
                }
            }
            return path;
        };
        for (int s = 0; s < sites; ++s) {
            for (int t = 0; t < sites; ++t) {
                int separation = std::max(std::abs(checks[s][0] - checks[t][0]),
                                          std::abs(checks[s][1] - checks[t][1])) / 2;
                for (int level = 0; level < 3; ++level)
                    if (separation <= (1 << level)) neighbours[level][s] |= 1U << t;
                pair_path[s][t] = walk(checks[s], checks[t]);
            }
            for (int edge = 0; edge < 2; ++edge) {
                auto end = checks[s];
                end[1-component] = edge == 0 ? -1 : side;
                boundary_distance[s][edge] = std::abs(end[1-component]-checks[s][1-component])/2;
                boundary_path[s][edge] = walk(checks[s], end);
            }
        }
    }

    struct Answer { std::uint64_t mask = 0; unsigned cycles = 1; };

    Answer decode(unsigned active) const {
        Answer answer;
        for (int level = 0; active; ++level) {
            if (level > 2) throw std::logic_error("Residual survives the largest distance-five radius");
            unsigned deferred = 0;
            while (active) {
                const int root = std::countr_zero(active);
                unsigned frontier = 1U << root, cluster = frontier;
                int eccentricity = 0;
                // One successive BFS shell corresponds to one extra GROW clock.
                while (frontier) {
                    unsigned adjacent = 0;
                    for (unsigned members = frontier; members; members &= members-1)
                        adjacent |= neighbours[level][std::countr_zero(members)];
                    frontier = adjacent & active & ~cluster;
                    cluster |= frontier;
                    if (frontier) ++eccentricity;
                }
                active &= ~cluster;
                answer.cycles += 3 + eccentricity;  // seed, growth to a fixed point, resolve
                int nearest = side, side_index = -1;
                for (int edge = 0; edge < 2; ++edge) {
                    for (unsigned m = cluster; m; m &= m-1) {
                        const int dist = boundary_distance[std::countr_zero(m)][edge];
                        if (dist < nearest) { nearest = dist; side_index = edge; }
                    }
                }
                const bool reaches_edge = nearest <= (1 << level);
                if (!reaches_edge && std::popcount(cluster)%2 == 1) {
                    deferred |= cluster;
                } else {
                    for (unsigned m = cluster; m; m &= m-1) {
                        int member = std::countr_zero(m);
                        answer.mask ^= reaches_edge ? boundary_path[member][side_index] : pair_path[member][root];
                    }
                }
            }
            active = deferred;
            if (active) ++answer.cycles;  // load deferred clusters at the next radius
        }
        return answer;
    }
};

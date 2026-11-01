"""Check or regenerate the three fixed-weight spatial convolution kernels."""
from pathlib import Path
import argparse
import hashlib


SPECS = ((4, 9, 1, 16, 11), (9, 9, 11, 24, 21), (9, 2, 21, 36, 1))


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def kernel(model_dir, index):
    """Expand coefficients into shared products and registered reduction trees.

    Each input window is ordered by row, column, then input channel. Products
    with the same input and coefficient are shared across output channels.
    Registers in the reduction tree are retained even for zero coefficients,
    so all channels have the same pipeline depth.
    """
    ci, co, input_bits, acc_bits, output_bits = SPECS[index]
    words = (model_dir / f"L{index}_weights_par.hex").read_text().split()
    bias_words = (model_dir / f"L{index}_bias.hex").read_text().split()
    if len(words) != 121 or len(bias_words) != co:
        raise ValueError(f"Layer {index + 1}: unexpected coefficient dimensions")
    weights = []
    for word in words:
        value = int(word, 16)
        if value.bit_length() > ci * co * 8:
            raise ValueError("Weight word exceeds its packed width")
        for channel in range(ci):
            weights.append([signed((value >> (8 * (out * ci + channel))) & 255, 8)
                            for out in range(co)])
    biases = []
    for word in bias_words:
        value = int(word, 16)
        if value.bit_length() > 48:
            raise ValueError("Bias word exceeds 48 bits")
        biases.append(signed(value, 48))
    if any(not -(1 << (acc_bits - 1)) <= value < (1 << (acc_bits - 1))
           for value in biases):
        raise ValueError("Bias does not fit the kernel accumulator")

    terms = len(weights)
    levels = (terms - 1).bit_length()
    product_bits = 8 if index == 0 else input_bits + 8
    name = f"hardwired_L{index + 1}"
    lines = ["`timescale 1ns/1ps",
             f'(* use_dsp="no" *) module {name}(input wire clk,input wire [{terms * input_bits - 1}:0] x,output reg [{co * acc_bits - 1}:0] total,output reg [{co * output_bits - 1}:0] y);']
    for term in range(terms):
        lines.append(f"reg signed [{input_bits - 1}:0] a{term}; always @(posedge clk) a{term} <= x[{term * input_bits} +: {input_bits}];")

    products = {}
    for out in range(co):
        for term in range(terms):
            weight = weights[term][out]
            key = (term, weight)
            if weight == 0 or key in products:
                continue
            product = f"p{term}_{'m' if weight < 0 else 'p'}{abs(weight)}"
            products[key] = product
            lines.append(f"reg signed [{product_bits - 1}:0] {product};")
            # The first layer takes unsigned binary values. Testing bit zero
            # avoids interpreting the one-bit register as signed minus one.
            expression = (f"a{term}[0] ? 8'sh{weight & 255:02x} : 8'sd0"
                          if index == 0 else
                          f"$signed(a{term}) * 8'sh{weight & 255:02x}")
            lines.append(f"always @(posedge clk) {product} <= {expression};")

    for out in range(co):
        nodes = [products.get((term, weights[term][out]), f"{acc_bits}'sd0")
                 for term in range(terms)]
        for level in range(levels):
            next_nodes = []
            for term in range(0, len(nodes), 2):
                node = f"s{out}_{level}_{term // 2}"
                right = nodes[term + 1] if term + 1 < len(nodes) else f"{acc_bits}'sd0"
                lines.append(f"reg signed [{acc_bits - 1}:0] {node}; always @(posedge clk) {node} <= $signed({nodes[term]}) + $signed({right});")
                next_nodes.append(node)
            nodes = next_nodes
        bias = biases[out] & ((1 << acc_bits) - 1)
        lines.append(f"wire signed [{acc_bits - 1}:0] b{out} = {nodes[0]} + {acc_bits}'sh{bias:x};")
        lines.append(f"always @(posedge clk) total[{out * acc_bits} +: {acc_bits}] <= b{out};")
        if index < 2:
            lines.append(f"always @(posedge clk) y[{out * output_bits} +: {output_bits}] <= b{out}[{acc_bits - 1}] ? {output_bits}'d0 : b{out}[{output_bits - 1}:0];")
        else:
            lines.append(f"always @(posedge clk) y[{out}] <= ($signed(b{out}) > 0);")
    return "\n".join(lines + ["endmodule", ""])


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=here.parents[1] / "models/2d")
    parser.add_argument("--output", type=Path,
                        help="Write to a new directory instead of checking the committed RTL")
    args = parser.parse_args()
    generated = {f"hardwired_L{i + 1}.v": kernel(args.models, i) for i in range(3)}
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
    for name, content in generated.items():
        if args.output:
            (args.output / name).write_text(content, encoding="ascii", newline="\n")
        elif (here / name).read_text(encoding="ascii") != content:
            raise SystemExit(f"Mismatch: {name}")
        digest = hashlib.sha256(content.encode("ascii")).hexdigest()
        print(f"{name}: {len(content.splitlines())} lines, SHA256 {digest}")
    print("Generated kernels written." if args.output else "All generated kernels match the model coefficients.")


if __name__ == "__main__":
    main()

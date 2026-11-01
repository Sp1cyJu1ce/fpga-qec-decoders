"""Repack the retained T32 middle-layer weights without changing any coefficient."""

import argparse
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "models" / "3d" / "t32"


def repack(words, lanes):
    # Each T32 group has 27 taps and 16 input-channel groups. A wider word
    # joins consecutive output-channel groups at the same tap/input position.
    source_width = 32 * 8 * 4
    if len(words) != 4 * 27 * 16:
        raise ValueError("Expected 1,728 T32 weight words")
    if any(value < 0 or value.bit_length() > source_width for value in words):
        raise ValueError("A T32 weight word exceeds 1,024 bits")
    groups_per_word = lanes // 32
    result = []
    for output_group in range(128 // lanes):
        for tap_input in range(27 * 16):
            value = 0
            for group in range(groups_per_word):
                source_group = output_group * groups_per_word + group
                value |= words[source_group * 432 + tap_input] << (group * source_width)
            result.append(value)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lanes", type=int, choices=(64, 128))
    parser.add_argument("output", type=Path, help="New coefficient directory")
    args = parser.parse_args()
    files = sorted(SOURCE.glob("L*.hex"))
    if len(files) != 12:
        raise ValueError("The retained T32 coefficient set is incomplete")
    args.output.mkdir(parents=True, exist_ok=False)
    for source in files:
        target = args.output / source.name
        if source.name in ("L2_weights.hex", "L3_weights.hex"):
            words = [int(word, 16) for word in source.read_text().split()]
            packed = repack(words, args.lanes)
            digits = args.lanes * 8  # Eight 4-bit input lanes per output lane.
            target.write_text("".join(f"{word:0{digits}x}\n" for word in packed),
                              encoding="ascii", newline="\n")
        else:
            shutil.copyfile(source, target)
    print(f"Repacked T32 to T{args.lanes}: {args.output.resolve()}")


if __name__ == "__main__":
    main()

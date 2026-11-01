"""Send a known input to an already programmed Arty and check every returned bit.

The board's counter measures only inference. Host round-trip time also includes
serial transfer, so the two measurements are reported separately.
"""
from pathlib import Path
import argparse
import json
import math
import statistics
import time

ROOT = Path(__file__).resolve().parents[1]


def frame_shape(model, distance):
    if distance < 3 or (model == '3d' and distance % 2 != 1):
        raise ValueError('distance must be at least three and must be odd for the 3D model')
    if model == '2d':
        side = 2 * distance - 1
        return (side + 10) ** 2, side ** 2, 4, 2
    if model == '3d':
        # The packaged UART top uses R=d, hence d+1 detector planes.
        voxels = (distance + 1) * ((distance + 3) // 2) ** 2
        return voxels, voxels, 12, 12
    raise ValueError('model must be 2d or 3d')


def read_words(path, count, width):
    """Read one hexadecimal word per line, allowing blank lines and // comments."""
    words = []
    for line_number, line in enumerate(Path(path).read_text(encoding='utf8').splitlines(), 1):
        token = line.split('//', 1)[0].strip()
        if not token:
            continue
        try:
            word = int(token, 16)
        except ValueError as error:
            raise ValueError(f'Invalid hexadecimal word at line {line_number}') from error
        if not 0 <= word < (1 << width):
            raise ValueError(f'Word at line {line_number} does not fit {width} bits')
        words.append(word)
    if len(words) != count:
        raise ValueError(f'Expected {count} words, found {len(words)}')
    return words


def pack_words(words, width):
    """Pack channel/word zero first, starting at the least-significant bit."""
    packed = bytearray()
    buffer = bits = 0
    for word in words:
        if not 0 <= word < (1 << width):
            raise ValueError(f'Word does not fit {width} bits')
        buffer |= word << bits
        bits += width
        while bits >= 8:
            packed.append(buffer & 255)
            buffer >>= 8
            bits -= 8
    if bits:
        packed.append(buffer)
    return bytes(packed)


def exact_read(port, count):
    data = bytearray()
    while len(data) < count:
        block = port.read(count - len(data))
        if not block:
            raise TimeoutError(f'Serial reply stopped after {len(data)} of {count} bytes')
        data.extend(block)
    return bytes(data)


def transaction(port, payload, expected, decision_bits, expected_cycles=None):
    request = b'S' + payload + b'G'
    started = time.perf_counter()
    if port.write(request) != len(request):
        raise OSError('Serial write did not send the complete request')
    port.flush()
    if exact_read(port, 1) != b'\xa5':
        raise ValueError('Unexpected reply header; check the programmed design and distance')
    cycles = int.from_bytes(exact_read(port, 4), 'little')
    decisions = exact_read(port, len(expected))
    if exact_read(port, 1) != b'\x5a':
        raise ValueError('Unexpected reply trailer')
    roundtrip_ms = (time.perf_counter() - started) * 1000
    mismatch = 0
    for index, (actual, wanted) in enumerate(zip(decisions, expected)):
        remaining = decision_bits - 8 * index
        mask = (1 << min(8, remaining)) - 1
        mismatch += ((actual ^ wanted) & mask).bit_count()
    padding = (8 - decision_bits % 8) % 8
    if padding and decisions[-1] >> (8 - padding):
        raise ValueError('Nonzero padding in the last reply byte')
    if mismatch:
        raise ValueError(f'{mismatch} of {decision_bits} decisions differ from the reference')
    if expected_cycles is not None and cycles != expected_cycles:
        raise ValueError(f'Core returned {cycles} clocks; expected {expected_cycles}')
    return {'cycles': cycles, 'decisions_checked': decision_bits, 'host_roundtrip_ms': roundtrip_ms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', choices=['2d', '3d'])
    parser.add_argument('--port', required=True, help='Serial port of the programmed board')
    parser.add_argument('--distance', type=int, default=5)
    parser.add_argument('--input', type=Path, help='Input hex file; defaults to the packaged d5 example')
    parser.add_argument('--expected', type=Path, help='Decision hex file; defaults to the packaged d5 example')
    parser.add_argument('--expected-cycles', type=int)
    parser.add_argument('--clock-mhz', type=float, help='Qualified nominal clock of the programmed core')
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--output', type=Path, help='Optional new JSON result file')
    args = parser.parse_args()
    try:
        input_count, output_count, input_width, output_width = frame_shape(args.model, args.distance)
    except ValueError as error:
        parser.error(str(error))
    if args.repeats < 1 or args.baud < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('repeats, baud and timeout must be positive')
    if args.clock_mhz is not None and (not math.isfinite(args.clock_mhz) or args.clock_mhz <= 0):
        parser.error('clock-mhz must be positive and finite')
    if args.expected_cycles is not None and args.expected_cycles < 0:
        parser.error('expected-cycles must be nonnegative')
    if args.output is not None and args.output.exists():
        parser.error('output already exists; choose a new result filename')
    if args.distance != 5 and (args.input is None or args.expected is None):
        parser.error('other distances require both --input and --expected')

    example = ROOT / f'examples/arty_{args.model}_d5'
    input_path = args.input or example / 'input.hex'
    expected_path = args.expected or example / ('decisions.hex' if args.model == '2d' else 'layer4_activation.hex')
    if max(input_count, output_count) > 65535:
        parser.error('this input exceeds the packaged UART harness address counters')
    inputs = read_words(input_path, input_count, input_width)
    outputs = read_words(expected_path, output_count, output_width)
    payload = pack_words(inputs, 4) if args.model == '2d' else b''.join(word.to_bytes(2, 'little') for word in inputs)
    expected = pack_words(outputs, output_width)

    try:
        import serial
    except ImportError:
        parser.error('install pyserial to use the board interface')
    results = []
    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=args.timeout) as port:
        # Opening the port does not reset the FPGA. Start from an idle, programmed
        # harness and discard stale host bytes once, before the first transaction.
        port.reset_input_buffer()
        for index in range(args.repeats):
            row = transaction(port, payload, expected, output_count * output_width, args.expected_cycles)
            results.append(row)
            print(f"{index + 1}/{args.repeats}: all {row['decisions_checked']} decisions match, {row['cycles']} clocks")
    cycles = sorted({row['cycles'] for row in results})
    if len(cycles) != 1:
        raise ValueError(f'The repeated deterministic input returned different cycle counts: {cycles}')
    summary = {'status': 'PASS', 'model': args.model, 'distance': args.distance,
               'transactions': args.repeats, 'decision_bits_per_transaction': output_count * output_width,
               'core_cycles': cycles[0], 'host_roundtrip_ms_median': statistics.median(row['host_roundtrip_ms'] for row in results),
               'scope': 'One supplied input repeated through the physical UART interface; not a logical-error-rate experiment.',
               'measurements': results}
    if args.clock_mhz is not None:
        summary.update(nominal_clock_mhz=args.clock_mhz, core_time_us=cycles[0] / args.clock_mhz)
    if args.output is not None:
        with args.output.open('x', encoding='utf8') as handle:
            json.dump(summary, handle, indent=2)
            handle.write('\n')
    print(json.dumps({key: value for key, value in summary.items() if key != 'measurements'}, indent=2))


if __name__ == '__main__':
    main()

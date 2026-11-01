"""Offline checks of byte packing and rejected malformed UART replies."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('board_check', ROOT / 'scripts/check_board.py')
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)


class FakePort:
    def __init__(self, reply, partial_write=False):
        self.reply = bytearray(reply)
        self.sent = b''
        self.partial_write = partial_write

    def write(self, data):
        self.sent += data
        return len(data) - int(self.partial_write)

    def flush(self):
        pass

    def read(self, count):
        # Serial reads may be shorter than requested, even in a valid reply.
        count = min(count, 2, len(self.reply))
        data = bytes(self.reply[:count])
        del self.reply[:count]
        return data


class BoardProtocolTests(unittest.TestCase):
    def test_distance_rules(self):
        self.assertEqual(board.frame_shape('2d', 30), (4761, 3481, 4, 2))
        self.assertEqual(board.frame_shape('3d', 5), (96, 96, 12, 12))
        with self.assertRaises(ValueError):
            board.frame_shape('3d', 4)
        with self.assertRaises(ValueError):
            board.frame_shape('2d', 2)

    def test_wire_order(self):
        self.assertEqual(board.pack_words([1, 2, 3], 4), b'\x21\x03')
        self.assertEqual(board.pack_words([0xabc, 0x123], 12), b'\xbc\x3a\x12')
        self.assertEqual(board.pack_words([0, 1, 2, 3, 2], 2), b'\xe4\x02')

    def test_partial_reads_and_counter(self):
        port = FakePort(b'\xa5' + (4377).to_bytes(4, 'little') + b'\xe4\x02\x5a')
        result = board.transaction(port, b'\x21\x03', b'\xe4\x02', 10, 4377)
        self.assertEqual(port.sent, b'S\x21\x03G')
        self.assertEqual(result['cycles'], 4377)
        self.assertEqual(result['decisions_checked'], 10)

    def test_malformed_replies(self):
        replies = [b'', b'\x00', b'\xa5\x01', b'\xa5\x01\x00\x00\x00\x00\x00',
                   b'\xa5\x01\x00\x00\x00\x01\x5a', b'\xa5\x01\x00\x00\x00\x80\x5a']
        for reply in replies:
            with self.subTest(reply=reply), self.assertRaises((ValueError, TimeoutError)):
                board.transaction(FakePort(reply), b'\x00', b'\x00', 2, 1)

    def test_rejects_wrong_cycle_count_and_short_write(self):
        reply = b'\xa5\x01\x00\x00\x00\x00\x5a'
        with self.assertRaises(ValueError):
            board.transaction(FakePort(reply), b'\x00', b'\x00', 2, 2)
        with self.assertRaises(OSError):
            board.transaction(FakePort(reply, partial_write=True), b'\x00', b'\x00', 2)

    def test_input_file_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.hex'
            path.write_text('A // first word\n\n0\n', encoding='utf8')
            self.assertEqual(board.read_words(path, 2, 4), [10, 0])
            with self.assertRaises(ValueError):
                board.read_words(path, 3, 4)
            with self.assertRaises(ValueError):
                board.read_words(path, 2, 2)

    def test_packaged_shapes(self):
        two = ROOT / 'examples/arty_2d_d5'
        three = ROOT / 'examples/arty_3d_d5'
        self.assertEqual(len(board.pack_words(board.read_words(two / 'input.hex', 361, 4), 4)), 181)
        self.assertEqual(len(board.pack_words(board.read_words(two / 'decisions.hex', 81, 2), 2)), 21)
        self.assertEqual(len(board.read_words(three / 'input.hex', 96, 12)) * 2, 192)
        self.assertEqual(len(board.pack_words(board.read_words(three / 'layer4_activation.hex', 96, 12), 12)), 144)


if __name__ == '__main__':
    unittest.main()

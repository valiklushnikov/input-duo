import unittest
from pathlib import Path


class ProtocolCopyTest(unittest.TestCase):
    def test_all_protocol_headers_are_byte_identical(self):
        root = Path(__file__).resolve().parent.parent
        headers = [
            root / "improved" / "mouse_switch_master" / "mouse_switch_protocol.h",
            root / "improved" / "mouse_switch_receiver" / "mouse_switch_protocol.h",
            root / "tests" / "protocol_compile_test" / "mouse_switch_protocol.h",
        ]
        contents = [header.read_bytes() for header in headers]
        self.assertEqual(contents[0], contents[1])
        self.assertEqual(contents[0], contents[2])


if __name__ == "__main__":
    unittest.main()

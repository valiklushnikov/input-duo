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

    def test_side_button_finder_headers_match_master(self):
        root = Path(__file__).resolve().parent.parent
        master = root / "improved" / "mouse_switch_master"
        finder = root / "diagnostics" / "side_button_finder"
        self.assertEqual(
            (master / "mouse_switch_protocol.h").read_bytes(),
            (finder / "mouse_switch_protocol.h").read_bytes(),
        )
        self.assertEqual(
            (master / "mouse_switch_ps2_logic.h").read_bytes(),
            (finder / "mouse_switch_ps2_logic.h").read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()

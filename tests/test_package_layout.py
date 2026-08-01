from pathlib import Path
import unittest


class PackageLayoutTests(unittest.TestCase):
    def test_required_sketches_are_in_matching_arduino_folders(self):
        root = Path(__file__).resolve().parents[1]
        required_sketches = (
            "legacy/master_original/master_original.ino",
            "legacy/receiver_original/receiver_original.ino",
            "improved/mouse_switch_master/mouse_switch_master.ino",
            "improved/mouse_switch_receiver/mouse_switch_receiver.ino",
        )

        for relative_path in required_sketches:
            with self.subTest(sketch=relative_path):
                sketch = root / relative_path
                self.assertTrue(sketch.is_file(), f"missing sketch: {relative_path}")
                self.assertEqual(sketch.parent.name, sketch.stem)


if __name__ == "__main__":
    unittest.main()

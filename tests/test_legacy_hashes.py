import hashlib
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class LegacyHashTests(unittest.TestCase):
    def assert_sha256(self, relative_path: str, expected_hash: str) -> None:
        archive = REPOSITORY_ROOT / relative_path
        self.assertTrue(archive.is_file(), f"Missing archival sketch: {archive}")
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest().upper(), expected_hash)

    def test_master_original_matches_recovered_firmware(self) -> None:
        self.assert_sha256(
            "legacy/master_original/master_original.ino",
            "5CA98439289B9A39A76EF7386333D013898548ADE97329AC723F5578562E98F3",
        )

    def test_receiver_original_matches_recovered_firmware(self) -> None:
        self.assert_sha256(
            "legacy/receiver_original/receiver_original.ino",
            "459B63339A2FE01F2D136C811D628D8CB474ED592B58B268AA1D4E4E4F507D39",
        )


if __name__ == "__main__":
    unittest.main()

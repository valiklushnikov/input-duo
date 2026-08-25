import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "tests" / "fuzz" / "generate_corpus.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("duo_fuzz_corpus_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FuzzCorpusTests(unittest.TestCase):
    def test_valid_header_config_crc_seed_changes_only_crc_field(self):
        generator = load_generator()
        valid = (ROOT / "tests/vectors/config_vectors/valid_minimal.bin").read_bytes()
        corrupted = generator.expected_corpus()["config"]["crc-corrupt-valid-header.bin"]

        self.assertEqual(corrupted[:12], valid[:12])
        self.assertNotEqual(corrupted[12:16], valid[12:16])
        self.assertEqual(corrupted[16:], valid[16:])

    def test_portable_smokes_are_excluded_from_fuzzer_configuration(self):
        cmake = (ROOT / "tests/fuzz/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("if(BUILD_TESTING AND DUO_NATIVE_TESTS AND NOT DUO_FUZZ_TESTS)", cmake)


if __name__ == "__main__":
    unittest.main()

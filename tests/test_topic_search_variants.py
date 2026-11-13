import pathlib
import sys
import unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from topic_discovery import _search_variants

class TopicSearchVariantTests(unittest.TestCase):
    def test_broad_and_focused_queries_are_distinct(self):
        variants = _search_variants("heart failure", "machine learning")
        self.assertIn(" OR ", variants["broad"])
        self.assertIn("AND", variants["focused"])
        self.assertIn("[tiab]", variants["focused"])
        self.assertNotEqual(variants["broad"], variants["focused"])

if __name__ == "__main__":
    unittest.main()

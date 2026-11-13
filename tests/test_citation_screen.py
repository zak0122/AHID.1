import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from citation_screen import deduplicate, prepare_screening_rows, write_screening_csv


class CitationScreenTests(unittest.TestCase):
    def test_deduplicates_by_pmid_doi_and_title(self):
        records = [
            {"pmid": "123", "doi": "10.1/abc", "title": "A Study"},
            {"pmid": "123", "doi": "10.1/other", "title": "Different title"},
            {"pmid": "999", "doi": "10.1/abc", "title": "Other"},
            {"pmid": "777", "title": "A Study!"},
        ]
        kept, duplicates = deduplicate(records)
        self.assertEqual(len(kept), 1)
        self.assertEqual(duplicates, 3)

    def test_uncertain_primary_records_are_not_called_eligible(self):
        rows, counts = prepare_screening_rows([
            {"pmid": "1", "title": "A cohort study of treatment outcomes",
             "design": "Retrospective cohort", "abstract": "Abstract"}
        ])
        self.assertEqual(counts["unclear_human_screen"], 1)
        self.assertEqual(rows[0]["triage_label"], "unclear_human_screen")
        self.assertIn("human title/abstract screening", rows[0]["triage_reason"])

    def test_review_is_flagged_as_secondary_not_primary(self):
        rows, counts = prepare_screening_rows([
            {"pmid": "2", "title": "Systematic review and meta-analysis of therapy"}
        ])
        self.assertEqual(counts["exclude_from_primary_pool"], 1)
        self.assertEqual(rows[0]["triage_label"], "exclude_from_primary_pool")

    def test_writes_csv(self):
        rows, _ = prepare_screening_rows([{"pmid": "3", "title": "Primary study"}])
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "screen.csv"
            write_screening_csv(rows, path)
            self.assertTrue(path.exists())
            self.assertIn("triage_label", path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()

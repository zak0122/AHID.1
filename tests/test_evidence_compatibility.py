import csv
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evidence_compatibility import compatibility_rows, write_compatibility_csv


class CompatibilityWorksheetTests(unittest.TestCase):
    def test_does_not_infer_outcome_compatibility(self):
        rows = compatibility_rows([{
            "pmid": "123", "doi": "10.x/test", "title": "Example",
            "design": "Prospective cohort", "abstract": "Abstract text",
        }])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pmid"], "123")
        self.assertEqual(rows[0]["study_design"], "Prospective cohort")
        self.assertEqual(rows[0]["outcome_name"], "")
        self.assertEqual(rows[0]["compatibility_decision"], "")

    def test_writes_extraction_template(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_compatibility_csv([{"pmid": "1", "title": "Study"}],
                                          pathlib.Path(temp) / "worksheet.csv")
            with path.open(encoding="utf-8-sig") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["pmid"], "1")
            self.assertIn("timepoint_or_followup", row)
            self.assertEqual(row["pooling_notes"], "")


if __name__ == "__main__":
    unittest.main()

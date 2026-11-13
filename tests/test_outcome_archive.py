import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from outcome_archive import save_daily_outcome  # noqa: E402


class DailyOutcomeArchiveTests(unittest.TestCase):
    def test_saves_dated_report_metadata_and_year_index(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"GITHUB_RUN_NUMBER": ""}):
            day = dt.date(2026, 11, 13)
            path = save_daily_outcome("# Daily report\n", root=pathlib.Path(temp), run_date=day)
            self.assertEqual(path.relative_to(pathlib.Path(temp)).as_posix(),
                             "outcomes/2026/11/13/report.md")
            metadata = json.loads(path.with_name("metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["run_date"], "2026-11-13")
            self.assertEqual(len(metadata["report_sha256"]), 64)
            index = pathlib.Path(temp) / "outcomes" / "2026" / "README.md"
            self.assertIn("2026-11-13", index.read_text(encoding="utf-8"))

    def test_second_run_does_not_overwrite_first(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"GITHUB_RUN_NUMBER": ""}):
            root = pathlib.Path(temp)
            day = dt.date(2026, 11, 13)
            first = save_daily_outcome("first", root=root, run_date=day)
            second = save_daily_outcome("second", root=root, run_date=day)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(encoding="utf-8"), "first")
            self.assertEqual(second.read_text(encoding="utf-8"), "second")


if __name__ == "__main__":
    unittest.main()

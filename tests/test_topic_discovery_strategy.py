"""Regression tests for novelty-first observational topic discovery."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from topic_discovery import (
    OBSERVATIONAL_ARCHETYPES,
    OBSERVATIONAL_QUERY,
    _observational_archetype_queries,
    _priority,
    _score,
)


class ObservationalArchetypeTests(unittest.TestCase):
    def test_three_distinct_observational_question_tracks_exist(self):
        self.assertEqual(
            set(OBSERVATIONAL_ARCHETYPES),
            {"prognosis", "incidence_prevalence", "predictors_risk_factors"},
        )

    def test_each_track_keeps_observational_design_filter(self):
        topic = '("Heart Failure"[Mesh] OR "Heart Failure"[tiab])'
        queries = _observational_archetype_queries(topic)
        self.assertEqual(set(queries), set(OBSERVATIONAL_ARCHETYPES))
        for name, query in queries.items():
            with self.subTest(track=name):
                self.assertIn(topic, query)
                self.assertIn(OBSERVATIONAL_QUERY.strip(), query)
                self.assertNotIn("Randomized Controlled Trial", query.split("AND")[-1])

    def test_broad_review_hits_do_not_automatically_reduce_score(self):
        self.assertEqual(_score(25, 0, 4), _score(25, 1000, 4))

    def test_review_hits_do_not_automatically_block_priority_signal(self):
        self.assertIn("EVIDENCE SIGNAL", _priority(20, 1000, False))


if __name__ == "__main__":
    unittest.main()

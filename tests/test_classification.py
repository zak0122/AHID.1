import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from monitor import (  # noqa: E402
    DESIGN_ORDER,
    REVIEW_DESIGN,
    PROTOCOL_DESIGN,
    _classify_study_design,
)
from pico_extract import draft_pico  # noqa: E402


class StudyDesignClassificationTests(unittest.TestCase):
    def classify(self, title, abstract="", pub_types=None, mesh=None):
        return _classify_study_design(title, abstract, pub_types or [], mesh or [])

    def test_publication_type_rct(self):
        self.assertEqual(
            self.classify("Drug X in heart failure", pub_types=["Randomized Controlled Trial"]),
            DESIGN_ORDER[0],
        )

    def test_unindexed_rct_text_fallback(self):
        self.assertEqual(
            self.classify(
                "Drug X versus placebo in heart failure",
                "Methods: Participants were randomly assigned to treatment or placebo.",
            ),
            DESIGN_ORDER[0],
        )

    def test_nonrandomized_is_not_misread_as_rct(self):
        self.assertEqual(
            self.classify(
                "Non-randomized trial of treatment X",
                "A non-randomized controlled trial was performed.",
            ),
            DESIGN_ORDER[1],
        )

    def test_generic_clinical_trial_is_unclassified_for_randomization(self):
        self.assertEqual(
            self.classify(
                "Treatment X in chronic disease",
                pub_types=["Clinical Trial"],
            ),
            DESIGN_ORDER[2],
        )

    def test_prospective_cohort(self):
        self.assertEqual(
            self.classify(
                "Antibiotic exposure in infancy",
                "We conducted a prospective cohort study of 500 children.",
            ),
            DESIGN_ORDER[3],
        )

    def test_retrospective_cohort(self):
        self.assertEqual(
            self.classify(
                "Outcomes after surgery",
                "A retrospective cohort study of hospital records.",
            ),
            DESIGN_ORDER[4],
        )

    def test_case_control(self):
        self.assertEqual(
            self.classify("Risk factors for disease", "A matched case-control study."),
            DESIGN_ORDER[6],
        )

    def test_cross_sectional(self):
        self.assertEqual(
            self.classify("Prevalence of disease", "A cross-sectional survey was conducted."),
            DESIGN_ORDER[7],
        )

    def test_systematic_review_separate(self):
        self.assertEqual(
            self.classify("Systematic review and meta-analysis of treatment X"),
            REVIEW_DESIGN,
        )

    def test_protocol_separate(self):
        self.assertEqual(
            self.classify("Protocol for a prospective cohort study"),
            PROTOCOL_DESIGN,
        )

    def test_uncertain_not_guessed_as_rct(self):
        self.assertEqual(
            self.classify("Clinical outcomes in patients with condition X", "We analysed records."),
            DESIGN_ORDER[9],
        )


class LocalPicoTests(unittest.TestCase):
    def test_pico_runs_without_external_provider_keys(self):
        result = draft_pico(
            "Randomized trial of Drug X versus placebo in adults with heart failure",
            "Methods: Adults with heart failure were randomized to Drug X or placebo. "
            "The primary outcome was all-cause mortality.",
            "Randomized controlled trial (RCT)",
        )
        self.assertEqual(result["method"], "rule_based_no_external_ai")
        self.assertEqual(result["intervention"], "Drug X")
        self.assertEqual(result["comparator"], "placebo")
        self.assertIn("mortality", result["outcome"].lower())
        self.assertIn("[tiab]", result["narrow_pool_search"])

    def test_unclear_fields_are_not_fabricated(self):
        result = draft_pico("A study of disease X", "")
        self.assertIn("unclear", result["population"])
        self.assertIn("unclear", result["outcome"])


if __name__ == "__main__":
    unittest.main()

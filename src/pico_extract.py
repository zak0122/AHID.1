"""
pico_extract.py
---------------
API-key-free, conservative PICO/PECO draft extraction.

This module intentionally does not pretend that keyword heuristics are an
LLM or a full-text screening process. It extracts only explicit signals
from titles/abstracts, labels uncertainty, and builds reproducible
title/abstract PubMed search blocks. No Gemini/Anthropic key is required.
"""

import re
from typing import Dict, Optional

STOP_WORDS = {
    "a", "an", "and", "or", "the", "of", "for", "in", "on", "with", "without",
    "among", "versus", "vs", "compared", "comparison", "effect", "effects",
    "efficacy", "effectiveness", "safety", "incidence", "prevalence", "risk",
    "association", "impact", "outcomes", "outcome", "study", "studies",
    "trial", "trials", "analysis", "analyses", "systematic", "review",
    "meta", "randomized", "randomised", "prospective", "retrospective",
    "cohort", "observational", "multicenter", "multicentre", "clinical",
    "patients", "patient", "adults", "adult", "children", "child",
    "using", "use", "based", "after", "during", "following", "versus",
    "new", "updated", "among", "from", "to", "by", "at", "as", "is", "are",
    "was", "were", "be", "been", "being", "into", "than", "that", "this",
}

POPULATION_PATTERNS = [
    r"\b((?:adults?|children|infants?|older adults?|elderly patients?|"
    r"pregnant women|women|men|patients|participants|individuals|people)"
    r"\s+(?:with|who have|undergoing|receiving)\s+[^.;:]{3,100})",
    r"\b(patients?\s+with\s+[^.;:]{3,100})",
]

OUTCOME_PATTERNS = [
    r"\b(?:primary outcome[s]?\s*(?:was|were|:)?\s*)([^.;]{4,120})",
    r"\b((?:all-cause\s+)?mortality|death|hospitali[sz]ation|recurrence|"
    r"relapse|disease progression|major adverse cardiovascular events|"
    r"adverse events|quality of life|treatment response|remission)\b",
]

INTERVENTION_PATTERNS = [
    r"\b(?:effect|efficacy|effectiveness|safety|impact)\s+of\s+([^.;:]{3,100}?)"
    r"\s+(?:on|in|among|for)\s+",
    r"\b(?:treated|treatment|therapy|received|receiving|exposed to)\s+"
    r"([^.;:]{3,90}?)(?:\s+(?:compared with|versus|vs\.?|and)\s+|[,.;])",
]


def _clean(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip(" \t\r\n,;:.()[]")
    return value[:180] if value else ""


def _first_match(patterns, text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _clean(match.group(1))
            if value:
                return value
    return ""


def _search_terms(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}", value or "")
    # Keep clinically meaningful terms; avoid broad generic words.
    kept = []
    for word in words:
        if word.lower() not in STOP_WORDS and word.lower() not in {x.lower() for x in kept}:
            kept.append(word)
    return " AND ".join(f'"{word}"[tiab]' for word in kept[:5])


def draft_pico(title: str, abstract: str, study_design: Optional[str] = None) -> Dict:
    """Return an explicitly heuristic PICO/PECO draft with uncertainty notes."""
    title = _clean(title)
    abstract = (abstract or "").strip()
    text = f"{title}. {abstract}"
    lower = text.lower()

    population = _first_match(POPULATION_PATTERNS, text)
    if population:
        population = re.split(
            r"\b(?:were|are|was|is|have|had|underwent|received|randomized|randomised|assigned|enrolled|included)\b",
            population, maxsplit=1, flags=re.IGNORECASE,
        )[0].strip()
    intervention = _first_match(INTERVENTION_PATTERNS, text)

    # Detect explicit head-to-head comparisons without guessing the arms.
    comparator = ""
    comparison = re.search(
        r"\b(?:trial of|effect of|efficacy of|safety of|treatment with|comparison of)\s+"
        r"([^,;:.]{2,60}?)\s+(?:versus|vs\.?|compared with|compared to)\s+"
        r"([^,;:.]{2,60}?)(?=\s+(?:in|among|for|with)\b|[,.;:]|$)",
        title, flags=re.IGNORECASE,
    )
    if not comparison:
        comparison = re.search(
            r"^([^,;:.]{2,60}?)\s+(?:versus|vs\.?|compared with|compared to)\s+"
            r"([^,;:.]{2,60}?)(?=\s+(?:in|among|for|with)\b|[,.;:]|$)",
            title, flags=re.IGNORECASE,
        )
    if comparison:
        intervention = _clean(comparison.group(1))
        comparator = _clean(comparison.group(2))
    else:
        comparator_match = re.search(
            r"\b(?:versus|vs\.?|compared with|compared to)\s+([^.;,:]{2,80})",
            text, flags=re.IGNORECASE,
        )
        if comparator_match:
            comparator = _clean(comparator_match.group(1))

    outcome = _first_match(OUTCOME_PATTERNS, abstract or title)
    if not outcome:
        outcome = _first_match(OUTCOME_PATTERNS, title)

    if not population:
        # Conservative fallback: a population may be implied in a title,
        # but is not inferred from disease terms alone.
        population = "unclear from title/abstract"
    if not intervention:
        intervention = "exposure/intervention unclear; inspect abstract"
    if not comparator:
        comparator = "not clearly reported in title/abstract"
    if not outcome:
        outcome = "outcome unclear; inspect full abstract"

    design = study_design or "Not classified; inspect PubMed publication types"
    notes = [
        "Automated rule-based draft; not an LLM extraction.",
        "Verify population, exposure/intervention, comparator, outcome and design against the full text.",
    ]
    found = sum([
        population != "unclear from title/abstract",
        intervention != "exposure/intervention unclear; inspect abstract",
        outcome != "outcome unclear; inspect full abstract",
    ])
    confidence = "low" if found <= 1 else ("moderate" if found == 2 else "moderate; verify manually")
    notes.append(f"Extraction signal: {found}/3 core fields explicit ({confidence} confidence).")

    search_blocks = [_search_terms(x) for x in (population, intervention, outcome)
                     if x and "unclear" not in x and "inspect" not in x]
    narrow_search = " AND ".join(f"({block})" for block in search_blocks)
    if not narrow_search:
        narrow_search = _search_terms(title)

    # Broaden using a shorter title/abstract keyword set, never by inventing a
    # MeSH heading. Add MeSH terms only after a human verifies the descriptor.
    broad_terms = _search_terms(title + " " + (abstract[:700] if abstract else ""))
    broad_search = broad_terms or narrow_search

    return {
        "population": population,
        "intervention": intervention,
        "comparator": comparator,
        "outcome": outcome,
        "study_design": design,
        "confidence_notes": " ".join(notes),
        "extraction_confidence": confidence,
        "broadened_population": population,
        "broadened_intervention": intervention,
        "broadened_outcome": outcome,
        "broadening_notes": "No automatic clinical broadening was made; this avoids changing the research question without evidence.",
        "narrow_pool_search": narrow_search,
        "broad_pool_search": broad_search,
        "method": "rule_based_no_external_ai",
    }


if __name__ == "__main__":
    import json
    sample = draft_pico(
        "Randomized trial of Drug X versus placebo in adults with heart failure",
        "Methods: Adults with heart failure were randomized to Drug X or placebo. "
        "The primary outcome was all-cause mortality.",
        "Randomized controlled trial (RCT)",
    )
    print(json.dumps(sample, indent=2))

"""
topic_discovery.py
------------------
A reproducible, API-key-free SRMA topic triage layer.

It starts from a bounded sample of recent primary studies in the configured
journal set, derives transparent title-term candidates, then checks each
candidate against PubMed as a whole using separate RCT and observational
search blocks plus a separate systematic-review/meta-analysis block.

Important: hit counts are screening signals, not eligible-study counts.
A person must screen records, compare outcomes/timepoints, inspect full
texts, check PROSPERO and assess risk of bias before calling a topic feasible.
"""

import math
import os
import re
import time
from collections import Counter, defaultdict
from typing import Dict, List, Tuple
from urllib.parse import quote_plus

import requests

from monitor import (
    NCBI_BASE, NCBI_API_KEY, REQUEST_PAUSE, REQUEST_TIMEOUT,
    DESIGN_ORDER, _api_params, _efetch_abstracts,
)
from pico_extract import draft_pico

RCT_QUERY = """
(
  "Randomized Controlled Trial"[pt] OR "Controlled Clinical Trial"[pt]
  OR randomized[tiab] OR randomised[tiab] OR randomi?ation[tiab]
  OR "randomly assigned"[tiab] OR placebo[tiab]
)
NOT (protocol[ti] OR editorial[pt] OR letter[pt] OR comment[pt])
"""

OBSERVATIONAL_QUERY = """
(
  "Observational Study"[pt] OR "Cohort Studies"[mh]
  OR "Prospective Studies"[mh] OR "Retrospective Studies"[mh]
  OR "Case-Control Studies"[mh] OR "Cross-Sectional Studies"[mh]
  OR cohort[tiab] OR observational[tiab] OR "case-control"[tiab]
  OR "cross-sectional"[tiab] OR registry[tiab]
  OR prospective[tiab] OR retrospective[tiab]
)
NOT (protocol[ti] OR editorial[pt] OR letter[pt] OR comment[pt])
"""

REVIEW_QUERY = """
(
  "Systematic Review"[pt] OR "Meta-Analysis"[pt]
  OR "systematic review"[tiab] OR "meta-analysis"[tiab]
  OR "meta analysis"[tiab]
)
NOT (protocol[ti] OR editorial[pt] OR letter[pt] OR comment[pt])
"""

STOP = {
    "about", "after", "among", "analysis", "association", "associated",
    "based", "between", "clinical", "compared", "comparison", "effect",
    "effects", "efficacy", "effectiveness", "evaluation", "following",
    "from", "impact", "incidence", "management", "outcome", "outcomes",
    "patients", "patient", "people", "persons", "population", "prevalence",
    "prospective", "retrospective", "randomized", "randomised", "review",
    "safety", "study", "studies", "systematic", "trial", "trials",
    "treatment", "versus", "using", "with", "without", "within", "during",
    "first", "years", "year", "adults", "adult", "children", "child",
    "women", "men", "care", "health", "hospital", "hospital-based",
    "multicenter", "multicentre", "international", "national", "global",
    "new", "updated", "experience", "characteristics", "factors",
    "determinants", "patterns", "burden", "risk", "risks", "predictors",
    "patients", "among", "versus", "based", "data", "evidence",
}

MAX_CANDIDATES = int(os.environ.get("MAX_TOPIC_CANDIDATES", "12"))
MIN_SEARCH_TERM_LENGTH = 4


def _tokens(title: str) -> List[str]:
    words = re.findall(r"[a-z][a-z0-9-]{3,}", (title or "").lower())
    output = []
    for word in words:
        word = word.strip("-")
        if len(word) < MIN_SEARCH_TERM_LENGTH or word in STOP:
            continue
        if word not in output:
            output.append(word)
    return output


def _candidate_pairs(records: List[Dict], limit: int) -> List[Dict]:
    """Choose medically specific pairs; avoid common generic title language."""
    token_docs = [_tokens(record.get("title", "")) for record in records]
    df = Counter(token for tokens in token_docs for token in set(tokens))
    n_docs = max(len(records), 1)
    grouped = defaultdict(list)

    for record, tokens in zip(records, token_docs):
        if len(tokens) < 2:
            continue
        pairs = list(zip(tokens, tokens[1:]))
        if not pairs:
            continue
        # Prefer informative but not ultra-rare terms. IDF helps prevent
        # generic words from dominating; preserve original word order.
        def pair_score(pair):
            a, b = pair
            idf = math.log((n_docs + 1) / (df[a] + 1)) + math.log((n_docs + 1) / (df[b] + 1))
            commonness_penalty = 0.0 if df[a] > 1 and df[b] > 1 else 0.35
            return idf - commonness_penalty
        pair = max(pairs, key=pair_score)
        grouped[pair].append(record)

    candidates = []
    for pair, seeds in grouped.items():
        candidates.append({
            "terms": pair,
            "seed_records": seeds,
            "seed_count": len(seeds),
            "specificity": round(
                math.log((n_docs + 1) / (df[pair[0]] + 1))
                + math.log((n_docs + 1) / (df[pair[1]] + 1)), 2
            ),
        })
    candidates.sort(
        key=lambda c: (c["seed_count"], c["specificity"],
                       max((r.get("pubdate", "") for r in c["seed_records"]), default="")),
        reverse=True,
    )
    return candidates[:limit]


def _pubmed_search(query: str, retmax: int = 30) -> Tuple[int, List[str]]:
    params = {
        "db": "pubmed", "term": query, "retmax": str(retmax),
        "retmode": "json", "sort": "relevance",
    }
    response = requests.get(
        f"{NCBI_BASE}/esearch.fcgi", params=_api_params(params),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json().get("esearchresult", {})
    time.sleep(REQUEST_PAUSE)
    return int(payload.get("count", 0)), payload.get("idlist", [])


def _sample_size(abstract: str) -> str:
    if not abstract:
        return "not reported in abstract"
    patterns = [
        r"\b(?:total of|included|enrolled|recruited|randomized|randomised)\s+"
        r"([\d,]+)\s+(?:patients|participants|participants|subjects|individuals)\b",
        r"\bN\s*=\s*([\d,]+)\b",
        r"\b([\d,]+)\s+(?:patients|participants|subjects)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, abstract, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return "not reported in abstract"


def _score(evidence_count: int, review_count: int, seed_count: int,
           is_rct: bool, has_both_designs: bool) -> int:
    """Transparent heuristic score (not a validated prediction model)."""
    evidence = min(50.0, 13.0 * math.log2(max(evidence_count, 0) + 1))
    novelty = max(0.0, 25.0 - 8.0 * min(review_count, 3))
    seed_signal = min(10.0, 3.0 + 3.0 * math.log2(max(seed_count, 1)))
    design_balance = 5.0 if has_both_designs else 0.0
    # Small penalty when the requested design stream has no evidence.
    if evidence_count == 0:
        evidence = 0.0
    return int(round(min(100.0, evidence + novelty + seed_signal + design_balance)))


def _priority(count: int, reviews: int, is_rct: bool) -> str:
    minimum = 3 if is_rct else 5
    if count >= minimum and reviews <= 1:
        return "TOP PRIORITY — manual screening required"
    if count >= minimum and reviews > 1:
        return "MODIFY / CHECK OVERLAP — existing reviews detected"
    if count >= 2:
        return "PROMISING SIGNAL — verify eligible studies"
    return "EARLY SIGNAL — insufficient evidence count so far"


def _assess_candidate(candidate: Dict) -> Dict:
    term_a, term_b = candidate["terms"]
    topic_query = f'"{term_a}"[tiab] AND "{term_b}"[tiab]'
    rct_count, rct_ids = _pubmed_search(f"({topic_query}) AND ({RCT_QUERY})")
    obs_count, obs_ids = _pubmed_search(
        f"({topic_query}) AND ({OBSERVATIONAL_QUERY})"
    )
    review_count, review_ids = _pubmed_search(
        f"({topic_query}) AND ({REVIEW_QUERY})"
    )

    all_ids = list(dict.fromkeys(rct_ids + obs_ids))[:40]
    details = _efetch_abstracts(all_ids)
    rct_refs = []
    obs_refs = []
    for pmid in rct_ids:
        item = details.get(pmid)
        if item:
            rct_refs.append(item)
    for pmid in obs_ids:
        item = details.get(pmid)
        if item:
            obs_refs.append(item)

    seeds = candidate["seed_records"]
    seed = seeds[0]
    pico = draft_pico(seed.get("title", ""), seed.get("abstract", ""),
                      seed.get("study_design", ""))
    total = rct_count + obs_count
    has_both = rct_count > 0 and obs_count > 0
    phrase = f"{term_a} {term_b}"
    return {
        "topic": phrase,
        "provisional_title": (
            f"{phrase.title()}: a systematic review and meta-analysis of "
            "randomized and observational evidence"
        ),
        "seed_count": candidate["seed_count"],
        "seed_titles": [x.get("title", "") for x in seeds[:5]],
        "seed_pmids": [x.get("pmid", "") for x in seeds[:5]],
        "rct_count": rct_count,
        "observational_count": obs_count,
        "total_primary_hits": total,
        "review_count": review_count,
        "rct_score": _score(rct_count, review_count, candidate["seed_count"], True, has_both),
        "observational_score": _score(obs_count, review_count, candidate["seed_count"], False, has_both),
        "rct_priority": _priority(rct_count, review_count, True),
        "observational_priority": _priority(obs_count, review_count, False),
        "pico": pico,
        "rct_refs": [
            {
                "title": item.get("title", ""),
                "pmid": item.get("pmid", ""),
                "doi": item.get("doi", ""),
                "design": item.get("study_design", ""),
                "sample_size": _sample_size(item.get("abstract", "")),
                "link": item.get("link", ""),
                "doi_link": item.get("doi_link", ""),
            } for item in rct_refs[:8]
        ],
        "observational_refs": [
            {
                "title": item.get("title", ""),
                "pmid": item.get("pmid", ""),
                "doi": item.get("doi", ""),
                "design": item.get("study_design", ""),
                "sample_size": _sample_size(item.get("abstract", "")),
                "link": item.get("link", ""),
                "doi_link": item.get("doi_link", ""),
            } for item in obs_refs[:8]
        ],
        "search_query": topic_query,
        "pubmed_search_url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(topic_query),
        "review_search_url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(
            f"({topic_query}) AND ({REVIEW_QUERY})"
        ),
        "prospero_search_url": "https://www.crd.york.ac.uk/prospero/#recordsSubmitted?searchType=1&RecordID=&titleSearch="
        + quote_plus(phrase),
        "limitations": (
            "PubMed hit counts are candidate retrieval counts, not screened or eligible studies. "
            "RCT and observational searches are separate and can overlap. Sample sizes are "
            "abstract-only signals and must not be summed because cohorts may overlap. "
            "PROSPERO overlap and outcome compatibility have not been adjudicated."
        ),
    }


def discover_topics(seed_records: List[Dict], max_candidates: int = MAX_CANDIDATES) -> List[Dict]:
    """Generate and assess a shortlist of evidence-informed topic candidates."""
    eligible_seeds = [
        record for record in seed_records
        if record.get("study_design") in set(DESIGN_ORDER[:8])
        and record.get("title")
    ]
    if not eligible_seeds:
        return []
    candidates = _candidate_pairs(eligible_seeds, max_candidates)
    assessed = []
    for candidate in candidates:
        try:
            assessed.append(_assess_candidate(candidate))
        except requests.RequestException as exc:
            print(f"[warn] topic assessment failed for {candidate['terms']}: {exc}")
        except (ValueError, KeyError, TypeError) as exc:
            print(f"[warn] topic assessment could not parse {candidate['terms']}: {exc}")
    return assessed


if __name__ == "__main__":
    from monitor import find_recent_topic_seeds
    seeds = find_recent_topic_seeds()
    topics = discover_topics(seeds)
    for topic in sorted(topics, key=lambda x: max(x["rct_score"], x["observational_score"]), reverse=True):
        print(topic["topic"], topic["rct_count"], topic["observational_count"], topic["review_count"])

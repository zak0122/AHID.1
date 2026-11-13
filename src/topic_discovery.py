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
  OR randomized[tiab] OR randomised[tiab] OR randomization[tiab] OR randomisation[tiab]
  OR "randomly assigned"[tiab] OR placebo[tiab]
)
NOT (
  protocol[ti] OR editorial[pt] OR letter[pt] OR comment[pt]
  OR "Observational Study"[pt] OR "Cohort Studies"[mh]
  OR "Case-Control Studies"[mh] OR "Cross-Sectional Studies"[mh]
)
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
NOT (
  protocol[ti] OR editorial[pt] OR letter[pt] OR comment[pt]
  OR "Randomized Controlled Trial"[pt] OR "Controlled Clinical Trial"[pt]
)
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
    "data", "evidence",
}

GENERIC_MESH = {
    "humans", "male", "female", "adult", "aged", "middle aged",
    "aged, 80 and over", "young adult", "adolescent", "child", "infant",
    "child, preschool", "newborn", "pregnancy", "animals", "mice",
    "rats", "time factors", "treatment outcome", "retrospective studies",
    "prospective studies", "cohort studies", "clinical trial",
    "randomized controlled trial", "observational study",
    "case-control studies", "cross-sectional studies", "follow-up studies",
    "comparative study", "multicenter study", "multicentre study",
    "female", "male", "humans",
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
    """Build co-occurring medical concepts from MeSH and title words.

    MeSH descriptors contribute multi-word clinical concepts; title/abstract
    terms provide a fallback for newly indexed records with incomplete
    MEDLINE indexing. A candidate is retained only when both concepts occur
    in the same seed record. These remain discovery leads, not finalized PICO.
    """
    token_docs = []
    pair_sources = defaultdict(set)
    grouped = defaultdict(list)
    n_docs = max(len(records), 1)

    for index, record in enumerate(records):
        mesh_concepts = []
        for raw in record.get("mesh_terms", []) or []:
            phrase = re.sub(r"\s+", " ", str(raw)).strip()
            key = phrase.lower()
            if not phrase or key in GENERIC_MESH or len(phrase) < MIN_SEARCH_TERM_LENGTH:
                continue
            if key not in {x.lower() for x in mesh_concepts}:
                mesh_concepts.append(phrase)

        title_tokens = _tokens(record.get("title", ""))
        token_docs.append(set(title_tokens))

        pairs = set()
        # MeSH-MeSH pairs represent two indexed clinical concepts that
        # co-occurred in one primary study.
        if len(mesh_concepts) >= 2:
            concepts = mesh_concepts[:7]
            for i, first in enumerate(concepts):
                for second in concepts[i + 1:]:
                    if first.lower() != second.lower():
                        pairs.add(tuple(sorted((first, second), key=str.lower)))
        # Mixed MeSH/title pairs add specificity when only one useful
        # descriptor exists, and keep early/incompletely indexed articles.
        if mesh_concepts and title_tokens:
            for concept in mesh_concepts[:4]:
                for token in title_tokens[:4]:
                    if token.lower() not in concept.lower():
                        pairs.add(tuple(sorted((concept, token), key=str.lower)))
        # No useful MeSH concepts? Use adjacent non-generic title terms.
        if not mesh_concepts and len(title_tokens) >= 2:
            for first, second in zip(title_tokens, title_tokens[1:]):
                pairs.add(tuple(sorted((first, second), key=str.lower)))

        for pair in pairs:
            grouped[pair].append(record)
            mesh_keys = {term.lower() for term in mesh_concepts}
            title_keys = {term.lower() for term in title_tokens}
            if pair[0].lower() in mesh_keys and pair[1].lower() in mesh_keys:
                pair_sources[pair].add("MeSH co-occurrence")
            elif (
                (pair[0].lower() in mesh_keys and pair[1].lower() in title_keys)
                or (pair[1].lower() in mesh_keys and pair[0].lower() in title_keys)
            ):
                pair_sources[pair].add("MeSH + title")
            else:
                pair_sources[pair].add("title/abstract")

    df = Counter()
    for tokens in token_docs:
        df.update(tokens)
    for pair, seed_records in grouped.items():
        df[pair[0]] += len(seed_records)
        df[pair[1]] += len(seed_records)

    candidates = []
    for pair, seeds in grouped.items():
        a, b = pair
        # Higher score means more informative but not vanishingly rare.
        specificity = (
            math.log((n_docs + 1) / (df[a] / 2 + 1))
            + math.log((n_docs + 1) / (df[b] / 2 + 1))
        )
        candidates.append({
            "terms": pair,
            "seed_records": seeds,
            "seed_count": len({x.get("pmid") or id(x) for x in seeds}),
            "specificity": round(specificity, 2),
            "candidate_source": ", ".join(sorted(pair_sources[pair])),
        })

    candidates.sort(
        key=lambda candidate: (
            candidate["seed_count"],
            candidate["specificity"],
            max((record.get("pubdate", "") for record in candidate["seed_records"]), default=""),
        ),
        reverse=True,
    )
    return candidates[:limit]


def _concept_query(term: str) -> str:
    """Search a candidate concept in indexed MeSH and free-text fields."""
    escaped = term.replace('"', "")
    return f'("{escaped}"[Mesh] OR "{escaped}"[tiab])'


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


def _score(evidence_count: int, review_count: int, seed_count: int) -> int:
    """Transparent per-design heuristic score (not a validated prediction model)."""
    evidence = min(55.0, 14.0 * math.log2(max(evidence_count, 0) + 1))
    novelty = max(0.0, 30.0 - 10.0 * min(review_count, 3))
    seed_signal = min(15.0, 4.0 + 4.0 * math.log2(max(seed_count, 1)))
    return int(round(min(100.0, evidence + novelty + seed_signal)))


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
    topic_query = f'({_concept_query(term_a)}) AND ({_concept_query(term_b)})'
    rct_search = f"({topic_query}) AND ({RCT_QUERY})"
    obs_search = f"({topic_query}) AND ({OBSERVATIONAL_QUERY})"
    review_search = f"({topic_query}) AND ({REVIEW_QUERY})"
    rct_count, rct_ids = _pubmed_search(rct_search)
    obs_count, obs_ids = _pubmed_search(obs_search)
    review_count, review_ids = _pubmed_search(review_search)

    all_ids = list(dict.fromkeys(rct_ids[:20] + obs_ids[:20] + review_ids[:20]))
    details = _efetch_abstracts(all_ids)
    rct_refs = []
    obs_refs = []
    review_refs = []
    for pmid in rct_ids:
        item = details.get(pmid)
        if item and item.get("study_design") == DESIGN_ORDER[0]:
            rct_refs.append(item)
    observational_designs = set(DESIGN_ORDER[3:9])
    for pmid in obs_ids:
        item = details.get(pmid)
        if item and item.get("study_design") in observational_designs:
            obs_refs.append(item)
    for pmid in review_ids:
        item = details.get(pmid)
        if item and item.get("study_design") == "Systematic review / meta-analysis":
            review_refs.append(item)

    seeds = candidate["seed_records"]
    seed = seeds[0]
    pico = draft_pico(seed.get("title", ""), seed.get("abstract", ""),
                      seed.get("study_design", ""))
    total = rct_count + obs_count
    phrase = f"{term_a} {term_b}"
    return {
        "topic": phrase,
        "candidate_source": candidate.get("candidate_source", "title/abstract"),
        "candidate_specificity": candidate.get("specificity", 0),
        "rct_title": (
            f"{phrase.title()}: a systematic review and meta-analysis of randomized "
            "controlled trials"
        ),
        "observational_title": (
            f"{phrase.title()}: a systematic review and meta-analysis of observational studies"
        ),
        "seed_count": candidate["seed_count"],
        "seed_titles": [x.get("title", "") for x in seeds[:5]],
        "seed_pmids": [x.get("pmid", "") for x in seeds[:5]],
        "rct_count": rct_count,
        "observational_count": obs_count,
        "total_primary_hits": total,
        "review_count": review_count,
        "rct_score": _score(rct_count, review_count, candidate["seed_count"]),
        "observational_score": _score(obs_count, review_count, candidate["seed_count"]),
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
        "rct_search_url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(rct_search),
        "observational_search_url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(obs_search),
        "pubmed_search_url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(topic_query),
        "review_search_url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(review_search),
        "review_refs": [
            {
                "title": item.get("title", ""),
                "pmid": item.get("pmid", ""),
                "doi": item.get("doi", ""),
                "link": item.get("link", ""),
            } for item in review_refs[:8]
        ],
        "observational_design_counts": {
            design: sum(1 for item in obs_refs if item.get("study_design") == design)
            for design in observational_designs
        },
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

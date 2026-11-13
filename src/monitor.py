"""
monitor.py
----------
PubMed discovery and *deterministic* study-design classification.

Search strategy deliberately combines publication types / MeSH with title-
abstract terms. PubMed's publication-type and MeSH indexing can lag behind
the first entry of a record, so a MeSH-only or [pt]-only search misses new
studies. All returned records are then classified from PubMed metadata and
the title/abstract; uncertain records stay visible for human screening.
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional

import requests
import yaml

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")
REQUEST_TIMEOUT = 35
RETMAX = 1000
BATCH_SIZE = 100
REQUEST_PAUSE = 0.36 if not NCBI_API_KEY else 0.12

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "journals.yaml"
STATE_PATH = ROOT / "state" / "seen_pmids.json"
REVIEW_STATE_PATH = ROOT / "state" / "seen_review_pmids.json"
PROTOCOL_STATE_PATH = ROOT / "state" / "seen_protocol_pmids.json"

# High-recall primary-study retrieval. This is intentionally not a single
# MeSH-only filter. It includes the principal publication types/MeSH terms
# AND common title/abstract wording available before full MEDLINE indexing.
PRIMARY_STUDY_FILTER = """
(
  "Randomized Controlled Trial"[pt] OR "Controlled Clinical Trial"[pt]
  OR "Clinical Trial"[pt] OR "Clinical Trial, Phase II"[pt]
  OR "Clinical Trial, Phase III"[pt] OR "Clinical Trial, Phase IV"[pt]
  OR "Observational Study"[pt] OR "Comparative Study"[pt]
  OR "Cohort Studies"[mh] OR "Prospective Studies"[mh]
  OR "Retrospective Studies"[mh] OR "Case-Control Studies"[mh]
  OR "Cross-Sectional Studies"[mh]
  OR randomized[tiab] OR randomised[tiab] OR randomization[tiab]
  OR randomisation[tiab] OR "randomly assigned"[tiab]
  OR "random allocation"[tiab] OR placebo[tiab]
  OR "controlled trial"[tiab] OR "clinical trial"[tiab]
  OR "prospective cohort"[tiab] OR "retrospective cohort"[tiab]
  OR "population-based cohort"[tiab] OR "longitudinal cohort"[tiab]
  OR "case-control"[tiab] OR "cross-sectional"[tiab]
  OR observational[tiab] OR registry[tiab]
)
NOT (editorial[pt] OR letter[pt] OR comment[pt] OR news[pt] OR correction[pt])
"""

REVIEW_FILTER = """
(
  "Systematic Review"[pt] OR "Meta-Analysis"[pt]
  OR "systematic review"[tiab] OR "meta-analysis"[tiab]
  OR "meta analysis"[tiab]
)
NOT (protocol[ti] OR editorial[pt] OR letter[pt] OR comment[pt])
"""

PROTOCOL_FILTER = """
(
  "protocol"[ti] OR "study protocol"[tiab] OR "trial protocol"[tiab]
  OR "protocol for"[tiab]
)
"""

DESIGN_ORDER = [
    "Randomized controlled trial (RCT)",
    "Non-randomized interventional / clinical trial",
    "Prospective cohort",
    "Retrospective cohort",
    "Cohort (direction unclear)",
    "Case-control",
    "Cross-sectional",
    "Other observational study",
    "Other / unclear primary study (manual screening)",
]

REVIEW_DESIGN = "Systematic review / meta-analysis"
PROTOCOL_DESIGN = "Protocol (not an included results study)"
DEFAULT_DAYS_BACK = 30


def _load_journals() -> List[Dict]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}
    journals = cfg.get("journals", [])
    if not journals:
        raise ValueError(f"No journals configured in {CONFIG_PATH}")
    return journals


def _load_seen(path: Path) -> set:
    if not path.exists():
        return set()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return set(str(x) for x in value)
    except (OSError, ValueError, TypeError) as exc:
        print(f"[warn] could not read state file {path}: {exc}")
        return set()


def _save_seen(seen: set, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(sorted(seen), handle, indent=2)
    temporary.replace(path)


def _api_params(params: Dict) -> Dict:
    result = dict(params)
    if NCBI_API_KEY:
        result["api_key"] = NCBI_API_KEY
    return result


def _esearch(issn: str, days_back: int = DEFAULT_DAYS_BACK,
             extra_filter: str = PRIMARY_STUDY_FILTER) -> List[str]:
    """Return PubMed IDs for a journal and entry-date window."""
    term = f'"{issn}"[issn] AND ("last {int(days_back)} days"[edat])'
    if extra_filter:
        term += f" AND ({extra_filter})"
    params = {
        "db": "pubmed", "term": term, "retmax": str(RETMAX),
        "retmode": "json", "sort": "most recent",
    }
    response = requests.get(
        f"{NCBI_BASE}/esearch.fcgi", params=_api_params(params),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    time.sleep(REQUEST_PAUSE)
    data = response.json().get("esearchresult", {})
    if data.get("warninglist"):
        print(f"[warn] PubMed search warning for {issn}: {data['warninglist']}")
    return data.get("idlist", [])


def _node_text(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _classify_study_design(title: str, abstract: str,
                           publication_types: List[str],
                           mesh_terms: List[str]) -> str:
    """Classify from publication metadata first, then conservative text rules."""
    title_text = (title or "").lower()
    abstract_text = (abstract or "").lower()
    text = f"{title_text} {abstract_text}"
    pub = " | ".join(publication_types).lower()
    mesh = " | ".join(mesh_terms).lower()

    if any(x in pub for x in ("systematic review", "meta-analysis", "meta analysis")):
        return REVIEW_DESIGN
    if re.search(r"\b(systematic review|meta-analysis|meta analysis)\b", title_text):
        return REVIEW_DESIGN
    if re.search(r"\b(protocol|study protocol|trial protocol)\b", title_text):
        return PROTOCOL_DESIGN

    # RCT must be detected before generic clinical trial / cohort wording.
    randomized_pt = "randomized controlled trial" in pub or "controlled clinical trial" in pub
    randomized_words = bool(re.search(
        r"\b(randomi[sz]ed|randomi[sz]ation|randomly assigned|random allocation)\b",
        text,
    ))
    if randomized_pt or (randomized_words and re.search(r"\b(trial|participants|patients)\b", text)):
        return DESIGN_ORDER[0]

    nonrandomized = bool(re.search(
        r"\b(non[- ]randomi[sz]ed|nonrandomi[sz]ed|quasi[- ]experimental|"
        r"single[- ]arm trial|open[- ]label trial)\b", text
    ))
    clinical_trial_pt = "clinical trial" in pub or "clinical trial" in mesh
    if nonrandomized or (clinical_trial_pt and not randomized_words):
        return DESIGN_ORDER[1]

    if ("case-control studies" in mesh or "case-control" in pub
            or re.search(r"\b(case[- ]control|matched controls?)\b", text)):
        return DESIGN_ORDER[5]

    if ("cross-sectional studies" in mesh or "cross-sectional" in pub
            or re.search(r"\b(cross[- ]sectional|prevalence survey)\b", text)):
        return DESIGN_ORDER[6]

    prospective = (
        "prospective studies" in mesh
        or bool(re.search(r"\bprospective\b", text))
    )
    retrospective = (
        "retrospective studies" in mesh
        or bool(re.search(r"\bretrospective\b", text))
    )
    cohort = "cohort studies" in mesh or "cohort" in pub or bool(
        re.search(r"\b(cohort|longitudinal|follow[- ]up study)\b", text)
    )
    if cohort and prospective and not retrospective:
        return DESIGN_ORDER[2]
    if cohort and retrospective and not prospective:
        return DESIGN_ORDER[3]
    if cohort:
        return DESIGN_ORDER[4]

    observational_pt = "observational study" in pub
    observational_words = bool(re.search(
        r"\b(observational|registry-based|population-based study|database study)\b",
        text,
    ))
    if observational_pt or observational_words:
        return DESIGN_ORDER[7]

    if any(x in pub for x in ("case reports", "case report")):
        return "Case report / case series (usually exclude from SRMA pooling)"
    return DESIGN_ORDER[8]


def _efetch_abstracts(pmids: List[str]) -> Dict[str, Dict]:
    """Fetch rich PubMed metadata; preserve abstract labels, publication types and DOI."""
    if not pmids:
        return {}
    results: Dict[str, Dict] = {}
    for start in range(0, len(pmids), BATCH_SIZE):
        batch = pmids[start:start + BATCH_SIZE]
        params = {
            "db": "pubmed", "id": ",".join(batch),
            "rettype": "abstract", "retmode": "xml",
        }
        response = requests.get(
            f"{NCBI_BASE}/efetch.fcgi", params=_api_params(params),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)

        for article in root.findall(".//PubmedArticle"):
            pmid = _node_text(article.find(".//PMID"))
            if not pmid:
                continue
            title = _node_text(article.find(".//ArticleTitle"))
            abstract_parts = []
            for part in article.findall(".//Abstract/AbstractText"):
                label = part.attrib.get("Label") or part.attrib.get("NlmCategory") or ""
                value = _node_text(part)
                if value:
                    abstract_parts.append(f"{label}: {value}" if label else value)
            abstract = " ".join(abstract_parts)

            journal = _node_text(article.find(".//Journal/Title"))
            pubdate_year = _node_text(article.find(".//JournalIssue/PubDate/Year"))
            pubdate_month = _node_text(article.find(".//JournalIssue/PubDate/Month"))
            pubdate_day = _node_text(article.find(".//JournalIssue/PubDate/Day"))
            pubdate = " ".join(x for x in (pubdate_year, pubdate_month, pubdate_day) if x)

            publication_types = [
                _node_text(node) for node in article.findall(".//PublicationTypeList/PublicationType")
                if _node_text(node)
            ]
            mesh_terms = [
                _node_text(node.find("DescriptorName"))
                for node in article.findall(".//MeshHeadingList/MeshHeading")
                if node.find("DescriptorName") is not None
            ]
            doi = ""
            for node in article.findall(".//ArticleIdList/ArticleId"):
                if node.attrib.get("IdType", "").lower() == "doi":
                    doi = _node_text(node)
                    break
            if not doi:
                for node in article.findall(".//ELocationID"):
                    if node.attrib.get("EIdType", "").lower() == "doi":
                        doi = _node_text(node)
                        break

            authors = []
            for author in article.findall(".//AuthorList/Author")[:8]:
                collective = _node_text(author.find("CollectiveName"))
                last = _node_text(author.find("LastName"))
                fore = _node_text(author.find("ForeName"))
                name = collective or " ".join(x for x in (fore, last) if x)
                if name:
                    authors.append(name)

            design = _classify_study_design(title, abstract, publication_types, mesh_terms)
            results[pmid] = {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "journal": journal,
                "pubdate": pubdate,
                "publication_types": publication_types,
                "mesh_terms": mesh_terms,
                "doi": doi,
                "authors": authors,
                "study_design": design,
                "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "doi_link": f"https://doi.org/{doi}" if doi else "",
            }
        time.sleep(REQUEST_PAUSE)
    return results


def _find_new_records(days_back: int, query_filter: str, state_path: Path,
                      desired_design: Optional[str] = None) -> List[Dict]:
    seen = _load_seen(state_path)
    new_records: List[Dict] = []
    journals = _load_journals()

    for journal in journals:
        try:
            pmids = _esearch(journal["issn"], days_back=days_back, extra_filter=query_filter)
            fresh = [pmid for pmid in pmids if pmid not in seen]
            if not fresh:
                continue
            details = _efetch_abstracts(fresh)
            # Mark only records successfully fetched. Failed IDs can be retried next run.
            for pmid, record in details.items():
                seen.add(pmid)
                record["journal_config_name"] = journal["name"]
                if desired_design is None or record["study_design"] == desired_design:
                    new_records.append(record)
        except Exception as exc:
            print(f"[warn] PubMed scan failed for {journal.get('name', journal['issn'])}: {exc}")

    _save_seen(seen, state_path)
    return new_records


def find_new_articles(days_back: int = DEFAULT_DAYS_BACK) -> List[Dict]:
    """Return new primary-study signals, with RCT and observational designs classified separately."""
    records = _find_new_records(days_back, PRIMARY_STUDY_FILTER, STATE_PATH)
    reviews_and_protocols = {REVIEW_DESIGN, PROTOCOL_DESIGN}
    excluded = reviews_and_protocols
    # Keep uncertain records visible: excluding them automatically would
    # recreate the false-negative problem this high-recall search is designed to avoid.
    return [
        record for record in records
        if record.get("study_design") not in excluded
        and not record.get("study_design", "").startswith("Case report")
    ]


def find_new_reviews(days_back: int = DEFAULT_DAYS_BACK) -> List[Dict]:
    """Find newly indexed systematic reviews/meta-analyses independently of primary studies."""
    return _find_new_records(
        days_back, REVIEW_FILTER, REVIEW_STATE_PATH,
        desired_design=REVIEW_DESIGN,
    )


def find_new_protocols(days_back: int = DEFAULT_DAYS_BACK) -> List[Dict]:
    """Find protocol publications separately; protocols are not completed results studies."""
    return _find_new_records(
        days_back, PROTOCOL_FILTER, PROTOCOL_STATE_PATH,
        desired_design=PROTOCOL_DESIGN,
    )


if __name__ == "__main__":
    articles = find_new_articles()
    print(f"Found {len(articles)} new primary-study signal(s).")
    for design in DESIGN_ORDER:
        matches = [article for article in articles if article["study_design"] == design]
        if matches:
            print(f"\n{design}: {len(matches)}")
            for article in matches:
                print(f"- {article['title']} ({article['link']})")
    reviews = find_new_reviews()
    protocols = find_new_protocols()
    print(f"\nNew systematic reviews/meta-analyses: {len(reviews)}")
    print(f"New protocols (not results studies): {len(protocols)}")

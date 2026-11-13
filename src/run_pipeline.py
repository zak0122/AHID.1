"""
run_pipeline.py
---------------
Daily entry point:
  1. Monitor newly indexed primary studies, reviews and protocols.
  2. Classify primary studies by design (RCT vs observational subtypes).
  3. Discover recent evidence-informed SRMA topic candidates.
  4. Run separate PubMed RCT / observational / existing-review searches.
  5. Produce a transparent ranked shortlist and article-level PICO drafts.

All extraction is API-key-free and rule-based. Counts are retrieval signals,
not final eligible study counts or automatic PROSPERO decisions.
"""

import datetime as dt
from collections import Counter
from pathlib import Path

from monitor import (
    DESIGN_ORDER,
    find_new_articles,
    find_new_reviews,
    find_new_protocols,
    find_recent_topic_seeds,
)
from pico_extract import draft_pico
from topic_discovery import discover_topics
from rss_watch import find_new_rss_items
from outcome_archive import save_daily_outcome

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"


def _ref_lines(refs):
    lines = []
    for ref in refs:
        doi = f" | [DOI](https://doi.org/{ref['doi']})" if ref.get("doi") else ""
        lines.append(
            f"- [{ref.get('title', 'Untitled')}]({ref.get('link', '')}) "
            f"— {ref.get('design', 'design unclear')}; abstract sample-size signal: "
            f"{ref.get('sample_size', 'not reported')}{doi}"
        )
    if not lines:
        lines.append("- No reference details returned in the first PubMed sample; open the search link.")
    return lines


def _topic_section(topics, design_key, priority_key, title_key, heading):
    lines = [f"\n## {heading}\n"]
    if not topics:
        return lines + ["No topic candidates could be assessed in this run.\n"]
    ranked = sorted(topics, key=lambda x: x[design_key], reverse=True)[:5]
    for index, topic in enumerate(ranked, 1):
        lines.append(f"### {index}. {topic[title_key]}")
        lines.append(
            f"**Candidate source:** {topic.get('candidate_source', 'title/abstract')}  \n"
            f"**Seed records sharing concepts:** {topic['seed_count']}  \n"
            f"**Score:** {topic[design_key]}/100 (heuristic triage, not a validated model)  \n"
            f"**Decision signal:** {topic[priority_key]}  \n"
            f"**Recent journal seed records:** {topic['seed_count']}  \n"
            f"**PubMed candidate hits:** {topic['rct_count']} RCT-search hits; "
            f"{topic['observational_count']} observational-search hits; "
            f"{topic['review_count']} existing-review-search hits\n"
            f"**Separate searches:** [RCT-only]({topic['rct_search_url']}) | "
            f"[Observational-only]({topic['observational_search_url']}) | "
            f"[Existing reviews]({topic['review_search_url']})\n"
        )
        pico = topic["pico"]
        lines.append("**Seed-study PICO/PECO draft (rule-based; verify):**")
        lines.append(f"- Population: {pico['population']}")
        lines.append(f"- Intervention/exposure: {pico['intervention']}")
        lines.append(f"- Comparator: {pico['comparator']}")
        lines.append(f"- Outcome: {pico['outcome']}")
        lines.append(f"- Seed design: {pico['study_design']}")
        lines.append(f"- Extraction notes: {pico['confidence_notes']}\n")
        lines.append(
            f"**Searches:** [PubMed candidate pool]({topic['pubmed_search_url']}) | "
            f"[Existing reviews]({topic['review_search_url']}) | "
            f"[PROSPERO title search]({topic['prospero_search_url']})\n"
        )
        lines.append("**RCT evidence references (separate track):**")
        lines.extend(_ref_lines(topic["rct_refs"]))
        lines.append("\n**Observational evidence references (separate track):**")
        lines.extend(_ref_lines(topic["observational_refs"]))
        lines.append("\n**Observational design subtypes in the fetched citation sample (not whole-pool counts):**")
        for design, n in topic.get("observational_design_counts", {}).items():
            lines.append(f"- {design}: {n}")
        lines.append("\n**Existing review/meta-analysis references:**")
        if topic.get("review_refs"):
            for review in topic["review_refs"]:
                doi = f" | [DOI](https://doi.org/{review['doi']})" if review.get("doi") else ""
                lines.append(f"- [{review['title']}]({review['link']}){doi}")
        else:
            lines.append("- No review references in the first PubMed sample; open the complete review search.")

        lines.append(f"\n> **Interpretation:** {topic['limitations']}\n")
        lines.append("---")
    return lines


def build_report() -> str:
    today = dt.date.today().isoformat()
    lines = [
        f"# AHID — SRMA Topic Discovery & Journal Watch ({today})",
        "",
        "> Automated evidence triage, not a substitute for duplicate removal, "
        "title/abstract screening, full-text eligibility, outcome harmonisation, "
        "risk-of-bias assessment or protocol registration.",
        "",
    ]

    try:
        articles = find_new_articles(days_back=30)
    except Exception as exc:
        articles = []
        lines.append(f"**New-article monitor warning:** {type(exc).__name__}: {exc}\n")

    try:
        reviews = find_new_reviews(days_back=30)
    except Exception as exc:
        reviews = []
        lines.append(f"**Review monitor warning:** {type(exc).__name__}: {exc}\n")

    try:
        protocols = find_new_protocols(days_back=30)
    except Exception as exc:
        protocols = []
        lines.append(f"**Protocol monitor warning:** {type(exc).__name__}: {exc}\n")

    lines.append("## 1. New primary-study signals")
    lines.append(f"**Records retrieved for this run:** {len(articles)}\n")
    design_counts = Counter(record.get("study_design", "Other / unclear") for record in articles)
    for design in DESIGN_ORDER:
        lines.append(f"- {design}: **{design_counts.get(design, 0)}**")
    if not articles:
        lines.append("- No new primary-study signals this run (this does not mean no research exists).")
    for record in articles[:100]:
        doi = f" | [DOI](https://doi.org/{record['doi']})" if record.get("doi") else ""
        lines.append(
            f"- [{record['title']}]({record['link']}) — "
            f"**{record['study_design']}**; {record.get('journal_config_name', '')}; "
            f"{record.get('pubdate', '')}{doi}"
        )

    lines.append("\n## 2. Newly indexed systematic reviews / meta-analyses")
    lines.append(f"**Records:** {len(reviews)}")
    for record in reviews[:100]:
        lines.append(f"- [{record['title']}]({record['link']}) — {record.get('journal_config_name', '')}")

    lines.append("\n## 3. Protocol publications (not results studies)")
    lines.append(f"**Records:** {len(protocols)}")
    for record in protocols[:100]:
        lines.append(f"- [{record['title']}]({record['link']}) — {record.get('journal_config_name', '')}")

    try:
        rss_items = find_new_rss_items()
    except Exception as exc:
        rss_items = []
        lines.append(f"\n**RSS monitor warning:** {type(exc).__name__}: {exc}")

    lines.append("\n## 4. Ahead-of-print RSS alerts (unindexed leads)")
    lines.append(f"**Items:** {len(rss_items)}")
    for item in rss_items[:100]:
        lines.append(
            f"- [{item.get('title', 'Untitled')}]({item.get('link', '')}) — "
            f"{item.get('journal_config_name', '')}; {item.get('pubdate', '')}"
        )

    lines.append("\n## 5. Topic discovery method")
    lines.append(
        "The topic engine starts with a PubMed-wide primary-study sample from the preceding "
        "365 days, supplements it with configured-journal coverage, derives MeSH/title concepts, and checks each "
        "candidate in PubMed with separate RCT, observational and review searches. This is broad discovery, not an exhaustive systematic search. "
        "The rankings below are heuristic triage scores; they do not prove novelty or "
        "meta-analysis feasibility."
    )
    topics = []
    try:
        seeds = find_recent_topic_seeds(days_back=365, max_per_journal=10, max_global=400)
        lines.append(f"\n**Recent primary-study seed sample:** {len(seeds)} records.")
        topics = discover_topics(seeds)
        lines.append(f"**Candidates assessed:** {len(topics)}.")
        lines.append(
            "**Score formula (heuristic, not validated):** design-specific evidence term "
            "min(55, 14 × log2(search hits + 1)) + review-overlap term "
            "max(0, 30 − 10 × min(review hits, 3)) + recent-seed term "
            "min(15, 4 + 4 × log2(seed records)). The RCT score uses RCT hits only; "
            "the observational score uses observational hits only."
        )
        lines.append(
            "**TOP PRIORITY label:** at least 3 RCT-search hits or 5 observational-search "
            "hits, and no more than 1 review-search hit. This is a manual-screening "
            "threshold, not proof that the studies are eligible or poolable."
        )
    except Exception as exc:
        lines.append(f"\n**Topic discovery warning:** {type(exc).__name__}: {exc}")

    lines.extend(_topic_section(
        topics, "rct_score", "rct_priority", "rct_title",
        "6. Ranked topics for RCT-only SRMA consideration",
    ))
    lines.extend(_topic_section(
        topics, "observational_score", "observational_priority", "observational_title",
        "7. Ranked topics for observational-study SRMA consideration",
    ))

    lines.extend([
        "\n## 8. Important interpretation rules",
        "- RCT and observational evidence counts are separate search results. Do not combine them into one pooled estimate.",
        "- Prospective cohorts, retrospective cohorts, case-control and cross-sectional studies remain distinct in the underlying metadata and are listed separately in the new-study feed.",
        "- PubMed counts are candidate hits, not the number of included studies. Screen every citation against prespecified eligibility criteria.",
        "- Abstract sample-size snippets are not summed: cohorts may overlap and abstracts may report different denominators.",
        "- A review hit is an overlap warning, not proof that the proposed PICO is already covered. Read its population, dates, interventions, outcomes and included studies.",
        "- Check PROSPERO and other registries manually; a search link is provided but registration status is not programmatically adjudicated.",
        "- No automatic impact factor, Q1/Q2, acceptance probability or publication guarantee is assigned.",
        "",
        "## 9. Next human steps",
        "1. Export each shortlisted RCT and observational search separately to RIS/CSV.",
        "2. Deduplicate PMIDs/DOIs and perform two-reviewer title/abstract screening.",
        "3. Retrieve full texts; confirm population, design, interventions/exposures, comparators, outcomes, follow-up and overlap.",
        "4. Assess design-appropriate risk of bias and whether outcomes/timepoints are sufficiently compatible for pooling.",
        "5. Check PROSPERO and recent reviews before selecting/registering a final protocol.",
        "",
    ])
    return "\n".join(lines)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report = build_report()
    dated_path = OUTPUT_DIR / f"report_{dt.date.today().isoformat()}.md"
    latest_path = OUTPUT_DIR / "report_latest.md"
    dated_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")
    archived_path = save_daily_outcome(report, root=ROOT)
    print(f"Wrote report to {dated_path}, {latest_path}, and immutable archive {archived_path}")

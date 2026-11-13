# AHID — SRMA Topic Discovery

> **Start here for the curated shortlist:** [VIABLE TOPICS MASTER](VIABLE_TOPICS_MASTER.md) — one document containing only evidence-screened viable topics, daily updates and monthly summaries.


AHID is a reproducible PubMed-based research surveillance and SRMA topic-triage tool. It is designed to help a research team find clinically relevant emerging questions and inspect the evidence pool before committing to a protocol.

## What it does

1. **Broad PubMed retrieval.** Uses publication-type tags, MeSH terms and title/abstract text words. This is intentionally not MeSH-only because new records may not have completed MEDLINE indexing.
2. **Study-design classification.** Classifies RCTs, non-randomized clinical trials, prospective cohorts, retrospective cohorts, cohorts with unclear direction, case-control studies, cross-sectional studies and other observational studies separately. Uncertain records remain labelled as uncertain for manual screening.
3. **Separate review and protocol alerts.** Systematic reviews/meta-analyses are reported separately from protocols; protocols are not treated as results studies.
4. **Topic candidate generation.** Samples recent primary studies from the configured journal set, derives co-occurring clinical concepts from MeSH descriptors plus title/abstract terms, and checks them against PubMed.
5. **Two separate SRMA tracks.** Every candidate gets independent RCT and observational search counts, references and scores. Do not pool those designs together merely because they concern the same topic.
6. **Existing-review signal.** Searches for systematic reviews/meta-analyses separately and links to a PROSPERO title search.
7. **Transparent PICO drafts.** Extracts only what simple text rules can identify. It does not call Gemini, Claude or another LLM, and it marks unclear fields rather than fabricating details.
8. **Evidence references.** Reports PubMed links, available DOI, design label and a cautious abstract-level sample-size signal for retrieved references.

## Important limitations

AHID is a **triage system**, not an automated systematic review. PubMed hit counts are not screened or eligible study counts. The topic score is a transparent heuristic, not a validated model of novelty, feasibility or publication probability. Keyword pairs can retrieve irrelevant studies, and the RCT and observational searches can overlap. Abstract sample-size snippets are not summed because cohorts can overlap and denominators may differ.

Before selecting a topic, researchers must:
- Export each design-specific search, deduplicate and screen titles/abstracts.
- Retrieve full texts and confirm eligibility, study design, population, interventions/exposures, comparator, outcomes and follow-up.
- Check outcome definitions/timepoints, independence of cohorts and whether quantitative pooling is defensible.
- Assess risk of bias with a design-appropriate tool and assess certainty where relevant.
- Read existing reviews and check PROSPERO/other registries manually.
- Select a final question and protocol with a methodologist/statistician.

The tool does not promise Q1 publication, impact factor, acceptance or a fixed minimum study count.

## Data sources and scope

- **PubMed / NCBI E-utilities:** main source of publication metadata, abstracts, PubMed IDs, publication types, MeSH terms and DOI when indexed.
- **Configured journals:** config/journals.yaml determines the journals used for the recent topic-seed sample and routine journal watch. The design-specific topic searches then run against PubMed as a whole.
- **Journal RSS feeds:** optional ahead-of-print alerts. RSS items are leads only, not verified indexed records.

Because topic seeds come from the configured journals, this is not an exhaustive global discovery search. Expand and maintain config/journals.yaml for the specialties you actually cover.

## Setup

Python 3.10+ is recommended.

```bash
pip install -r requirements.txt
```

Optional environment variable:
- NCBI_API_KEY — free NCBI key; increases E-utilities request rate. The pipeline works without it, but runs more slowly.

**No GEMINI_API_KEY, ANTHROPIC_API_KEY, or other generative-AI key is required.**

## Local run

```bash
cd src
python run_pipeline.py
```

The report is written to:
- output/report_YYYY-MM-DD.md
- output/report_latest.md
- outcomes/YYYY/MM/DD/report_run-N.md — immutable daily archive
- outcomes/YYYY/MM/DD/metadata_run-N.json — run provenance and report checksum
- outcomes/YYYY/README.md — yearly chronological index

To change the number of candidates assessed, set MAX_TOPIC_CANDIDATES (default 12). More candidates means more PubMed requests and a longer run.

## Tests

Run the rule-based tests without making network requests:

```bash
python -m unittest discover -s tests -v
```

## GitHub Actions

The workflow in .github/workflows/daily_monitor.yml runs daily on main and can also be started manually from the Actions tab. Pushes to main and feature branches run the same offline tests and discovery pipeline. It:
- Installs dependencies and runs offline unit tests first.
- Runs PubMed discovery and writes the Markdown report.
- Uploads generated reports as a 14-day Actions artifact.
- Commits updated state and reports only on main; feature-branch runs do not push generated data.

Add NCBI_API_KEY under **Settings → Secrets and variables → Actions** if desired. No Gemini secret is needed.

## State files

- state/seen_pmids.json — newly surfaced primary-study feed.
- state/seen_review_pmids.json — review/meta-analysis alerts.
- state/seen_protocol_pmids.json — protocol alerts.
- state/seen_rss_links.json — RSS item de-duplication.

Topic discovery now starts from a bounded PubMed-wide primary-study sample from the preceding 365 days, then adds a small configured-journal supplement. It is not limited to the journal list, but it is still a bounded discovery sample rather than an exhaustive systematic search. The sample is independent of the newly-seen journal watcher.

## Daily outcomes archive

Every successful pipeline run saves an immutable snapshot under `outcomes/YYYY/MM/DD/`:
- `report_run-N.md`: the complete human-readable report for that workflow run.
- `metadata_run-N.json`: date, UTC save time, Git commit, workflow run identifiers and SHA-256 report checksum.
- `outcomes/YYYY/README.md`: chronological index for year-end review.

Multiple runs on one date are preserved separately. The archive is committed only by scheduled/manual runs on `main`; feature-branch runs upload test artifacts without changing the historical archive. Keep the archive in Git so it can be searched, compared and amended through normal pull requests. Future pipeline changes should record their rationale in the repository changelog and preserve prior daily snapshots.


## Citation-level screening outputs

For each discovered candidate, the daily pipeline exports separate RCT, observational and review CSVs under `output/screening/YYYY-MM-DD/`. The exports contain PMID, DOI, title, design label, abstract, PubMed link and a conservative triage label.

- Duplicate records are removed by PMID, DOI and normalized title.
- Reviews are marked as secondary evidence rather than primary-study records.
- Clear protocols/editorials/letters are flagged out of the results pool.
- Other citations remain `unclear_human_screen` or `manual_check`; the tool does not claim final eligibility without topic-specific criteria and full-text review.
- Screening CSVs are uploaded as GitHub Actions artifacts and committed with daily outputs on main.

The current PICO/PECO extraction is a seed-article draft, not a validated protocol. Before screening, the research team must refine the question, define inclusion/exclusion criteria and check the search strategy across relevant databases.

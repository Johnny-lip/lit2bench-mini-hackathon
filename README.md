# From Literature to Benchmarks — Participant Handout

## At a Glance

|  |  |
| --- | --- |
| **Venue** | Institut Pascal, Université Paris-Saclay |
| **Half-day 1** | Tuesday 24 Nov · 9:30–12:30 |
| **Half-day 2** | Thursday 26 Nov · 9:30–12:30 |
| **Organizers** | Dr. João A. Gonçalves · Romina Raoofian · Justus Mairböck (University of Bonn · B-IT / Lamarr Institute) |
| **Contact** | jagoncalves@uni-bonn.de · s28rraoo@uni-bonn.de · s6jumair@uni-bonn.de |
| **Submission** | Thursday 26 Nov, 12:30 (end of Block 4) · your catalogue on Codabench: <https://www.codabench.org/competitions/18675/> |

## Getting Started

What is in this repository:

| Folder / file | Contents |
| --- | --- |
| [`suggested pipelines/`](suggested%20pipelines/) | `pipeline_01.ipynb` (laptop only) and `pipeline_02.ipynb` (with the LLM API): guidelines with gaps to fill that follow Blocks 1–4 |
| [`helpers/`](helpers/) | Corpus access, API client, API monitor, text cleaning and quote check ([README](helpers/README.md)) |
| [`catalogue/`](catalogue/) | Catalogue format (`schema.json`), two example tasks (`example.json`) and `validate.py` |
| [`corpus/`](corpus/) | Where the downloaded corpus goes ([README](corpus/README.md)) |
| [`API_NOTES.md`](API_NOTES.md) | LLM API: models, settings, limits, error messages and fixes |
| [`DISCLAIMER.md`](DISCLAIMER.md) | Terms of use of the corpus: research within the hackathon only; don't share or commit any part of it |

Setup (tested with Python 3.12):

```bash
python3.12 -m venv .venv && source .venv/bin/activate      # or: uv venv --python 3.12
pip install -r "suggested pipelines/requirements.txt"       # includes helpers/requirements.txt
export ILAAS_API_KEY=...                                     # only for the LLM API
```

Download the corpus into `corpus/` (link and layout in [corpus/README.md](corpus/README.md)); one decade is enough to start.
Check a catalogue with `python3 catalogue/validate.py your_catalogue.json` (add `--corpus` to check the quotes against the papers).

## What This Hackathon Is About

Physics has established measurements and studies — but no automated, reproducible way to extract downstream task candidates for foundation models from that literature. That is what this hackathon is designed to build: starting from a prepared corpus, your team identifies candidate benchmark tasks, links each to supporting papers, and documents the evidence. The result is a **task → papers catalogue** that future benchmark builders can use.

**What you are NOT doing:** training a foundation model or constructing a complete benchmark suite. **The key distinction throughout:** a paper *about* a topic is not automatically a paper that *establishes a benchmark task*. A task has a defined input modality, a defined prediction target and a scientific setting (e.g. *particle-level jet constituents → jet label*); a topic is not a task. Your job is to make that distinction explicit and defensible.

## Block 1 — Tuesday · Setup and Orientation

**Duration:** \~1 hour · **Half-day 1, opening block**

### Goal

Every team member can load the corpus, run the setup of the suggested pipeline (`suggested pipelines/pipeline_01.ipynb`), and read the two example tasks in `catalogue/example.json`. You should understand what a correct task–paper match looks like before moving on.

### Possible approaches

Pick one or combine:

- **Suggested pipeline** — open `suggested pipelines/pipeline_01.ipynb`, run its setup and write the search patterns for your area (sections 1–2) on one decade first. It is a guideline with gaps to fill, not a solution; its later sections and `pipeline_02.ipynb` (with the LLM API) follow Blocks 2–4.
- **Explore the index** — load one decade's TSV index (e.g. with pandas) and look at the `topics` and `inspire_categories` columns and full-text availability. Filter it by `topics` or by `inspire_categories` (or both) to get your record IDs, then pull those records from that decade's JSONL file.
- **Hand search** — search a few titles for a task you already know (e.g. jet tagging) and judge the hits yourself.
- **LLM check** — ask the LLM API whether a sample abstract performs your candidate task, then compare with your own reading.

### Pay attention to

- A paper that *performs* a task is not the same as one that merely *discusses* it.
- A task = input modality + prediction target + scientific setting; a topic is not a task.
- Abstracts contain LaTeX, MathML and HTML: clean them before searching or prompting (`strip_latex()` in `helpers/textutils.py`), or keyword hits and quote checks fail.
- The two example tasks in `catalogue/example.json` do not count toward the minimum.

### Checkpoint: end of Block 1

- [ ] Pipeline 1 setup runs and the corpus is queryable
- [ ] Candidate task in mind (name, input, prediction target)

---

## Block 2 — Tuesday · Retrieval and Candidate Discovery

**Duration:** \~2 hours · **Half-day 1, main working block**

### Goal

Produce a ranked candidate list of papers for your chosen application area (pp, PbPb, GW or multimessenger), selected by topic pool, by INSPIRE category, or both, grouped by task. By the end of Tuesday you should have enough material to start Thursday with refinement, not from scratch.

### Possible approaches

Pick one or combine:

- **Query families** — several phrasings per task (*"jet tagging"*, *"flavour tagging"*, *"quark gluon discrimination"*), weighting title matches above abstract matches.
- **Topic clustering** — local embeddings + BERTopic on the abstracts of your selection to see which subjects it contains; pick the clusters where a task is likely, then split them by task (Block 3, task clustering).
- **Seed and expand** — start from one clearly relevant paper and pull similar ones (shared keywords, embedding similarity, citations).
- **LLM-generated queries** — ask the LLM API for synonyms and task phrasings, then run them lexically; log the hits per term (many match nothing) and let only terms that name the task select a paper (method terms like "graph neural network" count only together with a task term).
- **Pre-filtering** — narrow your selection (topic pool and/or categories) first to papers mentioning ML methods (keyword list or INSPIRE categories) to speed up everything else.

### Pay attention to

- **Group by task, not topic.** Note `fm_scope`, `input_modality` and `observable_or_target` per task; never merge pp with PbPb, or GW detection with parameter estimation.
- A paper can carry more than one task label when it genuinely performs several tasks; it then gets one catalogue row per task.
- Generic terms like *"classification"* over-assign; look for main-contribution phrasing.
- Don't hard-exclude a paper just because it compares several tasks or uses vocabulary from more than one area; mark it `uncertain` instead.
- Some tasks appear without ML wording (plain measurement studies) — a filtered pool can miss them.
- Keep the list small enough to review; mark each paper `candidate`, `supported`, `uncertain` or `excluded`.
- Log every query or method — you need it Thursday and for `retrieval_method`.

### Checkpoint: end of Block 2 (= end of Tuesday)

- [ ] At least two distinct tasks, each with name, area, input modality and prediction target
- [ ] Candidate list of 50–200 papers per task, each with a provisional decision
- [ ] Query / method log saved

> **Thursday:** Bring your candidate list and query log. Block 3 begins with refining what you built today, not restarting.

---

## Block 3 — Thursday · Refinement and Evidence Queue

**Duration:** \~1.5 hours · **Half-day 2, opening block**

### Goal

Refine your retrieval, resolve duplicates, and produce a prioritised evidence-review queue — a shortlist of papers worth reading carefully to verify task–paper matches. By the end of this block you should have a refined method (or a justified decision to keep your baseline) and know exactly which papers to verify in Block 4.

### Possible approaches

Pick one or combine:

- **Sharper queries** — tighten queries that returned many `excluded` papers; broaden or add synonyms where known papers were missed.
- **Lightweight classifier** — train e.g. logistic regression on TF-IDF features using the papers you labelled on Tuesday.
- **Embedding re-ranking** — score candidates by similarity to a written task description.
- **Task clustering** — embed each title with the sentences that say what the paper does ("we propose …"), cluster hierarchically (try a few distance thresholds) and name each cluster by its TF-IDF keywords minus area and method words; keep the clusters that share one task.
- **LLM screening** — let the LLM API pre-sort abstracts into `candidate` / `uncertain` / `excluded`, kept as `automated_proposal`. LLMs tend to give confident answers and rarely say uncertain on their own; consider running two prompt variants and marking papers where they disagree as uncertain.
- **Manual triage** — rank by title alone; a focused lexical baseline is enough to reach the minimum.

### Pay attention to

- Merge wording variants only when input, target and scientific setting align.
- Group versions of the same study under one `study_group_id`; never count a study twice.
- Compare any new method against Tuesday's baseline on the same candidates, and record what changed and why.
- The queue holds 20–50 papers per task: your `uncertain` and most promising `candidate` papers.

### Checkpoint: end of Block 3

- [ ] Refined method (or kept baseline) compared (even informally) against Tuesday's baseline
- [ ] Duplicates resolved; `study_group_id` assigned
- [ ] Evidence-review queue of 20–50 papers per task, ranked by priority
- [ ] Every retrieved paper has at least a provisional `decision`

---

## Block 4 — Thursday · Verification and Depth

**Duration:** \~1.5 hours · **Half-day 2, closing block**

### Goal

Verify enough task–paper matches to meet the minimum success criterion, investigate one task in depth, and produce a clean, submittable catalogue.

### Possible approaches

Pick one or combine:

- **Close reading** — title and abstract first; open the full text only where the match is still unclear.
- **LLM-assisted verification** — send the task description + 2–3 selected passages (\~2k tokens; not the whole paper: long prompts from many teams overload the shared server) to the LLM API and get back a decision with a quoted passage, kept as `automated_proposal` until you confirm it. Ask for one contiguous verbatim quote and check that it occurs in the text (`quote_match()` in `helpers/textutils.py`); if the full-text verdict disagrees with the abstract screening, mark the row uncertain.
- **Passage search** — keyword or embedding search inside the full text to locate the evidence sentence quickly.
- **Depth comparison** — for one task, line up 3–5 papers by input, metric and result to see where evidence is strong or ambiguous.

### Pay attention to

- **No new infrastructure in this block** — use what you have; the catalogue is the deliverable.
- Meet the minimum first (two tasks, one or two `supported` papers each), then go deeper.
- A `supported` row needs a quoted `evidence_text`, a `match_rationale` and `review_status: human_reviewed`; an LLM answer alone never counts.
- Record `study_type` for each verified paper.

### Checkpoint: minimum success checklist

- [ ] At least 2 distinct `task_id` values in the catalogue
- [ ] Each has at least 1 row with `decision: supported` and `review_status: human_reviewed`
- [ ] `paper_id` and `paper_url` present and non-duplicated (check `study_group_id`)
- [ ] CSV/JSON exported with every required field and checked with `catalogue/validate.py`
- [ ] Submitted on Codabench

## Application Areas

Choose **one** area. Select its papers by topic pool, by INSPIRE category, or both. Most papers have no topic, so a category selection finds more, but keep the distinctions below: categories alone don't separate pp from PbPb or detection from parameter estimation.

| Area | Key distinction | Example tasks |
| --- | --- | --- |
| **pp collisions** | Distinct from PbPb — different physics and evaluation setting | Jet tagging, jet-energy regression |
| **PbPb / heavy ion** | Distinct from pp — quark–gluon plasma context | Collective-flow observables, jet quenching, particle spectra |
| **Gravitational waves** | Detection ≠ parameter estimation — different inputs and metrics | Signal/noise classification; mass/spin/sky-location regression |
| **Multimessenger** | Multi-messenger signals combined; small pool (3,777 papers, about half also in GW), so add INSPIRE categories | Cross-messenger association, source classification |

## Tools, Data and Technical Setup

### What the organizer provides

- **Corpus** — 1,894,408 INSPIRE-HEP records in **JSONL files**, split by decade. Files are compressed with zstandard; the full release is \~23 GB. Each record contains a record ID, title, abstract, source, and INSPIRE categories. Full text is given in separate files. One TSV index file per decade (e.g. index\_2020s.tsv.zst) holds each record's metadata, including a `topics` column listing its topic pools (one or more, e.g. `pbpb|gw`) and whether abstract and full text are available. Topic pools are not separate downloads: filter each decade's index by its `topics` column to extract your area's records. You can also select by INSPIRE category: the index's `inspire_categories` column holds 17 categories (e.g. Experiment-HEP, Instrumentation, Data Analysis and Statistics). Use a topic pool, one or more categories, or both.

  **Most records have no topic.** 1,776,258 of 1,894,408 records (93.8 %) have an empty `topics` column, so the four pools together cover only about 6 % of the corpus. INSPIRE categories cover far more: more than 99.5 % of records from 2010 on have one, while about a third of the records before 2000 have none. A category selection therefore reaches papers that no pool contains, but categories don't separate pp from PbPb or GW from multimessenger, so check the area of each hit.
- **Starter code** — in the lit2bench-mini-hackathon repository, helpers/: corpus\_extract.py (select papers by topic pool and/or INSPIRE category, stream metadata and full text, save a subset), ilaas\_client.py (API client with retries, response cache and budget logging), api\_monitor.py (live view of calls, errors with fixes, budget) and textutils.py (clean LaTeX, check quotes). Suggested pipelines in suggested pipelines/: pipeline\_01.ipynb (laptop only) and pipeline\_02.ipynb (with the LLM API), guidelines with gaps to fill that follow Blocks 1–4. catalogue/ holds the catalogue format (schema.json, example.json) and validate.py.
- **API to open-source LLMs** — open-source models behind an OpenAI-compatible endpoint. Default model gemma-4-31b with thinking off; at most 4 parallel requests per team; at most 2 for calls with long texts; client timeout 300 s by default, 600 s for long texts; after a timeout wait ≥ 30 s before retrying (the server keeps working on, and bills, requests you gave up on); cache every response (ILAAS\_CACHE=1 with the client in helpers/ilaas\_client.py), because temperature 0 is not deterministic and the cache lets you rerun offline. Models, limits and error messages: API\_NOTES.md in the repository. Base URL: `https://llm.aristote.education/v1`; put your key in `ILAAS_API_KEY`. Each team gets a key at the event; don't commit it. Example — list the available models:

  ```bash
  curl -sS https://llm.aristote.education/v1/models \
    -H "Authorization: Bearer $ILAAS_API_KEY"
  ```

**Default:** lexical retrieval on your laptop.

**Optional extra:** the LLM API, only if the service is live. If it is unavailable, the lexical baseline alone produces the full deliverable.

## Deliverable Specification

Submit **one file: your catalogue**, by the end of Block 4, on Codabench: <https://www.codabench.org/competitions/18675/>.

The catalogue is organised as task → papers, following the schema below: one row per task–paper pair, as CSV, as a JSON list of rows, or as JSON `{"tasks": [{…, "papers": [rows]}]}`. Check it with `python3 catalogue/validate.py your_catalogue.json` (add `--corpus` to check the quotes against the papers).

**How to submit** (also under "How to submit" on Codabench):

1. Export your catalogue in the handout format: one row per task–paper pair, as `.json` or `.csv`.
2. Put that one file in a `.zip`, with the file at the top level of the zip.
3. Upload the zip under **My Submissions**. Up to 5 submissions per day.

The result page shows your score, which of your tasks matched a reference task, and rows with problems (missing paper id, missing evidence, an id that is not in the corpus). It does not show the reference.

### Required catalogue fields (every row)

| Field | Meaning | Notes |
| --- | --- | --- |
| `field` | Application area / Scientific field (e.g. `pp`, `PbPb`, `GW`, `multimessenger`) | Do not collapse pp/PbPb |
| `fm_scope` | Intended foundation-model input/context | E.g. `particle-level jet constituents` |
| `task_id` | Stable short identifier for the task | E.g. `jet_classification_pp` |
| `task_label` | Human-readable task name |  |
| `task_type` | `classification` / `regression` / `reconstruction` / `detection` / `parameter estimation` / `generation` / `other` |  |
| `observable_or_target` | What is predicted or measured | Use `unknown` only when source is genuinely insufficient |
| `input_modality` | What goes in |  |
| `study_type` | `experimental` / `phenomenological` / `methodological` / `unspecified` | Where relevant; otherwise `unspecified` |
| `paper_id` | Paper identifier: INSPIRE id (`INSPIRE:1234567`) or arXiv id (`arXiv:2101.01234`) | These are the ids Codabench finds in the corpus |
| `paper_url` | Direct link to paper |  |
| `study_group_id` | Groups duplicate versions of the same study |  |
| `match_rationale` | Why this study supports a potential benchmark for this FM scope | Required for every row |
| `evidence_text` | Quoted passage supporting the match | Required for `supported` decisions |
| `evidence_location` | Where in the paper (abstract / section / page) |  |
| `decision` | `candidate` / `supported` / `uncertain` / `excluded` |  |
| `review_status` | `automated_proposal` or `human_reviewed` |  |
| `retrieval_method` | Retrieval method (e.g. `lexical`, `LLM-assisted`) and the query family that produced this row | For reproducibility |

### Optional enrichment fields

`dataset_status` · `dataset_url` · `model_code_status` · `model_code_url` · `metric` · `reported_result` · `evaluation_setting`

For availability fields use: `not_checked` / `mentioned_unverified` / `verified_accessible` / `restricted` / `unavailable_confirmed`. **Never write `unavailable` just because the abstract is silent.**

Also optional: `team` (your team name), `setting` (scientific setting: system, energy, experiment) and `recid` (INSPIRE record id; makes the quote check exact).

### Example row (illustrative only — not from the reference set)

```
field: pp
fm_scope: particle-level jet constituents
task_id: jet_classification_pp
task_label: Jet tagging / classification in pp collisions
task_type: classification
observable_or_target: jet label (quark/gluon or flavour)
input_modality: particle-level jet constituents
study_type: methodological
paper_id: arXiv:XXXX.XXXXX
paper_url: https://arxiv.org/abs/XXXX.XXXXX
study_group_id: sg_XXXXXXX
match_rationale: Paper trains and evaluates a classifier on particle-level jet inputs with a jet label target, directly establishing the evaluation setting.
evidence_text: "We evaluate [model] on a jet tagging benchmark using particle-level inputs…"
evidence_location: abstract
decision: supported
review_status: human_reviewed
retrieval_method: lexical, query family: jet AND (tagging OR classification)
```

> **One row per task–paper relationship.** One study can appear in multiple rows if it genuinely supports multiple distinct tasks. Different versions of the same study are one row, deduplicated via `study_group_id`.

## Evaluation Criteria

### Minimum success criterion

> **At least two distinct downstream/benchmark tasks, each supported by one or two correctly matched papers/studies.**

This is a completion threshold, not a retrieval-accuracy target. Focus on getting this right before expanding scope.

### What makes a match correct

A counted match needs an identifiable source (paper ID + link), a quoted passage showing the study performs the task (not just a topical association), and a defensible rationale linking it to a potential benchmark for the stated FM scope. It is **not counted** if it rests on a hallucinated citation, a duplicate paper version, or a paper that only mentions related work.

### What you will be evaluated on

| Criterion | How it is assessed |
| --- | --- |
| Verified task–paper matches | Checked against organizer reference cases; additional valid matches accepted after manual validation |
| Distinct tasks supported | Count of tasks with at least one correct match |
| Evidence correctness | Passages exist in the text and genuinely support the interpretation |

### What is not penalized

- Discovering valid task–paper matches not in the organizer's reference list (they count after validation)
- Leaving enrichment fields (`dataset_status`, etc.) as `not_checked`

## Fallbacks and Troubleshooting

| Problem | Fallback action | Output preserved |
| --- | --- | --- |
| Corpus too large for your laptop | Restrict to abstracts only (no full text) and limit to a single decade of records | Same task definitions, ranked candidates, catalogue schema |
| LLM API service unavailable or quota exhausted | Replay your saved responses (ILAAS\_CACHE=1 with the client in helpers/ilaas\_client.py works without key or network); verify the remaining papers manually | Human-verified matches; no unsupported claims of live inference |
| Full text needed but unavailable in corpus | Open a freely accessible version manually (e.g. arXiv or open-access publisher page); retain unresolved records as `uncertain` | Core matches where evidence is sufficient; enrichment stays optional |
| Classifier training unstable (too few labels) | Retain the lexical baseline — a stable baseline beats an unstable classifier | Same catalogue |

An `uncertain` record is a valid catalogue entry — never guess `supported` when the evidence is insufficient. A failed optional component (the LLM API) must not delay completing the lexical baseline and the core catalogue.

## Links

|  |  |
| --- | --- |
| **Repository** | lit2bench-mini-hackathon repository: suggested pipelines/, helpers/ (README), catalogue/ (format and validator) · <https://github.com/Johnny-lip/lit2bench-mini-hackathon/> |
| **Corpus and index** | corpus/README.md in the repository · download: <https://uni-bonn.sciebo.de/s/k3P5XLx4iRnj6JL> |
| **LLM API endpoint** | <https://llm.aristote.education/v1> · key: one per team, handed out at the event |
| **API notes** | API\_NOTES.md in the repository: models per step, settings, limits, error messages and fixes |

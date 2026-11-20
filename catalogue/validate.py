#!/usr/bin/env python3
"""Validate a lit2bench task catalogue in the handout format.

  python3 validate.py catalogue.json
  python3 validate.py catalogue.csv --corpus                 # + quote check against ../corpus
  python3 validate.py catalogue.json --corpus ../corpus/subsets/<name>

Format (handout, "Deliverable Specification"; schema.json): one row per task-paper pair, as CSV, as a
JSON list of rows, or as JSON {"tasks": [{..., "papers": [rows]}]}. In the tasks layout, fields set on
a task apply to every paper row of that task. These are the layouts the scorer reads.

Checks
  - every row has the required handout fields; evidence_text for every supported row
  - allowed values of task_type, study_type, decision, review_status and the availability fields
  - recid (optional) is an INSPIRE record id; paper_id looks like an arXiv id, DOI or INSPIRE key
  - no study twice in a task: non-excluded rows of one task with the same study_group_id or paper_id
  - minimum success criterion: at least two tasks, each with a row that is supported AND human_reviewed
    (the scorer's PASS rule)
  - warnings for things the scorer can't use: unknown field values, verified_accessible without a URL
This part needs only the standard library.

With --corpus every evidence_text is also checked against the paper itself: title, abstract and full
text are read from the downloaded corpus (or a subset saved by helpers/corpus_extract.py; default:
$LIT2BENCH_CORPUS or ../corpus) and compared with helpers/textutils.quote_match: LaTeX/MathML stripped,
case, whitespace, dashes and quote marks ignored, pieces joined with "..." accepted (each >= 4 words),
words split by a line break ("re- duction") joined. A supported row whose quote is not found is an
error. Papers are found by recid, or by an INSPIRE or arXiv paper_id / paper_url; a paper known only by
DOI or texkey is not checked (warning). The quote check needs pandas and zstandard
(pip install -r ../helpers/requirements.txt). Whether a match is scientifically correct is decided by
the organizers, not by this script.
"""

import argparse
import csv
import json
import os
import re
import sys

HELPERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "helpers")
sys.path.insert(0, HELPERS)
from textutils import quote_match, strip_latex  # noqa: E402  (standard library only)

REQUIRED = ["field", "fm_scope", "task_id", "task_label", "task_type", "observable_or_target", "input_modality",
            "study_type", "paper_id", "paper_url", "study_group_id", "match_rationale", "decision",
            "review_status", "retrieval_method"]
ALLOWED = {
    "task_type": {"classification", "regression", "reconstruction", "detection", "parameter estimation",
                  "generation", "other"},
    "study_type": {"experimental", "phenomenological", "methodological", "unspecified"},
    "decision": {"candidate", "supported", "uncertain", "excluded"},
    "review_status": {"automated_proposal", "human_reviewed"},
    "dataset_status": {"not_checked", "mentioned_unverified", "verified_accessible", "restricted",
                       "unavailable_confirmed"},
}
ALLOWED["model_code_status"] = ALLOWED["dataset_status"]
# field values the scorer recognises (case-insensitive); others are ignored by it
FIELD_NAMES = {"pp", "proton-proton", "proton proton", "pbpb", "pb-pb", "heavy-ion", "heavy ion", "heavy ions",
               "gw", "gravitational waves", "gravitational wave", "gravitational-wave", "mm", "multimessenger",
               "multi-messenger", "cosmology"}


# ---------------------------------------------------------------- reading
def load_rows(path):
    """(rows, team): flat row dicts with string values, from CSV, a JSON list of rows or {"tasks": [...]}."""
    team = ""
    if path.endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    else:
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            team = str(data.get("team") or "")
            rows = []
            for t in data["tasks"]:
                top = {k: v for k, v in t.items() if not isinstance(v, (list, dict))}
                for p in t.get("papers") or []:
                    if isinstance(p, dict):
                        rows.append({**top, **{k: v for k, v in p.items() if v not in ("", None)}})
        elif isinstance(data, list):
            rows = data
        else:
            raise SystemExit(f"{path}: not a catalogue layout. Use a JSON list of rows or "
                             '{"tasks": [{..., "papers": [rows]}]} (or CSV). A dict keyed by task_id is not read '
                             "by the scorer.")
    rows = [{k: ("" if v is None else str(v).strip()) for k, v in r.items() if k and not isinstance(v, (list, dict))}
            for r in rows if isinstance(r, dict)]
    return rows, team or next((r["team"] for r in rows if r.get("team")), "")


# ---------------------------------------------------------------- paper ids
_INSPIRE = re.compile(r"inspirehep\.net/(?:literature|record)/(\d+)|^(?:inspire(?:-hep)?|recid)[\s:_#-]*(\d+)$", re.I)
_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$|^arxiv[\s:]*(\S+)$|"
                    r"^(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[a-z]{2})?/\d{7}(?:v\d+)?)$", re.I)
_DOI = re.compile(r"doi\.org/10\.|^(?:doi[\s:]*)?10\.\d{4,9}/\S+$", re.I)
_TEXKEY = re.compile(r"^(?:(?:inspire|texkey)[\s:]+)?[a-z][\w.'+-]*:\d{4}[a-z]{2,3}$", re.I)


def arxiv_key(a):
    """'arXiv:1602.03837v2' -> '1602.03837'."""
    return re.sub(r"v\d+$", "", re.sub(r"^arxiv[\s:]*", "", str(a or "").strip(), flags=re.I))


def id_kind(s):
    """'inspire' / 'arxiv' / 'doi' / 'texkey' / None for a paper_id or paper_url."""
    s = (s or "").strip()
    for kind, rx in (("inspire", _INSPIRE), ("arxiv", _ARXIV), ("doi", _DOI), ("texkey", _TEXKEY)):
        if rx.search(s):
            return kind
    return None


def row_recid(r, by_arxiv):
    """INSPIRE recid of a row (str) from recid, paper_id or paper_url; None if it can't be looked up."""
    if r.get("recid", "").isdigit():
        return str(int(r["recid"]))
    for s in (r.get("paper_id", ""), r.get("paper_url", "")):
        m = _INSPIRE.search(s.strip())
        if m:
            return m.group(1) or m.group(2)
        m = _ARXIV.search(s.strip())
        if m:
            key = arxiv_key(next(g for g in m.groups() if g))
            if key in by_arxiv:
                return by_arxiv[key]
    return None


# ---------------------------------------------------------------- quote check
FOUND = ("exact", "normalized", "fragments")
_HYPHEN = re.compile(r"(\w)-\s+(\w)")           # "re- duction" (line-break hyphenation) -> "reduction"
_DASH = re.compile(r"\s*([-‐-―−])\s*")   # "star – black" vs "star–black"


def load_papers(root, rows):
    """({row index: recid}, {recid: [(location, text), ...]}) for the rows, read with helpers/corpus_extract.py."""
    try:
        from corpus_extract import iter_papers, select
    except ImportError as e:
        raise SystemExit(f"--corpus needs the helpers' packages ({e}): pip install -r {HELPERS}/requirements.txt")
    index = select(root=root)
    by_arxiv = {arxiv_key(a): r for a, r in zip(index.arxiv_id, index.recid) if a}
    want = {i: row_recid(r, by_arxiv) for i, r in enumerate(rows)}
    sel = index[index.recid.isin(set(want.values()) - {None})]
    print(f"reading {len(sel)} papers from {root} (meta + full text, can take a few minutes) ...", file=sys.stderr)
    text = {}
    for meta, ft in iter_papers(sel, root=root, progress=True):
        title = ((meta.get("titles") or [{}])[0]).get("title") or ""
        abstract = " ".join(a.get("value") or "" for a in meta.get("abstracts") or [])
        parts = [("title", title), ("abstract", abstract)]
        parts += [("full text", (s.get("heading") or "") + "\n" + (s.get("text") or ""))
                  for s in (ft or {}).get("sections") or []]
        text[str(meta["recid"])] = parts
    return want, text


def find_quote(quote, parts):
    """(match kind, location) of the quote in the paper's parts; ("not_found", "") if absent.
    Tried in turn: quote_match as is, line-break hyphenation joined, spaces around dashes removed."""
    q_dash = _DASH.sub(r"\1", quote)
    for loc, txt in parts:
        plain = strip_latex(txt)
        for q, t in ((quote, txt), (quote, _HYPHEN.sub(r"\1\2", plain)), (q_dash, _DASH.sub(r"\1", plain))):
            m = quote_match(q, t)
            if m in FOUND:
                return m, loc
    return "not_found", ""


def stated_location(s):
    """evidence_location -> 'title' / 'abstract' / 'full text', or None when it can't be compared."""
    s = (s or "").strip().lower()
    if s.startswith("title"):
        return "title"
    if s.startswith("abstract"):
        return "abstract"
    if re.match(r"(section|page|full ?text|body|introduction|results|conclusion|appendix|figure|table|fig\.)", s):
        return "full text"
    return None


# ---------------------------------------------------------------- checks
def validate(rows, corpus=None):
    errors, warnings = [], []
    if not rows:
        return ["no rows found: the file is empty or not one of the accepted layouts"], warnings, set()
    seen = {}
    for i, r in enumerate(rows):
        where = f"row {i + 1} ({r.get('task_id') or '?'} | {r.get('paper_id') or '?'})"
        missing = [k for k in REQUIRED if not r.get(k)]
        if missing:
            errors.append(f"{where}: missing {', '.join(missing)}")
        for k, allowed in ALLOWED.items():
            if r.get(k) and r[k] not in allowed:
                errors.append(f"{where}: {k} {r[k]!r} not in {sorted(allowed)}")
        dec = r.get("decision", "")
        if dec == "supported" and not r.get("evidence_text"):
            errors.append(f"{where}: supported row without evidence_text")
        if r.get("evidence_text") and not r.get("evidence_location"):
            warnings.append(f"{where}: evidence_text without evidence_location")
        if r.get("recid") and not r["recid"].isdigit():
            errors.append(f"{where}: recid {r['recid']!r} is not an INSPIRE record id (digits only)")
        if r.get("paper_id") and not id_kind(r["paper_id"]):
            warnings.append(f"{where}: paper_id {r['paper_id']!r} is not an arXiv id, DOI or INSPIRE key")
        if r.get("field") and r["field"].lower() not in FIELD_NAMES:
            warnings.append(f"{where}: field {r['field']!r} is not recognised by the scorer "
                            "(use pp, PbPb, GW, multimessenger or cosmology)")
        for st, url in (("dataset_status", "dataset_url"), ("model_code_status", "model_code_url")):
            if r.get(st) == "verified_accessible" and not r.get(url):
                warnings.append(f"{where}: {st} verified_accessible without {url} (earns no availability points)")
        if dec and dec != "excluded":
            for key in ("study_group_id", "paper_id"):
                k = (r.get("task_id"), key, r.get(key, "").lower())
                if k[2] and k in seen:
                    errors.append(f"{where}: same {key} as {seen[k]} in this task: one row per study "
                                  "(exclude the other versions)")
                seen.setdefault(k, where)

    labels = {}
    for r in rows:
        labels.setdefault(r.get("task_id"), set()).add(r.get("task_label", ""))
    for tid, ls in labels.items():
        if len(ls - {""}) > 1:
            warnings.append(f"task {tid}: {len(ls - {''})} different task_label values")

    if corpus:
        checked = [i for i, r in enumerate(rows) if r.get("evidence_text")]
        want, text = load_papers(corpus, [rows[i] for i in checked])
        counts = {}
        for n, i in enumerate(checked):
            r = rows[i]
            where = f"row {i + 1} ({r.get('task_id')} | {r.get('paper_id')})"
            rid = want[n]
            if rid is None:
                warnings.append(f"{where}: paper not found by recid, INSPIRE or arXiv id; quote not checked")
                continue
            if rid not in text:
                warnings.append(f"{where}: recid {rid} not in {corpus}, quote not checked")
                continue
            kind, loc = find_quote(r["evidence_text"], text[rid])
            counts[kind] = counts.get(kind, 0) + 1
            if kind not in FOUND:
                (errors if r.get("decision") == "supported" else warnings).append(
                    f"{where}: evidence_text not found in the paper")
            else:
                said = stated_location(r.get("evidence_location"))
                if said and said != loc:
                    warnings.append(f"{where}: quote is in the {loc}, not the {said}")
        print("quote check: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing checked"),
              file=sys.stderr)

    passed = {r["task_id"] for r in rows
              if r.get("task_id") and r.get("decision") == "supported" and r.get("review_status") == "human_reviewed"}
    return errors, warnings, passed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("catalogue")
    ap.add_argument("--corpus", nargs="?", const="", default=None, metavar="DIR",
                    help="check evidence quotes against the corpus (or a saved subset) in DIR; "
                         "without DIR: $LIT2BENCH_CORPUS or ../corpus")
    a = ap.parse_args()
    rows, team = load_rows(a.catalogue)
    corpus = None
    if a.corpus is not None:
        corpus = a.corpus or os.environ.get("LIT2BENCH_CORPUS") or os.path.join(HELPERS, "..", "corpus")
    errors, warnings, passed = validate(rows, corpus)
    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("error:", e)
    tasks = {r.get("task_id") for r in rows if r.get("task_id")}
    n_sup = sum(r.get("decision") == "supported" for r in rows)
    print(f"{team + ': ' if team else ''}{len(rows)} rows, {len(tasks)} tasks, {n_sup} supported rows; "
          f"{len(passed)} tasks with a supported + human_reviewed row; minimum criterion (2 such tasks): "
          f"{'met' if len(passed) >= 2 else 'NOT met'}; format: {'valid' if not errors else f'{len(errors)} errors'}")
    sys.exit(0 if not errors and len(passed) >= 2 else 1)


if __name__ == "__main__":
    main()

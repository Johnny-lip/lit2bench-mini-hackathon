"""Select papers by topic pool AND/OR INSPIRE category, stream them, and optionally save a subset.

Selection uses only the small index files (seconds); streaming reads the big meta / fulltext files once
per decade and json-decodes only the selected lines.

A saved subset has the SAME layout as the corpus, so every command (and the Python API) also streams
from a subset by pointing --root at it:
    <out>/index/index_<decade>.tsv.zst          filtered index rows
    <out>/<decade>/meta_<decade>.jsonl.zst      selected meta records (original lines, unchanged)
    <out>/<decade>/fulltext_<decade>.jsonl.zst  selected full texts (if --kind fulltext/both)
    <out>/manifest.json                          filters, counts, source, date

Command line (from helpers/; corpus in ../corpus or $LIT2BENCH_CORPUS):
  python corpus_extract.py --list-categories
  python corpus_extract.py --topic pp --category Experiment-HEP --count
  python corpus_extract.py --category "Data Analysis and Statistics" Computing --decade 2020s --print 5
  python corpus_extract.py --topic gw --category Instrumentation --has-fulltext --kind both --save
        -> ../corpus/subsets/topic-gw_cat-instrumentation_all-decades_ft/   (name from the filters; --out to choose)
  python corpus_extract.py --root ../corpus/subsets/topic-gw_cat-instrumentation_all-decades_ft --print 3
  python corpus_extract.py --root ../corpus/subsets/<name> --category Instrumentation --category-mode only --count

Filters (all optional, combined with AND):
  --topic T [T ...]        pp | pbpb | gw | mm | none (= papers without a topic)
  --topic-mode any|all|only  any (default): at least one of them; all: every one; only: no other topic
  --category C [C ...]     INSPIRE categories (case-insensitive; see --list-categories)
  --category-mode any|all|only
  --exclude-category C ...  drop papers carrying any of these
  --decade D [D ...]       pre1990 1990s 2000s 2010s 2020s (default: all)
  --has-abstract  --has-fulltext  --doc-type article thesis ...  --limit N

Python:
  from corpus_extract import select, iter_records, iter_papers
  sel = select(topics=["pp"], categories=["Experiment-HEP"], has_fulltext=True)   # pandas DataFrame (index rows)
  for rec in iter_records(sel, kind="meta"): ...            # dicts, decade by decade
  for meta, ft in iter_papers(sel): ...                    # meta + full text joined (ft None if absent)
  iter_records(select(root="../corpus/subsets/<name>"), ...)      # same, from a saved subset
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import zstandard

# the corpus in its local layout (index/, <decade>/meta_*.jsonl.zst, ...); see ../corpus/README.md
CORPUS = Path(os.environ.get("LIT2BENCH_CORPUS") or Path(__file__).resolve().parent.parent / "corpus")
DECADES = ["pre1990", "1990s", "2000s", "2010s", "2020s"]
TOPICS = ["pp", "pbpb", "gw", "mm"]
_RECID = re.compile(r'\{"recid": ?(\d+)')


# --------------------------------------------------------------------------- files
def _read_lines(path: Path):
    with path.open("rb") as fh:
        reader = zstandard.ZstdDecompressor(max_window_size=2**31).stream_reader(fh)
        yield from io.TextIOWrapper(reader, encoding="utf-8")


def _recid(line: str) -> str:
    m = _RECID.match(line)
    return m.group(1) if m else str(json.loads(line)["recid"])


def available_decades(root: Path) -> list[str]:
    found = [d for d in DECADES if (Path(root) / "index" / f"index_{d}.tsv.zst").exists()]
    if not found:
        raise SystemExit(f"no corpus index under {Path(root).resolve()}/index: download the corpus first "
                         "(see corpus/README.md) or point LIT2BENCH_CORPUS / --root to it")
    return found


def read_index(root: Path, decade: str) -> pd.DataFrame:
    path = Path(root) / "index" / f"index_{decade}.tsv.zst"
    with path.open("rb") as fh:
        text = io.TextIOWrapper(zstandard.ZstdDecompressor(max_window_size=2**31).stream_reader(fh), encoding="utf-8")
        df = pd.read_csv(text, sep="\t", dtype=str, keep_default_na=False, quoting=3)
    df["decade"] = decade                      # trust the file, not the column
    return df


def _set(s: str) -> frozenset:
    return frozenset(x.strip() for x in s.split("|") if x.strip())


# --------------------------------------------------------------------------- selection
def all_categories(root: Path = CORPUS, decades=None) -> pd.Series:
    cats = {}
    for d in decades or available_decades(root):
        for cs in read_index(root, d).inspire_categories.map(_set):
            for c in cs:
                cats[c] = cats.get(c, 0) + 1
    return pd.Series(cats).sort_values(ascending=False)


def _match(sets: pd.Series, wanted: set, mode: str) -> pd.Series:
    if mode == "any":
        return sets.map(lambda s: bool(s & wanted))
    if mode == "all":
        return sets.map(lambda s: wanted <= s)
    if mode == "only":
        return sets.map(lambda s: bool(s) and s <= wanted)
    raise ValueError(f"mode must be any|all|only, not {mode}")


def _canon_categories(names, known) -> list[str]:
    low = {k.lower(): k for k in known}
    out = []
    for n in names or []:
        if n.lower() not in low:
            close = [k for k in known if n.lower()[:4] in k.lower()]
            raise SystemExit(f"unknown INSPIRE category {n!r}. Did you mean: {close or sorted(known)}?")
        out.append(low[n.lower()])
    return out


def select(root: Path = CORPUS, decades=None, topics=None, topic_mode="any", categories=None,
           category_mode="any", exclude_categories=None, has_abstract=False, has_fulltext=False,
           doc_types=None, limit=None) -> pd.DataFrame:
    """Return the index rows of all matching papers (columns of index_*.tsv + topic_set, category_set)."""
    root = Path(root)
    decades = decades or available_decades(root)
    missing = [d for d in decades if d not in available_decades(root)]
    if missing:
        raise SystemExit(f"no index for {missing} under {root}")
    df = pd.concat([read_index(root, d) for d in decades], ignore_index=True)
    df["topic_set"] = df.topics.map(_set)
    df["category_set"] = df.inspire_categories.map(_set)
    keep = pd.Series(True, index=df.index)
    if topics:
        t = set(topics)
        bad = t - set(TOPICS) - {"none"}
        if bad:
            raise SystemExit(f"unknown topic(s) {bad}; use {TOPICS} or 'none'")
        no_topic = df.topic_set.map(len) == 0
        m = _match(df.topic_set, t - {"none"}, topic_mode) if t - {"none"} else pd.Series(False, index=df.index)
        keep &= (m | no_topic) if "none" in t else m
    known = set().union(*df.category_set) if len(df) else set()
    if categories:
        keep &= _match(df.category_set, set(_canon_categories(categories, known)), category_mode)
    if exclude_categories:
        ex = set(_canon_categories(exclude_categories, known))
        keep &= ~df.category_set.map(lambda s: bool(s & ex))
    if has_abstract:
        keep &= df.has_abstract == "1"
    if has_fulltext:
        keep &= df.fulltext == "1"
    if doc_types:
        keep &= df.document_type.isin(doc_types)
    sel = df[keep]
    return sel.head(limit) if limit else sel


# --------------------------------------------------------------------------- streaming
def iter_lines(sel: pd.DataFrame, kind: str = "meta", root: Path = CORPUS, progress=False):
    """Yield (decade, raw json line) of the selected papers from meta_ or fulltext_ files."""
    assert kind in ("meta", "fulltext")
    for decade, g in sel.groupby("decade", sort=False):
        want = set(g.recid) if kind == "meta" else set(g[g.fulltext == "1"].recid)
        path = Path(root) / decade / f"{kind}_{decade}.jsonl.zst"
        if not want:
            continue
        if not path.exists():
            print(f"! {path} missing, skipped", file=sys.stderr)
            continue
        t0, found, seen = time.time(), 0, 0
        for line in _read_lines(path):
            seen += 1
            if _recid(line) in want:
                found += 1
                yield decade, line
                if found == len(want):        # everything found: stop reading this file
                    break
            if progress and seen % 200_000 == 0:
                print(f"  {kind} {decade}: {seen:,} lines read, {found:,}/{len(want):,} found", file=sys.stderr)
        if progress:
            print(f"  {kind} {decade}: {found:,}/{len(want):,} found in {time.time() - t0:.0f}s", file=sys.stderr)


def iter_records(sel: pd.DataFrame, kind: str = "meta", root: Path = CORPUS, progress=False):
    """Yield decoded records (dicts) of the selected papers."""
    for _, line in iter_lines(sel, kind, root, progress):
        yield json.loads(line)


def iter_papers(sel: pd.DataFrame, root: Path = CORPUS, progress=False):
    """Yield (meta, fulltext or None) per selected paper, reading both files of a decade in one pass.

    Both files are in index order (fulltext only for papers with fulltext == 1), so they are merged by
    position. Full texts that arrive out of order are buffered, so the result is correct either way;
    a warning tells how many were missing.
    """
    for decade in sel.decade.unique():
        one = sel[sel.decade == decade]
        ft_iter = iter_lines(one, "fulltext", root)
        buf, missing = {}, 0
        has_ft = set(one[one.fulltext == "1"].recid)
        for _, mline in iter_lines(one, "meta", root, progress):
            meta = json.loads(mline)
            rid = str(meta["recid"])
            ft = None
            if rid in has_ft:
                while rid not in buf:
                    nxt = next(ft_iter, None)
                    if nxt is None:
                        break
                    buf[_recid(nxt[1])] = nxt[1]
                line = buf.pop(rid, None)
                if line is None:
                    missing += 1
                else:
                    ft = json.loads(line)
            yield meta, ft
        if missing or buf:
            print(f"! {decade}: {missing} full texts not found, {len(buf)} out of order", file=sys.stderr)


# --------------------------------------------------------------------------- saving
def subset_name(args) -> str:
    slug = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    parts = []
    if args.topic:
        parts.append(f"topic{'' if args.topic_mode == 'any' else '-' + args.topic_mode}-" + "+".join(args.topic))
    if args.category:
        parts.append(f"cat{'' if args.category_mode == 'any' else '-' + args.category_mode}-"
                     + "+".join(slug(c) for c in args.category))
    if args.exclude_category:
        parts.append("notcat-" + "+".join(slug(c) for c in args.exclude_category))
    if args.doc_type:
        parts.append("type-" + "+".join(slug(t) for t in args.doc_type))
    parts.append("-".join(args.decade) if args.decade else "all-decades")
    if args.has_abstract:
        parts.append("abs")
    if args.has_fulltext:
        parts.append("ft")
    if args.limit:
        parts.append(f"first{args.limit}")
    name = "_".join(parts)
    if len(name) > 120:
        import hashlib
        name = name[:100] + "_" + hashlib.sha1(name.encode()).hexdigest()[:8]
    return name


def save_subset(sel: pd.DataFrame, out: Path, kinds, root: Path, filters: dict, level: int = 10):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} exists and is not empty; choose another --out or delete it")
    (out / "index").mkdir(parents=True, exist_ok=True)
    cols = [c for c in sel.columns if c not in ("topic_set", "category_set")]
    counts = {}
    cctx = zstandard.ZstdCompressor(level=level, threads=-1)
    for decade, g in sel.groupby("decade", sort=False):
        with (out / "index" / f"index_{decade}.tsv.zst").open("wb") as fh, cctx.stream_writer(fh) as w:
            w.write(g[cols].to_csv(sep="\t", index=False).encode())
        counts[decade] = {"index": len(g)}
    for kind in kinds:
        for decade, g in sel.groupby("decade", sort=False):
            (out / decade).mkdir(exist_ok=True)
            path = out / decade / f"{kind}_{decade}.jsonl.zst"
            n = 0
            with path.open("wb") as fh, cctx.stream_writer(fh) as w:
                for _, line in iter_lines(g, kind, root, progress=True):
                    w.write(line.encode() if line.endswith("\n") else (line + "\n").encode())
                    n += 1
            counts[decade][kind] = n
    manifest = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source_root": str(Path(root).resolve()),
                "filters": filters, "papers": len(sel), "counts": counts,
                "layout": "same as the corpus: index/index_<decade>.tsv.zst, <decade>/{meta,fulltext}_<decade>.jsonl.zst"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


# --------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=CORPUS, help="corpus or saved subset (default: ../corpus or $LIT2BENCH_CORPUS)")
    ap.add_argument("--decade", nargs="+", choices=DECADES)
    ap.add_argument("--topic", nargs="+")
    ap.add_argument("--topic-mode", default="any", choices=["any", "all", "only"])
    ap.add_argument("--category", nargs="+")
    ap.add_argument("--category-mode", default="any", choices=["any", "all", "only"])
    ap.add_argument("--exclude-category", nargs="+")
    ap.add_argument("--has-abstract", action="store_true")
    ap.add_argument("--has-fulltext", action="store_true")
    ap.add_argument("--doc-type", nargs="+", help="e.g. article thesis 'conference paper' review note")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--kind", default="meta", choices=["meta", "fulltext", "both"],
                    help="what to stream / save (default meta)")
    ap.add_argument("--count", action="store_true", help="only print counts from the index (no streaming)")
    ap.add_argument("--list-categories", action="store_true")
    ap.add_argument("--print", type=int, metavar="N", help="stream and print the first N papers")
    ap.add_argument("--save", action="store_true", help="save the subset under <corpus>/subsets/<auto name>")
    ap.add_argument("--out", type=Path, help="save the subset to this directory (implies --save)")
    args = ap.parse_args()

    if args.list_categories:
        print(all_categories(args.root, args.decade).to_string())
        return
    t0 = time.time()
    sel = select(args.root, args.decade, args.topic, args.topic_mode, args.category, args.category_mode,
                 args.exclude_category, args.has_abstract, args.has_fulltext, args.doc_type, args.limit)
    print(f"selected {len(sel):,} papers from {args.root} in {time.time() - t0:.1f}s "
          f"(with abstract {int((sel.has_abstract == '1').sum()):,}, with full text {int((sel.fulltext == '1').sum()):,})")
    print("  per decade: " + ", ".join(f"{d} {n:,}" for d, n in sel.decade.value_counts().reindex(DECADES).dropna().astype(int).items()))
    tc = sel.topics.replace("", "(none)").value_counts().head(8)
    print("  topics:     " + ", ".join(f"{k} {v:,}" for k, v in tc.items()))
    cc = pd.Series([c for s in sel.category_set for c in s]).value_counts().head(8) if len(sel) else pd.Series(dtype=int)
    print("  categories: " + ", ".join(f"{k} {v:,}" for k, v in cc.items()))
    if args.count or not len(sel):
        return

    if args.print:
        head = sel.head(args.print)
        if args.kind == "both":
            for meta, ft in iter_papers(head, args.root):
                title = ((meta.get("titles") or [{}])[0]).get("title", "")
                abstract = ((meta.get("abstracts") or [{}])[0]).get("value", "")
                print(f"\n{meta['recid']} | {title[:100]}\n  abstract: {abstract[:200]}"
                      f"\n  full text: {len(ft['sections']) if ft else 0} sections")
        else:
            for rec in iter_records(head, args.kind, args.root):
                title = rec.get("title") or ((rec.get("titles") or [{}])[0]).get("title", "")
                extra = (f"{len(rec.get('sections') or [])} sections" if args.kind == "fulltext" else
                         ", ".join(c.get("term", "") for c in rec.get("inspire_categories") or []))
                print(f"{rec['recid']} | {title[:100]} | {extra}")

    if args.save or args.out:
        out = args.out or (CORPUS / "subsets" / subset_name(args))
        kinds = ["meta", "fulltext"] if args.kind == "both" else [args.kind]
        filters = {k: v for k, v in vars(args).items() if k in ("decade", "topic", "topic_mode", "category",
                   "category_mode", "exclude_category", "has_abstract", "has_fulltext", "doc_type", "limit") and v}
        t0 = time.time()
        m = save_subset(sel, out, kinds, args.root, filters)
        size = sum(f.stat().st_size for f in Path(out).rglob("*") if f.is_file())
        print(f"saved {m['papers']:,} papers ({', '.join(kinds)}) to {out} in {time.time() - t0:.0f}s, {size / 1e6:.1f} MB")
        print(f"stream it with: python corpus_extract.py --root {out} --print 5")


if __name__ == "__main__":
    main()

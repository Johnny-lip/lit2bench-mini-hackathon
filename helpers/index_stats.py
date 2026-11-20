"""Statistics over all corpus index files (corpus/index/index_*.tsv.zst). No API calls.

Prints, for all decades together and per decade:
  - records, records WITHOUT a topic, records without an INSPIRE category, records with an abstract
  - every topic / category table has a 'with abstract' column
  - records per topic and per topic combination (exact set of topics on the record)
  - records per INSPIRE category and per category combination (duplicates inside a record removed)
  - a cross-check of the topic counts against index/summary_all.json.zst
usage:
  python index_stats.py                 # all index files
  python index_stats.py --top 50        # longer combination tables (default 25)
  python index_stats.py --decade 2020s  # one decade only
  python index_stats.py --csv logs/index_stats   # also write the full tables as CSV
"""
import argparse
import io
import json
from collections import Counter
from pathlib import Path

import pandas as pd
import zstandard

from corpus_extract import CORPUS  # noqa: E402

INDEX = CORPUS / "index"
TOPIC_ORDER = ["pp", "pbpb", "gw", "mm"]
DECADES = ["pre1990", "1990s", "2000s", "2010s", "2020s"]


def read_index(path):
    with path.open("rb") as fh:
        text = zstandard.ZstdDecompressor(max_window_size=2**31).stream_reader(fh)
        return pd.read_csv(io.TextIOWrapper(text, encoding="utf-8"), sep="\t", dtype=str, keep_default_na=False,
                           usecols=["recid", "decade", "topics", "inspire_categories", "has_abstract", "fulltext"], quoting=3)


def split_set(s, order=None):
    """'a|b|a' -> canonical tuple ('a', 'b'); '' -> ()."""
    items = {x.strip() for x in s.split("|") if x.strip()}
    key = (lambda x: (order.index(x) if x in order else len(order), x)) if order else None
    return tuple(sorted(items, key=key))


def table(counter, total, top=None, label="value", abstracts=None):
    """abstracts: optional Counter with the number of papers WITH an abstract per key."""
    rows = [(" + ".join(k) if isinstance(k, tuple) else k, n, 100 * n / total)
            + ((abstracts.get(k, 0),) if abstracts is not None else ()) for k, n in counter.most_common(top)]
    cols = [label, "papers", "% of records"] + (["with abstract"] if abstracts is not None else [])
    return pd.DataFrame(rows, columns=cols).round({"% of records": 2})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25, help="rows shown for combination tables")
    ap.add_argument("--decade", choices=DECADES)
    ap.add_argument("--csv", type=Path, help="directory to write the full tables to")
    args = ap.parse_args()
    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 90)

    files = sorted(INDEX.glob("index_*.tsv.zst"), key=lambda p: DECADES.index(p.stem.split("_")[1].split(".")[0]))
    if args.decade:
        files = [f for f in files if args.decade in f.name]
    frames = []
    for f in files:
        d = read_index(f)
        print(f"read {f.name}: {len(d):,} records")
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    n = len(df)

    df["topic_set"] = df.topics.map(lambda s: split_set(s, TOPIC_ORDER))
    df["cat_set"] = df.inspire_categories.map(split_set)
    dup = df.recid.duplicated().sum()

    # ------------------------------------------------------------- overview per decade
    print("\n=== Overview")
    ov = df.groupby("decade", sort=False).agg(
        records=("recid", "size"),
        no_topic=("topic_set", lambda s: int((s.map(len) == 0).sum())),
        several_topics=("topic_set", lambda s: int((s.map(len) > 1).sum())),
        no_inspire_category=("cat_set", lambda s: int((s.map(len) == 0).sum())),
        several_categories=("cat_set", lambda s: int((s.map(len) > 1).sum())),
        with_abstract=("has_abstract", lambda s: int((s == "1").sum())),
        with_fulltext=("fulltext", lambda s: int((s == "1").sum())))
    ov.loc["ALL"] = ov.sum()
    for col in ("no_topic", "no_inspire_category", "with_abstract"):
        ov[f"{col} %"] = (100 * ov[col] / ov.records).round(1)
    print(ov.to_string())
    print(f"duplicate recids across files: {dup}")
    no_topic = int((df.topic_set.map(len) == 0).sum())
    print(f"\n>>> {no_topic:,} of {n:,} papers ({100 * no_topic / n:.1f} %) have NO topic; "
          f"{n - no_topic:,} have at least one.")

    # ------------------------------------------------------------- topics
    single = Counter(t for ts in df.topic_set for t in ts)
    print("\n=== Papers per topic (a paper with several topics counts for each)")
    per_dec = pd.DataFrame({dec: Counter(t for ts in g.topic_set for t in ts)
                            for dec, g in df.groupby("decade", sort=False)}).fillna(0).astype(int)
    per_dec["ALL"] = per_dec.sum(axis=1)
    per_dec["only this topic"] = [int((df.topic_set == (t,)).sum()) for t in per_dec.index]
    per_dec["with abstract"] = [int(((df.has_abstract == "1") & df.topic_set.map(lambda s, t=t: t in s)).sum())
                                for t in per_dec.index]
    per_dec["with fulltext"] = [int(((df.fulltext == "1") & df.topic_set.map(lambda s, t=t: t in s)).sum())
                                for t in per_dec.index]
    print(per_dec.loc[[t for t in TOPIC_ORDER if t in per_dec.index] +
                      [t for t in per_dec.index if t not in TOPIC_ORDER]].to_string())

    has_abs = df.has_abstract == "1"
    combos = Counter(ts for ts in df.topic_set if ts)
    combos_abs = Counter(ts for ts in df.topic_set[has_abs] if ts)
    print(f"\n=== Papers per topic combination (exact set; {len(combos)} combinations, papers without topic left out)")
    print(table(combos, n, label="topics", abstracts=combos_abs).to_string(index=False))

    # cross-check against the shipped summary
    summ = INDEX / "summary_all.json.zst"
    if summ.exists() and not args.decade:
        s = json.loads(zstandard.ZstdDecompressor().decompress(summ.read_bytes(), max_output_size=10**7))
        chk = [(t, s.get(f"topic_{t}"), single.get(t, 0)) for t in TOPIC_ORDER]
        ok = all(a == b for _, a, b in chk) and s.get("records") == n
        print(f"\ncross-check with summary_all.json: records {s.get('records'):,} vs {n:,}; "
              + ", ".join(f"{t} {a} vs {b}" for t, a, b in chk) + ("  -> OK" if ok else "  -> MISMATCH"))

    # ------------------------------------------------------------- INSPIRE categories
    cats = Counter(c for cs in df.cat_set for c in cs)
    print(f"\n=== Papers per INSPIRE category ({len(cats)} categories; a paper counts for each of its categories)")
    cat_dec = pd.DataFrame({dec: Counter(c for cs in g.cat_set for c in cs)
                            for dec, g in df.groupby("decade", sort=False)}).fillna(0).astype(int)
    cat_dec["ALL"] = cat_dec.sum(axis=1)
    cat_dec["% of records"] = (100 * cat_dec.ALL / n).round(2)
    cat_dec["only this category"] = [int((df.cat_set == (c,)).sum()) for c in cat_dec.index]
    cats_abs = Counter(c for cs in df.cat_set[has_abs] for c in cs)
    cat_dec["with abstract"] = [cats_abs.get(c, 0) for c in cat_dec.index]
    print(cat_dec.sort_values("ALL", ascending=False).to_string())

    cat_combos = Counter(cs for cs in df.cat_set if cs)
    cat_combos_abs = Counter(cs for cs in df.cat_set[has_abs] if cs)
    print(f"\n=== Papers per INSPIRE category combination (top {args.top} of {len(cat_combos)} combinations)")
    print(table(cat_combos, n, args.top, label="inspire_categories", abstracts=cat_combos_abs).to_string(index=False))

    # topic x category: which categories do the topic papers carry?
    print("\n=== INSPIRE categories of papers per topic (top 6 each)")
    for t in [t for t in TOPIC_ORDER if t in single]:
        sub = df[df.topic_set.map(lambda s, t=t: t in s)]
        c = Counter(x for cs in sub.cat_set for x in cs)
        none = int((sub.cat_set.map(len) == 0).sum())
        print(f"  {t:5} ({len(sub):,}): " + ", ".join(f"{k} {v:,}" for k, v in c.most_common(6))
              + f"; no category {none:,}")

    if args.csv:
        args.csv.mkdir(parents=True, exist_ok=True)
        ov.to_csv(args.csv / "overview.csv")
        per_dec.to_csv(args.csv / "topics.csv")
        table(combos, n, label="topics", abstracts=combos_abs).to_csv(args.csv / "topic_combinations.csv", index=False)
        cat_dec.sort_values("ALL", ascending=False).to_csv(args.csv / "inspire_categories.csv")
        table(cat_combos, n, label="inspire_categories", abstracts=cat_combos_abs).to_csv(args.csv / "inspire_category_combinations.csv", index=False)
        print(f"\nfull tables written to {args.csv}/")


if __name__ == "__main__":
    main()

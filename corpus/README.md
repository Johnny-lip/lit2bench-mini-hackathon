# Corpus (not in git)

Download the corpus into this folder before running the helpers.

Download: https://uni-bonn.sciebo.de/s/k3P5XLx4iRnj6JL

The scripts in `helpers/` expect this layout (compressed, with index files):

```
corpus/
  index/index_<decade>.tsv.zst              one row per record: recid, title, topics, inspire_categories,
                                            has_abstract, fulltext, ...  (54 MB in total)
  index/summary_<decade>.json.zst, summary_all.json.zst
  <decade>/meta_<decade>.jsonl.zst          full INSPIRE records
  <decade>/fulltext_<decade>.jsonl.zst      extracted full texts
  subsets/                                  written by corpus_extract.py --save
```

Decades: `pre1990`, `1990s`, `2000s`, `2010s`, `2020s`. Whole corpus about 23 GB
(2020s 9.4 GB, 2010s 8.5 GB, 2000s 3.7 GB, 1990s 1.2 GB, pre1990 0.6 GB). One decade is enough for most work.

The topic pools (`pp`, `pbpb`, `gw`, `mm`) exist only in the `topics` column of the index files.
93.8 % of all records have no topic, so selecting by INSPIRE category reaches far more papers.

Elsewhere: set `LIT2BENCH_CORPUS=/path/to/corpus` or pass `--root` to `corpus_extract.py`.

# Corpus (not in git)

Download the corpus into this folder before running the helpers.

Download: https://uni-bonn.sciebo.de/s/k3P5XLx4iRnj6JL

The scripts in `helpers/` expect this layout (compressed, with index files):

```
corpus/
  index/index_<decade>.tsv.zst              one row per record: recid, title, topics, inspire_categories,
                                            has_abstract, fulltext, ...  (54 MiB in total)
  index/summary_<decade>.json.zst, summary_all.json.zst
  <decade>/meta_<decade>.jsonl.zst          full INSPIRE records
  <decade>/fulltext_<decade>.jsonl.zst      extracted full texts
  subsets/                                  written by corpus_extract.py --save
```

The five periods are `pre1990`, `1990s`, `2000s`, `2010s` and `2020s`. The compressed
indexes and metadata together take about **10 GiB**. Including full text brings the
total to about **23 GiB**.

For Tuesday’s first field search, download `index/index_<decade>.tsv.zst` and
`<decade>/meta_<decade>.jsonl.zst` for **all five periods**. Keep the files compressed.
The metadata supplies titles and abstracts. Add a period’s `fulltext_<decade>.jsonl.zst`
when you need the text of a shortlisted paper. If your laptop can handle only one
decade, use its files and record the period in your query log.

The topic pools (`pp`, `pbpb`, `gw`, `mm`) exist only in the `topics` column of the index files.
93.8 % of all records have no topic, so selecting by INSPIRE category reaches far more papers.

Elsewhere: set `LIT2BENCH_CORPUS=/path/to/corpus` or pass `--root` to `corpus_extract.py`.

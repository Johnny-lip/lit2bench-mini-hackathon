# Helpers

Shared code for your own scripts. Needs the corpus in
`../corpus` (see `../corpus/README.md`) and, for live API calls, the key:
`export ILAAS_API_KEY=...` (endpoint `https://llm.aristote.education/v1`, OpenAI-compatible).

```
pip install -r requirements.txt
```

Use them from another folder with `sys.path.insert(0, "<path to>/helpers")`. The API call log and cache go to
`helpers/logs/` (not in git).

## Files

| file | what it does |
|---|---|
| `corpus_extract.py` | Select papers by topic pool and/or INSPIRE category (seconds, from the index), stream meta + full text, save a subset with the corpus layout |
| `index_stats.py` | Counts per topic, INSPIRE category and their combinations (no API) |
| `ilaas_client.py` | API client: retries, response cache (`ILAAS_CACHE=1`; one folder per endpoint: `logs/cache/chat/`, `rerank/`, `embeddings/`), budget headers, call log in `logs/` without paper text |
| `api_errors.py` | Turns API errors into a short cause and fix (used by the client and the monitor) |
| `api_monitor.py` | Run in a second terminal: calls, errors with fixes, tokens, budget (`python3 api_monitor.py`) |
| `textutils.py` | `strip_latex()` before prompting, `quote_match()` to check evidence quotes |
| `review_page.py` | Human review of a catalogue: `python3 review_page.py catalogue.json` writes a self-contained HTML page (one card per task–paper row, Supported / Uncertain / Excluded, export of the decisions); `--apply review_decisions.csv` writes them back with `review_status = human_reviewed` |

## Rules for the API

- At most 4 parallel requests per team, always with retry (the client already retries).
- Default model `gemma-4-31b` with thinking off. Send the abstract plus 2–3 passages, not whole papers;
  at most 2 parallel requests for calls with long texts.
- `temperature=0` is not deterministic, so keep the cache: reruns then give the same answers, also offline.
- Models per step, limits and every error message with its fix: [`../API_NOTES.md`](../API_NOTES.md).

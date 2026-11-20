# LLM API notes

Reference for the optional LLM API. The handout has the essentials; this page is for choosing settings and
fixing errors. The numbers were measured on 10–20 Nov 2026 (several thousand test calls, including load tests
that simulated up to 40 teams at once).

The API is optional. The lexical baseline alone produces the full deliverable.

## Access

- Endpoint: `https://llm.aristote.education/v1`, OpenAI-compatible (LiteLLM proxy). Any OpenAI client works
  with `base_url` set to it.
- Key: `export ILAAS_API_KEY=...` [KEY DISTRIBUTION AND QUOTA TBD]. The budget belongs to the key and is shared
  by everyone using it; every response reports it in the headers `x-litellm-key-spend` and
  `x-litellm-key-max-budget`.
- Check:

  ```
  curl -sS https://llm.aristote.education/v1/models -H "Authorization: Bearer $ILAAS_API_KEY"
  ```

  `/models` can list a model that does not answer (see `qwen-3.6` below). One call per model shows what
  really works.

## Starter client

`helpers/ilaas_client.py` handles retries, the response cache and logging for you:

```python
from ilaas_client import ILaaSClient
c = ILaaSClient(tag="screening-v2", use_cache=True)    # tag: local label for the log (api_monitor.py --tag); cache: or ILAAS_CACHE=1
r = c.chat("gemma-4-31b", [{"role": "user", "content": prompt}],
           thinking=False, temperature=0, max_tokens=1500, response_format={"type": "json_object"})
c.rerank("bge-reranker-v2-m3", "short task query", passages, top_n=3)
c.embed("bge-m3", texts)
```

- Every failure prints its cause and a fix (`! <type>: <fix>`).
- `python3 helpers/api_monitor.py` in a second terminal shows calls, errors with fixes, tokens and budget;
  `--tag screening` limits it to calls whose tag starts with `screening`.
- The call log `helpers/logs/api_calls.jsonl` holds no paper text (only lengths and hashes), and rerank
  results never keep the passages the reranker echoes back, so logs and caches can be shared. If you
  use your own client, note that `/rerank` returns every document you send.

Plain OpenAI SDK (`pip install openai`), if you prefer it:

```python
import os
from openai import OpenAI
client = OpenAI(base_url="https://llm.aristote.education/v1", api_key=os.environ["ILAAS_API_KEY"])
client.chat.completions.create(model="gemma-4-31b", messages=msgs, temperature=0, max_tokens=1500,
                               response_format={"type": "json_object"},
                               extra_body={"chat_template_kwargs": {"enable_thinking": False}})
```

## Which model for what

| Use | Model | Settings and notes |
|---|---|---|
| LLM check, query terms, screening, verification | **`gemma-4-31b`** | **Default.** `temperature=0`, JSON mode, thinking off, `max_tokens` ≥ 1000 for JSON answers. ~1–3 s per abstract; a full pipeline run (~400 calls: screening, verification, enrichment) in ~4 min at 4 workers. The most generous judge of the tested models: it accepts more borderline papers, so check its `supported` rows by hand. Send passages, not whole papers (below). |
| Second opinion for screening | same model with a second prompt, or `qwen-3.8-27b` | qwen-3.8 judges about as well and a bit stricter, but **reasons by default** (switch thinking off, or the answer can come back empty) and its speed depends on the load: the same run took 3 min one day and 38 min another, with single calls waiting up to 4 min. Fine for comparisons, not for a whole team's run. |
| Re-ranking candidates, finding passages in a full text | `bge-reranker-v2-m3` (`/rerank`) | **Always send `top_n`**. Query: a short noun phrase "task + objects + setting", not a question. ≤ 8 192 tokens per document; tested up to 400 documents per call. |
| Similar papers, duplicate detection | `bge-m3` (`/embeddings`) | 1024-dim. ≤ 8 192 tokens per input (truncate to ~8 000 characters: one long input fails the whole batch). Cosine ≥ 0.95 of title + abstract = same study; 0.90–0.95: check by hand. |
| Avoid | `llama-3.1-8b` | Weakest: found the fewest real papers, marks data as "available" for almost every paper, poor query terms; fails on long prompts. |
| Unavailable | `qwen-3.6-35b-instruct`, `qwen-3.6-35b-instruct-reasoning-high` | Listed by `/models`, but since 17 Nov every call fails (HTTP 404 `Unknown model`, later 400 `no healthy deployments`). |

### Reasoning

Leave it off (`thinking=False`). Tested on a full pipeline run with gemma and qwen-3.8: the catalogue did
not get better, the cost tripled and every call took 2–4× as long.

- Off: `chat_template_kwargs: {"enable_thinking": false}` (client: `thinking=False`), or `reasoning_effort: "none"`.
- Any other `reasoning_effort` value switches reasoning **on**. qwen-3.8 accepts only `low`, `medium`, `xhigh`, `none`.
- Reasoning text is in `message.reasoning_content`, not `content`. With reasoning on, raise `max_tokens`
  by several thousand, or the reasoning can use it all and leave the answer empty.

## Limits, speed, cost

| | |
|---|---|
| **Parallel requests** | **At most 4 per team, always with retry.** gemma answered 40 simulated teams × 4 parallel requests with short prompts (abstract-sized) in ≤ 7 s per call. The proxy refuses connections (`ConnectError`) instead of answering HTTP 429 when it is overloaded. |
| **Long prompts** | **The real limit.** 40 teams × 4 requests of ~7 500 tokens each (≈ 32 000 characters of full text) overloaded gemma: over 1 000 timeouts in 10 min, and every gemma user stayed slow for ~14 min after the test stopped. So: send the abstract plus 2–3 selected passages (~2 000 tokens), not long blocks of text; **at most 2 parallel requests for calls with long texts**, with a client timeout of 600 s (`ILaaSClient(timeout=600)`); after a timeout on a long prompt wait ≥ 30 s before retrying. |
| Timeouts | A request the client gives up on keeps running on the server and is still billed; an immediate retry doubles the load. The client's default timeout is 300 s; for calls with long texts use 600 s (`ILaaSClient(timeout=600)`), so that a slow answer is not abandoned and sent again; after a `ReadTimeout` it waits 30 s before retrying (other connection errors: 1–8 s). |
| Throughput | 200 abstracts screened in ~35 s; 9 465 abstracts embedded in ~104 s. |
| Largest request | ~1 MiB request body ≈ 950 000 characters ≈ 225 000 tokens. Accepted doesn't mean read carefully, and long prompts are slow and load the server for everyone (above). |
| Cost | gemma ≈ 0.02 and qwen-3.8 ≈ 0.05 budget units per million tokens; llama, `bge-m3` and the reranker report cost 0. One full pipeline run (~400 calls) with gemma: ≈ 0.011 (≈ 0.03 with reasoning). |
| Rate-limit headers | `x-ratelimit-*` appear only sometimes and don't reset predictably; don't throttle on them. |
| Reproducibility | `temperature=0` is **not** deterministic. Cache every response and submit the cache with your code. |

## Errors

| Message | Meaning and fix |
|---|---|
| 401 `Authentication Error, Invalid proxy server token passed` | Wrong key: check `$ILAAS_API_KEY`. |
| 403 `The requested model '…' is not available for this API key` | Typo in the model name: copy it from `/models`. |
| 404 `… 'error': 'Unknown model'. Received Model Group=…` | The model is listed but its backend doesn't serve it right now (qwen-3.6 since 17 Nov). Use `gemma-4-31b`; tell the organizers. |
| 400 `no healthy deployments` | Same: the proxy has no working backend for that model. Use another model or retry later. |
| 404 `NotFoundError … Received Model Group=bge-m3` | Embedding or rerank model sent to `/chat/completions`. |
| 404 `{"detail":"Not Found"}` | Wrong path: the base URL ends in `/v1`; paths are `/chat/completions`, `/embeddings`, `/rerank`, `/models`. |
| 500 `'>=' not supported between instances of 'str' and 'int'` | **Not an outage; the request is invalid.** Seen for: `max_tokens` 0 or far too large, `temperature` as a string, empty `messages`, an unsupported `reasoning_effort`, a chat model on `/embeddings`, a prompt longer than the model's context (llama). Fix the request, don't retry. |
| 500 `… 413 Request Entity Too Large` | Request body over ~1 MiB: send passages instead of the whole paper. |
| 400 `ContextWindowExceededError … 8192 tokens` | One embedding input or rerank document is too long: split or truncate it (also tables without full stops). |
| 400 `body.top_n` (random) | `/rerank` without `top_n`: always send it. |
| 429 `All deployments for selected model are in cooldown` | A failed request (often a chat model on `/embeddings`) blocked the model **for everyone for ~5 s**. Wait ≥ 6 s and retry. |
| `ConnectError` / `ConnectTimeout` | Too many parallel requests, or the service is down. Fewer workers, retry with backoff; if it persists, use the fallback below. |
| `ReadTimeout` | No answer in time: usually an overloaded model (often after long prompts from many users). Send shorter texts, fewer parallel requests, wait ≥ 30 s before retrying. |
| 200, `finish_reason: "length"`, broken or empty JSON | `max_tokens` too small, or reasoning used it up: raise `max_tokens`, switch thinking off. |
| Unknown parameters (e.g. a misspelt option) | Silently ignored: no error, no effect. |

## If the API is unavailable

Run every call through the response cache from the start (`ILAAS_CACHE=1` or `use_cache=True`). The same
scripts then replay all earlier answers without key or network; a call that was never made fails at once
with `cache miss`, and those papers are verified by hand. A changed prompt, model or text is a new request
and is not in the cache.

"""Turn an ILaaS / LiteLLM failure into a short kind + fix hint (stdlib only).

Used by ilaas_client.py (printed when a call fails) and api_monitor.py (error table with hints).
Works on one record of logs/api_calls.jsonl: {"status", "path", "request", "response", "error"}.
The rules come from error probes against the hackathon endpoint (Nov 2026), one request per error case.

  classify(record)  -> (kind, hint) for a failed call, else None
  warnings(record)  -> [(kind, hint), ...] for a call that "worked" but probably not as intended
"""
import json

EMBED_MODELS = {"bge-m3"}
RERANK_MODELS = {"bge-reranker-v2-m3"}
# top-level fields an OpenAI-compatible /chat/completions request may carry; anything else is
# silently ignored by the proxy (no error, no effect)
CHAT_PARAMS = {
    "model", "messages", "temperature", "top_p", "n", "stream", "stop", "max_tokens", "max_completion_tokens",
    "presence_penalty", "frequency_penalty", "logit_bias", "user", "response_format", "seed", "tools",
    "tool_choice", "logprobs", "top_logprobs", "chat_template_kwargs", "reasoning_effort", "stream_options",
    "parallel_tool_calls", "extra_body", "top_k", "min_p", "repetition_penalty",
}
GENERIC_500 = "not supported between instances of 'str' and 'int'"


def _text(rec):
    """Error text of a record, whatever the client stored."""
    if rec.get("error"):
        return str(rec["error"])
    r = rec.get("response")
    return r if isinstance(r, str) else json.dumps(r or {}, ensure_ascii=False)


def _chars(c):
    """Length of a message content: plain text, or the {'chars': n} stub of a redacted log record."""
    if isinstance(c, str):
        return len(c)
    if isinstance(c, dict) and isinstance(c.get("chars"), int):
        return c["chars"]
    if isinstance(c, list):
        return sum(_chars(x.get("text") if isinstance(x, dict) else x) for x in c)
    return len(json.dumps(c)) if c else 0


def _prompt_chars(req):
    msgs = req.get("messages") or []
    return sum(_chars(m.get("content")) for m in msgs if isinstance(m, dict))


def _generic_500_cause(rec):
    """The proxy answers many invalid requests with the same HTTP 500; guess which field is wrong.
    Returns (short cause, fix)."""
    req, path = rec.get("request") or {}, rec.get("path") or ""
    model = str(req.get("model") or "")
    if path.endswith("/embeddings") and model not in EMBED_MODELS:
        return ("chat model on /embeddings", f"'{model}' is a chat model, /embeddings needs bge-m3. This error "
                f"also puts {model} into a ~5 s cooldown for ALL users")
    if path.endswith("/chat/completions"):
        mt = req.get("max_tokens")
        if not req.get("messages"):
            return ("empty messages", "the messages list is empty")
        if isinstance(mt, (int, float)) and (mt < 1 or mt > 200_000):
            return ("max_tokens out of range", f"max_tokens={mt} is out of range: use 1000-4000 for JSON answers")
        if isinstance(req.get("temperature"), str) or isinstance(mt, str):
            return ("number sent as string", "temperature / max_tokens must be numbers, not strings")
        if "reasoning_effort" in req:
            return ("reasoning_effort", "remove reasoning_effort; use chat_template_kwargs={'enable_thinking': False/True}")
        n = _prompt_chars(req)
        if "llama" in model and n > 400_000:
            return ("prompt over llama context", f"prompt (~{n // 4300 // 1000:,}k tokens) is over llama-3.1-8b's "
                    "context: use gemma-4-31b or send passages")
    return ("cause unknown", "check model name for the endpoint, max_tokens, temperature, messages; "
            "don't retry unchanged")


def classify(rec):
    """(kind, hint) for a failed record, or None if it is not an error."""
    st, txt = rec.get("status"), _text(rec)
    req = rec.get("request") or {}
    model, path = req.get("model"), rec.get("path") or ""
    if st == 0 or "cache miss" in txt:
        return ("offline cache miss", "this exact request was never sent before: set ILAAS_API_KEY to send it, "
                "or mark the paper for manual verification. A changed prompt/model/text is a new request")
    if st is None:                                         # transport error, no HTTP answer
        if "Timeout" in txt and "ConnectTimeout" not in txt:   # Read/Write/PoolTimeout: no answer in time
            return ("client timeout", "the server was too slow for the client timeout: raise timeout "
                    "(ILaaSClient(timeout=300)), send passages instead of whole papers")
        if "LocalProtocolError" in txt or "Illegal header" in txt:
            return ("bad API key value", "the key is empty or has a newline/space: export ILAAS_API_KEY=... and re-source")
        # ConnectError / ConnectTimeout / ReadError: the proxy refused or dropped the connection (it throttles this
        # way instead of answering HTTP 429)
        return ("connection refused", "too many parallel requests (keep <= 4 per team) or the service is down: "
                "fewer workers, retry with backoff; if it persists use the cache / lexical fallback")
    if not isinstance(st, int) or st < 400:
        return None
    if st == 401:
        return ("wrong API key", "check $ILAAS_API_KEY (echo ${ILAAS_API_KEY:0:6}); export it again in this terminal")
    if st == 403 and "not available for this API key" in txt:
        return ("unknown model name", f"'{model}' does not exist: copy the exact id from GET /v1/models "
                "(e.g. gemma-4-31b, bge-m3, bge-reranker-v2-m3)")
    if "no healthy deployments" in txt:
        return ("model unavailable", f"'{model}' is listed by /models, but the proxy has no working backend for it "
                "right now (HTTP 400 'no healthy deployments'): use another chat model (gemma-4-31b, or "
                "qwen-3.8-27b with thinking off) or retry later, and tell the organizers")
    if st == 404 and "Unknown model" in txt:
        return ("model unavailable", f"'{model}' is listed by /models, but its backend does not serve it right now "
                "(upstream 404 'Unknown model'): use another chat model (gemma-4-31b, or qwen-3.8-27b with thinking off) "
                "or retry later, and tell the organizers")
    if st == 404:
        if model in EMBED_MODELS | RERANK_MODELS and path.endswith("/chat/completions"):
            return ("wrong model for endpoint", f"{model} is not a chat model: use it on "
                    f"{'/embeddings' if model in EMBED_MODELS else '/rerank'}, chat with gemma-4-31b")
        return ("wrong URL path", f"'{path}' does not exist: base_url must end in /v1, paths are "
                "/chat/completions, /embeddings, /rerank, /models")
    if st == 429:
        if "cooldown" in txt:
            return ("model in cooldown", f"a failed request (often a chat model on /embeddings) blocked {model} "
                    "for ~5 s for everyone: wait >= 6 s and retry; fix the failing request if it is yours")
        return ("rate limited", "slow down: fewer parallel workers (<= 4), retry with backoff")
    if "Request Entity Too Large" in txt:
        return ("request too large", "request body over ~1 MiB (~950k chars, ~225k tokens): send abstract + "
                "top reranked passages instead of the whole text")
    if "ContextWindowExceeded" in txt or "8192" in txt:
        return ("input too long", "an embedding input or rerank document is over 8192 tokens (~30k chars): "
                "split long texts into chunks (~1500 chars) and hard-split chunks without sentence ends")
    if "reasoning effort" in txt.lower():
        return ("bad reasoning_effort", "remove reasoning_effort; switch reasoning with "
                "chat_template_kwargs={'enable_thinking': False}")
    if "top_n" in txt:
        return ("rerank top_n", "always send top_n as an integer (the client does this)")
    if GENERIC_500 in txt:
        cause, hint = _generic_500_cause(rec)
        return (f"invalid request (500): {cause}", "not an outage, don't retry unchanged: " + hint)
    if st >= 500:
        return ("server error", "retry with backoff; if it repeats for every request the service is down: "
                "use the cache / lexical fallback")
    return (f"HTTP {st}", "read the message; check model name, endpoint and parameters")


def warnings(rec):
    """Problems in a successful (HTTP 200) chat call."""
    out = []
    if rec.get("status") != 200 or not str(rec.get("path", "")).endswith("/chat/completions"):
        return out
    req, resp = rec.get("request") or {}, rec.get("response") or {}
    unknown = sorted(set(req) - CHAT_PARAMS)
    if unknown:
        out.append(("unknown parameter ignored", f"{', '.join(unknown)} silently ignored by the proxy: check the "
                    "spelling; reasoning switch is chat_template_kwargs={'enable_thinking': ...}"))
    ch = (resp.get("choices") or [{}])[0] if isinstance(resp, dict) else {}
    content = (ch.get("message") or {}).get("content")
    if ch.get("finish_reason") == "length":
        reas = ((resp.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
        out.append(("truncated answer", "finish_reason=length: raise max_tokens (>= 1000 for JSON)"
                    + ("; switch thinking off (reasoning used the budget)" if reas else "")
                    + (" -> content is EMPTY" if not content else "; JSON is probably broken")))
    elif ch and not content:
        out.append(("empty answer", "content is empty: switch thinking off (chat_template_kwargs="
                    "{'enable_thinking': False}) or raise max_tokens"))
    return out

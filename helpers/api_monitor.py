#!/usr/bin/env python3
"""Live monitor for logs/api_calls.jsonl (written by ilaas_client.py).

Follows the log like `tail -f` and redraws a small dashboard: calls, errors, retries, models, tags,
tokens, cost, key budget (x-litellm-key-spend / -max-budget), rate-limit headers and latency.
Rings the terminal bell each time a further 5 % of a limit is used up:
  - key budget:  spend / max budget
  - rate limits: (limit - remaining) / limit for requests and tokens (only when the proxy sends them)
Bells only fire for steps crossed while the monitor runs, not for the history already in the file.

Stdlib only, so any python3 works:
  python3 api_monitor.py                    # whole file so far + live
  python3 api_monitor.py --new              # only calls logged after start
  python3 api_monitor.py --once             # print one snapshot and exit
  options: --file PATH  --interval 1.0  --step 5  --bell-on-error  --no-bell
Cache hits (ILaaSClient use_cache=True) are not written to the log, so they don't appear here;
offline cache misses are.
Every error and warning (truncated / empty answer, ignored parameter) is classified with api_errors.py
(keep it next to this file) and shown with a fix hint; --hints N sets how many types are listed.
"""
import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path(__file__).parent / "logs" / "api_calls.jsonl"
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from api_errors import classify, warnings
except ImportError:                                   # monitor still works, just without hints
    classify = lambda rec: None                       # noqa: E731
    warnings = lambda rec: []                         # noqa: E731


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class Stats:
    def __init__(self):
        self.calls = 0                      # HTTP responses + transport failures
        self.status = Counter()
        self.retries = 0                    # records the client logged before retrying
        self.paths, self.models, self.tags = Counter(), Counter(), Counter()
        self.prompt_tok = self.out_tok = 0
        self.cost = 0.0
        self.spend = self.budget = None
        self.rl = {}                        # "requests"/"tokens" -> (limit, remaining, ts)
        self.lat = deque(maxlen=500)
        self.recent = deque()               # timestamps (epoch) for calls/min
        self.last_error = None
        self.problems = {}                  # kind -> {"n", "retried", "last", "hint", "model", "warn"}
        self.last_ts = None
        self.first_ts = None

    def add(self, d):
        self.calls += 1
        st = d.get("status")
        self.status["transport error" if st is None else str(st)] += 1
        if "attempt" in d:
            self.retries += 1
        req = d.get("request") or {}
        self.paths[d.get("path", "?")] += 1
        if req.get("model"):
            self.models[req["model"]] += 1
        self.tags[d.get("tag") or "-"] += 1
        u = (d.get("response") or {}).get("usage") or {}
        self.prompt_tok += u.get("prompt_tokens") or 0
        self.out_tok += u.get("completion_tokens") or 0
        h = d.get("headers") or {}
        # most responses carry only x-litellm-response-cost-original
        self.cost += fnum(h.get("x-litellm-response-cost", h.get("x-litellm-response-cost-original"))) or 0.0
        if fnum(h.get("x-litellm-key-spend")) is not None:
            self.spend = fnum(h["x-litellm-key-spend"])
        if fnum(h.get("x-litellm-key-max-budget")) is not None:
            self.budget = fnum(h["x-litellm-key-max-budget"])
        for kind in ("requests", "tokens"):
            lim, rem = fnum(h.get(f"x-ratelimit-limit-{kind}")), fnum(h.get(f"x-ratelimit-remaining-{kind}"))
            if lim and rem is not None:
                self.rl[kind] = (lim, rem, d.get("ts"))
        if d.get("wall_ms") is not None and st == 200:
            self.lat.append(d["wall_ms"] / 1000)
        if st is None or (isinstance(st, int) and (st >= 400 or st == 0)):
            err = d.get("error") or json.dumps(d.get("response"))[:300]
            self.last_error = f"{d.get('ts', '')[11:19]} [{st}] {d.get('path')} {req.get('model', '')}: {err}"
        found = [(c, False) for c in [classify(d)] if c] + [(w, True) for w in warnings(d)]
        for (kind, hint), warn in found:
            p = self.problems.setdefault(kind, {"n": 0, "retried": 0, "warn": warn})
            p["n"] += 1
            p["retried"] += "attempt" in d          # the client retried after this one
            p.update(last=(d.get("ts") or "")[11:19], hint=hint, model=req.get("model") or "")
        ts = d.get("ts")
        if ts:
            self.last_ts = ts
            self.first_ts = self.first_ts or ts
            try:
                self.recent.append(datetime.fromisoformat(ts).timestamp())
            except ValueError:
                pass

    @property
    def errors(self):
        return sum(n for s, n in self.status.items() if s in ("transport error", "0") or s[0] in "45")

    def fractions(self):
        """Used fraction of every limit we know about."""
        f = {}
        if self.spend is not None and self.budget:
            f["budget"] = self.spend / self.budget
        for kind, (lim, rem, _) in self.rl.items():
            f[f"rate-limit {kind}"] = (lim - rem) / lim
        return f


def bar(frac, width=30):
    frac = max(0.0, min(1.0, frac))
    n = round(frac * width)
    return "█" * n + "░" * (width - n)


def render_problems(s, top):
    if not s.problems:
        return ["problems     none"]
    out = ["problems     (type: count, last seen, model -> fix)   \033[2mretried = the client retried, may have succeeded\033[0m"]
    rows = sorted(s.problems.items(), key=lambda kv: kv[1]["last"], reverse=True)[:top]   # most recent first
    for kind, p in rows:
        col = "\033[33m" if p["warn"] or p["retried"] == p["n"] else "\033[31m"
        extra = f", {p['retried']} retried" if p["retried"] else ""
        out.append(f"  {col}{kind}\033[0m: {p['n']}{extra}, last {p['last']}" + (f", {p['model']}" if p["model"] else ""))
        out.append(f"      -> {p['hint']}")
    if len(s.problems) > top:
        out.append(f"  ... {len(s.problems) - top} more types (--hints N)")
    return out


def render(s, path, bells, started, top=6):
    now = time.time()
    while s.recent and s.recent[0] < now - 60:
        s.recent.popleft()
    out = [f"\033[1mILaaS API monitor\033[0m  {path}  (started {time.strftime('%H:%M:%S', time.localtime(started))}, "
           f"now {time.strftime('%H:%M:%S')}, Ctrl-C to quit)", ""]
    err_col = "\033[31m" if s.errors else "\033[32m"
    out.append(f"calls        {s.calls:>8}   last 60 s: {len(s.recent):>4}/min   "
               f"{err_col}errors {s.errors}\033[0m   retried attempts {s.retries}")
    out.append("status       " + "  ".join(f"{k}: {v}" for k, v in sorted(s.status.items())))
    out.append("endpoints    " + "  ".join(f"{k}: {v}" for k, v in s.paths.most_common(4)))
    out.append("models       " + "  ".join(f"{k}: {v}" for k, v in s.models.most_common(6)))
    out.append("tags (top 5) " + "  ".join(f"{k}: {v}" for k, v in s.tags.most_common(5)))
    out.append(f"tokens       prompt {s.prompt_tok:,}   completion {s.out_tok:,}   "
               f"cost (sum of x-litellm-response-cost) {s.cost:.5f}")
    if s.lat:
        lat = sorted(s.lat)
        out.append(f"latency      median {statistics.median(lat):.1f} s   p90 {lat[int(0.9 * (len(lat) - 1))]:.1f} s   "
                   f"max {lat[-1]:.1f} s   (last {len(lat)} OK calls)")
    out.append("")
    if s.spend is not None:
        f = s.fractions().get("budget")
        if f is not None:
            out.append(f"budget       {bar(f)} {100 * f:6.3f} %   spend {s.spend:.6f} of {s.budget:g}  (per key, shared by all its users)")
        else:
            out.append(f"budget       spend {s.spend:.6f} (no max budget header)")
    else:
        out.append("budget       no x-litellm-key-spend header seen yet")
    if s.rl:
        for kind, (lim, rem, ts) in s.rl.items():
            f = (lim - rem) / lim
            out.append(f"rate {kind:8} {bar(f)} {100 * f:6.2f} %   remaining {rem:g} of {lim:g}  (header from {str(ts)[11:19]})")
    else:
        out.append("rate limits  no x-ratelimit-* headers seen yet (the proxy sends them only sporadically)")
    out.append("")
    out += render_problems(s, top)
    out.append("")
    out.append(f"bells rung   {bells}" + (f"   last error: {s.last_error[:160]}" if s.last_error else ""))
    out.append(f"last call    {s.last_ts or '-'}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--interval", type=float, default=1.0, help="redraw interval in seconds")
    ap.add_argument("--step", type=float, default=5.0, help="bell every STEP percent of a limit")
    ap.add_argument("--new", action="store_true", help="ignore calls already in the file")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--bell-on-error", action="store_true", help="also ring on every new error")
    ap.add_argument("--no-bell", action="store_true")
    ap.add_argument("--hints", type=int, default=6, help="error/warning types listed with fix hints")
    ap.add_argument("--tag", help="only count calls whose tag starts with this (e.g. e2e_mm)")
    args = ap.parse_args()

    started = time.time()
    s = Stats()
    pos, inode, buf = 0, None, ""
    steps = {}            # limit name -> last 5 %-step index seen
    bells = 0

    def feed(line):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            return
        if args.tag and not str(d.get("tag") or "").startswith(args.tag):
            return
        s.add(d)

    def read_new():
        """Return complete new lines; handles a truncated or replaced file."""
        nonlocal pos, inode, buf
        try:
            st = os.stat(args.file)
        except FileNotFoundError:
            return []
        if inode is not None and (st.st_ino != inode or st.st_size < pos):
            pos, buf = 0, ""                       # file rotated or truncated: start over
        inode = st.st_ino
        if st.st_size == pos:
            return []
        with open(args.file, "r", encoding="utf-8") as f:
            f.seek(pos)
            data = f.read()
            pos = f.tell()
        buf += data
        *lines, buf = buf.split("\n")              # keep a half-written last line for later
        return lines

    # history
    if args.new:
        try:
            pos = os.stat(args.file).st_size
            inode = os.stat(args.file).st_ino
        except FileNotFoundError:
            pass
    else:
        for line in read_new():
            feed(line)
    steps = {k: int(100 * v // args.step) for k, v in s.fractions().items()}

    if args.once:
        print(render(s, args.file, bells, started, args.hints))
        return

    try:
        while True:
            errors_before = s.errors
            for line in read_new():
                feed(line)
            ring = False
            for k, v in s.fractions().items():
                step = int(100 * v // args.step)
                if k in steps and step > steps[k]:
                    ring = True
                steps[k] = step        # rate-limit windows reset, so the step may also go down again
            if args.bell_on_error and s.errors > errors_before:
                ring = True
            if ring and not args.no_bell:
                bells += 1
                sys.stdout.write("\a")
            sys.stdout.write("\033[H\033[2J" + render(s, args.file, bells, started, args.hints) + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()

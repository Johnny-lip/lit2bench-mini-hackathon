#!/usr/bin/env python3
"""Turn a catalogue into a review page for the human check, and apply the decisions back.

  python3 review_page.py catalogue.json                     -> catalogue_review.html
  python3 review_page.py catalogue.json --apply review_decisions.csv
                                                            -> catalogue_reviewed.json

The page is one self-contained HTML file: open it in any browser, no server and no internet
needed. It shows one card per task-paper row (paper links, task, evidence quote, rationale,
score) with three buttons, "Valid task paper", "Uncertain" and "Rejected", two toggles for whether
the paper's data and model are available, and a comment box. Decisions are kept in the browser while
you work; "Export decisions" saves review_decisions.csv, and "Import decisions" loads such a file
again (to continue on another machine, or to merge a teammate's review).

--apply writes the decisions into a copy of the catalogue: decision becomes supported (Valid task
paper), uncertain or excluded (Rejected); dataset_status / model_code_status become available or
unavailable where you marked them; the comment goes to review_comment. Rows without a decision are
left as they are (an unmarked candidate row still counts as a claim when scored).

Catalogues in the handout format are accepted as CSV, as a JSON list of rows, or as JSON
{"tasks": [{..., "papers": [rows]}]}. Standard library only.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys


def load(path):
    """-> (rows, original): rows are the row dicts themselves (edited in place by --apply)."""
    if path.endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        return rows, rows
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("tasks"), list):
        rows = []
        for t in data["tasks"]:
            for p in t.get("papers") or []:
                for k, v in t.items():            # task-level fields apply to every paper of the task
                    if not isinstance(v, (list, dict)) and p.get(k) in (None, ""):
                        p[k] = v
                rows.append(p)
        return rows, data
    rows = data.get("rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        sys.exit(f"{path}: no rows found")
    return rows, data


def text(v):
    return "" if v is None else str(v).strip()


def paper_links(row):
    """Links to show for a row, from paper_url and paper_id (arXiv, DOI, INSPIRE)."""
    out, seen = [], set()
    def add(label, url):
        if url and url not in seen:
            seen.add(url); out.append([label, url])
    pid, url = text(row.get("paper_id")), text(row.get("paper_url"))
    for s in (pid, url, text(row.get("paper_aliases"))):
        for part in re.split(r"[|;\s]+", s):
            low = part.lower()
            m = re.search(r"inspirehep\.net/(?:literature|record)/(\d+)", low) or re.fullmatch(r"inspire[:_-]?(\d+)", low)
            if m:
                add(f"INSPIRE {m.group(1)}", f"https://inspirehep.net/literature/{m.group(1)}")
            m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+)", low) or re.fullmatch(r"arxiv:(\S+)", low) or \
                re.fullmatch(r"(\d{4}\.\d{4,5}(?:v\d+)?)", low)
            if m:
                a = re.sub(r"(v\d+)?(\.pdf)?$", "", m.group(1))
                add(f"arXiv:{a}", f"https://arxiv.org/abs/{a}")
            m = re.search(r"doi\.org/(10\.\S+)", part, re.I) or re.fullmatch(r"(?:doi:)?(10\.\d{4,9}/\S+)", part, re.I)
            if m:
                add(f"doi:{m.group(1)}", f"https://doi.org/{m.group(1)}")
    if url.lower().startswith("http") and url not in seen:
        add("paper", url)
    return out


def title_of(row):
    t = text(row.get("title"))
    if not t:
        d = row.get("diagnostics")
        if isinstance(d, str) and d.startswith("{"):
            m = re.search(r"['\"]title['\"]:\s*['\"](.+?)['\"],\s*['\"]", d)
            t = m.group(1) if m else ""
        elif isinstance(d, dict):
            t = text(d.get("title"))
    return t


def key_of(row):
    return text(row.get("task_id")) + "|" + text(row.get("paper_id") or row.get("paper_url"))


def build_page(rows, name):
    items = []
    for r in rows:
        dec = text(r.get("decision")).lower()
        group = 1 if dec in ("candidate", "") else 2 if dec == "uncertain" else 3
        items.append({
            "k": key_of(r), "g": group, "f": text(r.get("field")), "t": text(r.get("task_id")),
            "tl": text(r.get("task_label")), "tg": text(r.get("observable_or_target")), "inp": text(r.get("input_modality")),
            "id": text(r.get("paper_id")), "ti": title_of(r), "links": paper_links(r),
            "cd": {"supported": "Valid task paper", "excluded": "Rejected"}.get(dec, dec or "none"), "why": text(r.get("match_rationale")),
            "ev": text(r.get("evidence_text")), "el": text(r.get("evidence_location")),
            "m": text(r.get("metric")), "rr": text(r.get("reported_result"))[:700], "es": text(r.get("evaluation_setting"))[:300],
            "ds": text(r.get("dataset_status")) or "not_checked", "du": text(r.get("dataset_url")),
            "cs": text(r.get("model_code_status")) or "not_checked", "cu": text(r.get("model_code_url")),
            "rm": text(r.get("retrieval_method"))[:200],
        })
    items.sort(key=lambda x: (x["g"], x["f"], x["t"]))
    keys = sorted(i["k"] for i in items)
    dup = len(keys) - len(set(keys))
    store = "lit2bench-review-" + hashlib.sha1("\n".join(keys).encode()).hexdigest()[:12]
    data = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.replace("__NAME__", html_escape(name)).replace("__STORE__", store).replace("__DATA__", data)
    return page, len(items), dup


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def apply(path, rows, original, decisions_path, out_path):
    with open(decisions_path, newline="", encoding="utf-8-sig") as f:
        dec = {r["task_id"] + "|" + r["paper_id"]: r for r in csv.DictReader(f)}
    n = unknown = 0
    seen = set()
    for r in rows:
        d = dec.get(key_of(r))
        if not d:
            continue
        seen.add(key_of(r))
        choice = {"valid": "supported", "valid task paper": "supported", "rejected": "excluded"}.get(
            text(d.get("decision")).lower(), text(d.get("decision")).lower())
        if choice in ("supported", "uncertain", "excluded"):
            r["decision"] = choice
            n += 1
        for col, field in (("data", "dataset_status"), ("model", "model_code_status")):
            if text(d.get(col)).lower() in ("available", "unavailable"):
                r[field] = text(d.get(col)).lower()
        if text(d.get("comment")):
            r["review_comment"] = text(d.get("comment"))
    unknown = len(set(dec) - seen)
    if path.endswith(".csv"):
        cols = list(dict.fromkeys(k for r in rows for k in r))
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(rows)
    else:
        json.dump(original, open(out_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    by = {}
    for r in rows:
        by[text(r.get("decision"))] = by.get(text(r.get("decision")), 0) + 1
    print(f"applied {n} decisions to {len(rows)} rows -> {out_path}" + (f" ({unknown} decisions matched no row)" if unknown else ""))
    names = {"supported": "Valid task paper (supported)", "excluded": "Rejected (excluded)"}
    for d, c in sorted(by.items()):
        print(f"  {c:4d}  {names.get(d, d) or '(no decision)'}")
    tasks_ok = {text(r.get("task_id")) for r in rows if text(r.get("decision")) == "supported"}
    print(f"tasks with a paper marked Valid task paper: {len(tasks_ok)} (the minimum is 2, and the paper must be in the solution)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("catalogue")
    ap.add_argument("--apply", metavar="DECISIONS_CSV", help="write the exported decisions into a copy of the catalogue")
    ap.add_argument("--out", help="output file (default: <catalogue>_review.html, or <catalogue>_reviewed.<ext> with --apply)")
    a = ap.parse_args()
    rows, original = load(a.catalogue)
    stem, ext = os.path.splitext(a.catalogue)
    if a.apply:
        apply(a.catalogue, rows, original, a.apply, a.out or f"{stem}_reviewed{ext}")
        return
    page, n, dup = build_page(rows, os.path.basename(a.catalogue))
    out = a.out or f"{stem}_review.html"
    open(out, "w", encoding="utf-8").write(page)
    print(f"{n} rows -> {out}  (open it in a browser)")
    if dup:
        print(f"warning: {dup} rows share a task_id and paper_id with another row; they share one decision")


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Catalogue review: __NAME__</title>
<style>
:root { --bg:#f3f5f6; --surface:#fff; --ink:#18242b; --muted:#5d6c75; --line:#d5dcdf; --accent:#0b6e7c; --accent-ink:#fff; --wash:#e6eef0;
  --good:#1f7a4a; --good-bg:#e0f2e8; --warn:#8a5a00; --warn-bg:#fbeed0; --bad:#a8322d; --bad-bg:#f9e1df;
  --body:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; --mono:ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace; }
@media (prefers-color-scheme: dark) { :root { --bg:#10181c; --surface:#18232a; --ink:#e4ecef; --muted:#94a5ae; --line:#2b3a43; --accent:#5cc4d2; --accent-ink:#06262b; --wash:#1f2e36;
  --good:#7fd6a4; --good-bg:#173326; --warn:#f0c36a; --warn-bg:#3a2d10; --bad:#f29a94; --bad-bg:#40201e; color-scheme:dark } }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--ink); font-family:var(--body); font-size:15px; line-height:1.5 }
.wrap { max-width:960px; margin:0 auto; padding:0 16px }
header.page { padding:20px 0 8px } h1 { font-size:1.4rem; margin:0 } .lede { color:var(--muted); margin:4px 0 0; max-width:70ch }
.bar { position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line); padding:10px 0 }
.bar .wrap { display:flex; flex-direction:column; gap:10px }
.progress,.filters { display:flex; align-items:center; gap:8px 12px; flex-wrap:wrap }
.meter { flex:1 1 160px; height:8px; background:var(--wash); border-radius:4px; overflow:hidden; min-width:100px } .meter>span { display:block; height:100%; width:0; background:var(--accent) }
.count { font-variant-numeric:tabular-nums; font-weight:600 } .sync { color:var(--muted); font-size:.85rem }
.seg { display:inline-flex; border:1px solid var(--line); border-radius:8px; overflow:hidden; background:var(--surface) }
.seg button { border:0; background:transparent; color:var(--ink); font:inherit; font-size:.85rem; padding:6px 10px; cursor:pointer; font-variant-numeric:tabular-nums }
.seg button+button { border-left:1px solid var(--line) } .seg button[aria-pressed="true"] { background:var(--accent); color:var(--accent-ink) }
select,input[type="search"],textarea { font:inherit; color:var(--ink); background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:6px 10px; min-width:0; max-width:100% }
input[type="search"] { flex:1 1 160px }
button.act,label.act { font:inherit; font-size:.85rem; font-weight:500; border:1px solid var(--accent); color:var(--accent); background:transparent; border-radius:8px; padding:6px 12px; cursor:pointer }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px }
main { padding:16px 0 48px; display:flex; flex-direction:column; gap:12px } .empty { color:var(--muted); padding:32px 0; text-align:center }
.card { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:14px 16px; display:flex; flex-direction:column; gap:10px; min-width:0 }
.card[data-decision="supported"] { border-color:var(--good) } .card[data-decision="uncertain"] { border-color:var(--warn) } .card[data-decision="excluded"] { border-color:var(--bad) }
.meta { display:flex; flex-wrap:wrap; gap:6px; font-size:.8rem } .chip { border-radius:999px; padding:2px 9px; background:var(--wash) }
.chip.good { background:var(--good-bg); color:var(--good) } .chip.warn { background:var(--warn-bg); color:var(--warn) } .chip.bad { background:var(--bad-bg); color:var(--bad) }
.chip.task { font-family:var(--mono); font-size:.75rem; overflow-wrap:anywhere }
.card h2 { font-size:1.05rem; margin:0; overflow-wrap:anywhere } .links { display:flex; flex-wrap:wrap; gap:4px 14px; font-size:.85rem; font-family:var(--mono) } a { color:var(--accent) }
dl { margin:0; display:grid; grid-template-columns:7.5rem minmax(0,1fr); gap:6px 12px } dt { color:var(--muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; padding-top:3px }
dd { margin:0; overflow-wrap:anywhere } dd.quote { border-left:3px solid var(--line); padding-left:10px } .none { color:var(--muted) }
.decide { display:flex; flex-wrap:wrap; gap:8px; align-items:flex-start; border-top:1px solid var(--line); padding-top:10px }
.decide .seg button[aria-pressed="true"][data-v="supported"] { background:var(--good); color:var(--surface) }
.decide .seg button[aria-pressed="true"][data-v="uncertain"] { background:var(--warn); color:var(--surface) }
.decide .seg button[aria-pressed="true"][data-v="excluded"] { background:var(--bad); color:var(--surface) }
.decide textarea { flex:1 1 260px; height:34px; min-height:34px; resize:vertical; font-size:.9rem } .more { align-self:center }
.seglabel { font-size:.75rem; color:var(--muted); padding:7px 8px 0; text-transform:uppercase; letter-spacing:.05em }
@media (max-width:560px) { dl { grid-template-columns:1fr; gap:2px } dt { padding-top:8px } }
</style></head><body>
<header class="page"><div class="wrap"><h1>Catalogue review: __NAME__</h1>
<p class="lede">One card per task–paper row. Open the paper and check that its ML is the main method for the task and that it reports a number for that ML. Then mark it <b>Valid task paper</b>, <b>Uncertain</b> or <b>Rejected</b>, and say whether its data and model are available. Decisions stay in this browser; export them and run <code>review_page.py --apply</code> to write them into the catalogue.</p></div></header>
<div class="bar"><div class="wrap">
<div class="progress"><span class="count" id="count"></span><div class="meter" aria-hidden="true"><span id="meter"></span></div><span class="sync" id="sync"></span>
<button class="act" id="exportBtn" type="button">Export decisions</button><label class="act" for="importFile">Import decisions</label><input type="file" id="importFile" accept=".csv,text/csv" hidden></div>
<div class="filters"><div class="seg" id="groupSeg" role="group" aria-label="Group"></div>
<div class="seg" id="statusSeg" role="group" aria-label="Status"><button type="button" data-v="open" aria-pressed="true">To review</button><button type="button" data-v="done" aria-pressed="false">Reviewed</button><button type="button" data-v="all" aria-pressed="false">All</button></div>
<select id="taskSel" aria-label="Task"><option value="all">All tasks</option></select><input type="search" id="q" placeholder="Search title or paper id" aria-label="Search"></div>
</div></div>
<div class="wrap"><main id="list"></main></div>
<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
  "use strict";
  var STORE = "__STORE__", PAGE = 40;
  var GROUPS = { 1: "Needs a decision", 2: "Marked uncertain", 3: "Marked valid or rejected" };
  var LABEL = { supported: "Valid task paper", uncertain: "Uncertain", excluded: "Rejected" };
  function avState(s) { s = (s || "").toLowerCase(); return /^(available|verified_accessible|yes)$/.test(s) ? "available" : /^(unavailable|unavailable_confirmed|no)$/.test(s) ? "unavailable" : ""; }
  var AV = { verified_accessible: ["verified accessible", "good"], mentioned_unverified: ["mentioned, not verified", "warn"], unavailable_confirmed: ["confirmed unavailable", "bad"], restricted: ["restricted", "warn"], not_checked: ["not checked", ""] };
  var papers = JSON.parse(document.getElementById("data").textContent), decisions = {}, cards = {};
  var filter = { group: "all", status: "open", task: "all", q: "" }, shown = PAGE, current = [];
  var $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function link(href, text) { var a = el("a", null, text); a.href = href; a.target = "_blank"; a.rel = "noopener"; return a; }
  function safeUrl(u) { if (/^10\.\d/.test(u)) return "https://doi.org/" + u; return /^https?:\/\//i.test(u) ? u : ""; }
  function decided(p) { var d = decisions[p.k]; return !!(d && d.decision); }
  function persist() { try { localStorage.setItem(STORE, JSON.stringify(decisions)); $("sync").textContent = "Saved in this browser"; } catch (e) { $("sync").textContent = "This browser does not keep data: export before closing"; } }
  try { decisions = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { decisions = {}; }

  function counts() {
    var done = 0, per = { 1: [0, 0], 2: [0, 0], 3: [0, 0] };
    papers.forEach(function (p) { per[p.g][1]++; if (decided(p)) { done++; per[p.g][0]++; } });
    $("count").textContent = done + " of " + papers.length + " reviewed";
    $("meter").style.width = (papers.length ? 100 * done / papers.length : 0) + "%";
    var seg = $("groupSeg"); seg.textContent = "";
    [["all", "All", done, papers.length]].concat([1, 2, 3].filter(function (g) { return per[g][1]; }).map(function (g) { return [String(g), GROUPS[g], per[g][0], per[g][1]]; })).forEach(function (x) {
      var b = el("button", null, x[1] + " " + x[2] + "/" + x[3]); b.type = "button"; b.setAttribute("aria-pressed", String(filter.group === x[0]));
      b.addEventListener("click", function () { filter.group = x[0]; rebuild(); }); seg.appendChild(b);
    });
  }
  function row(dl, label, node, cls) { dl.appendChild(el("dt", null, label)); var dd = el("dd", cls); dd.appendChild(node); dl.appendChild(dd); }
  function txt(s) { return s ? document.createTextNode(s) : el("span", "none", "not given"); }
  function avail(status, url) {
    var f = document.createDocumentFragment(), a = AV[status] || [status, ""];
    f.appendChild(el("span", "chip " + a[1], a[0]));
    var u = safeUrl(url); if (u) { f.appendChild(document.createTextNode(" ")); f.appendChild(link(u, url)); }
    return f;
  }
  function card(p) {
    var c = el("article", "card"), meta = el("div", "meta");
    meta.appendChild(el("span", "chip" + (p.g === 1 ? " warn" : ""), GROUPS[p.g]));
    if (p.f) meta.appendChild(el("span", "chip", p.f));
    meta.appendChild(el("span", "chip task", p.t));
    meta.appendChild(el("span", "chip", "in catalogue: " + p.cd));
    c.appendChild(meta);
    c.appendChild(el("h2", null, p.ti || p.id || "(no title)"));
    var links = el("div", "links"); p.links.forEach(function (l) { links.appendChild(link(l[1], l[0])); });
    if (!p.links.length) links.appendChild(el("span", "none", p.id || "no paper id"));
    c.appendChild(links);
    var dl = el("dl");
    row(dl, "Task", txt([p.tl || p.t, p.tg ? "target: " + p.tg : "", p.inp ? "input: " + p.inp : ""].filter(Boolean).join(" · ")));
    row(dl, "Evidence", txt(p.ev ? p.ev + (p.el ? "  (" + p.el + ")" : "") : ""), "quote");
    row(dl, "Rationale", txt(p.why));
    if (p.m || p.rr) row(dl, "Score", txt([p.m, p.rr, p.es].filter(Boolean).join(" · ")));
    if (p.du || p.cu) { row(dl, "Dataset", avail(p.ds, p.du)); row(dl, "Model / code", avail(p.cs, p.cu)); }
    if (p.rm) row(dl, "Found by", txt(p.rm));
    c.appendChild(dl);
    var decide = el("div", "decide"), seg = el("div", "seg"), buttons = {};
    ["supported", "uncertain", "excluded"].forEach(function (v) {
      var b = el("button", null, LABEL[v]); b.type = "button"; b.dataset.v = v;
      b.addEventListener("click", function () { var d = decisions[p.k] || {}; set(p, { decision: d.decision === v ? "" : v }); });
      buttons[v] = b; seg.appendChild(b);
    });
    var av = {};
    [["data", "Data"], ["model", "Model"]].forEach(function (x) {
      var s2 = el("div", "seg"); s2.setAttribute("role", "group"); s2.setAttribute("aria-label", x[1] + " available?");
      s2.appendChild(el("span", "seglabel", x[1]));
      ["available", "unavailable"].forEach(function (v) {
        var b = el("button", null, v === "available" ? "available" : "not available"); b.type = "button"; b.dataset.v = v;
        b.addEventListener("click", function () { var d = decisions[p.k] || {}, cur = d[x[0]] != null ? d[x[0]] : avState(x[0] === "data" ? p.ds : p.cs), patch = {}; patch[x[0]] = cur === v ? "" : v; set(p, patch); });
        av[x[0] + v] = b; s2.appendChild(b);
      });
      decide.appendChild(s2);
    });
    var ta = el("textarea"); ta.placeholder = "Comment (optional)"; ta.setAttribute("aria-label", "Comment");
    ta.addEventListener("change", function () { set(p, { comment: ta.value.trim() }); });
    decide.insertBefore(seg, decide.firstChild); decide.appendChild(ta); c.appendChild(decide);
    cards[p.k] = { card: c, buttons: buttons, av: av, ta: ta, p: p }; paint(p.k); return c;
  }
  function paint(k) {
    var c = cards[k]; if (!c) return; var d = decisions[k] || {};
    c.card.dataset.decision = d.decision || "";
    Object.keys(c.buttons).forEach(function (v) { c.buttons[v].setAttribute("aria-pressed", String(d.decision === v)); });
    ["data", "model"].forEach(function (x) { var cur = d[x] != null ? d[x] : avState(x === "data" ? c.p.ds : c.p.cs);
      ["available", "unavailable"].forEach(function (v) { c.av[x + v].setAttribute("aria-pressed", String(cur === v)); }); });
    if (document.activeElement !== c.ta) c.ta.value = d.comment || "";
  }
  function set(p, patch) {
    var d = decisions[p.k] || { decision: "", comment: "" };
    if (patch.decision != null) d.decision = patch.decision; if (patch.comment != null) d.comment = patch.comment;
    if (patch.data != null) d.data = patch.data; if (patch.model != null) d.model = patch.model;
    if (d.decision || d.comment || d.data || d.model) decisions[p.k] = d; else delete decisions[p.k];
    paint(p.k); counts(); persist();
  }
  function matches(p) {
    if (filter.group !== "all" && String(p.g) !== filter.group) return false;
    if (filter.task !== "all" && p.t !== filter.task) return false;
    if (filter.status === "open" && decided(p)) return false;
    if (filter.status === "done" && !decided(p)) return false;
    if (filter.q) { var q = filter.q.toLowerCase(); if ((p.ti + " " + p.id).toLowerCase().indexOf(q) < 0) return false; }
    return true;
  }
  function rebuild() { current = papers.filter(matches); shown = PAGE; render(); }
  function render() {
    counts(); var list = $("list"); list.textContent = ""; cards = {};
    if (!current.length) { list.appendChild(el("p", "empty", "No rows match these filters.")); return; }
    current.slice(0, shown).forEach(function (p) { list.appendChild(card(p)); });
    if (current.length > shown) { var b = el("button", "act more", "Show more (" + (current.length - shown) + " left in this view)"); b.type = "button"; b.addEventListener("click", function () { shown += PAGE; render(); }); list.appendChild(b); }
  }
  function cell(s) { s = String(s == null ? "" : s); return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
  $("exportBtn").addEventListener("click", function () {
    var lines = ["task_id,paper_id,decision,data,model,comment,title"];
    papers.forEach(function (p) { var d = decisions[p.k]; if (d) lines.push([p.t, p.id, d.decision, d.data || "", d.model || "", d.comment, p.ti].map(cell).join(",")); });
    var a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/csv" })); a.download = "review_decisions.csv";
    document.body.appendChild(a); a.click(); a.remove(); $("sync").textContent = "Exported " + (lines.length - 1) + " decisions";
  });
  function parseCsv(text) {
    var rows = [], row = [], cur = "", q = false;
    for (var i = 0; i < text.length; i++) { var ch = text[i];
      if (q) { if (ch === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; }
      else if (ch === '"') q = true; else if (ch === ",") { row.push(cur); cur = ""; }
      else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(cur); cur = ""; if (row.length > 1 || row[0]) rows.push(row); row = []; }
      else cur += ch; }
    if (cur || row.length) { row.push(cur); rows.push(row); }
    return rows;
  }
  $("importFile").addEventListener("change", function (e) {
    var f = e.target.files[0]; if (!f) return; var r = new FileReader();
    r.onload = function () {
      var rows = parseCsv(String(r.result)), h = rows.shift() || [], ix = {}, n = 0, known = {};
      h.forEach(function (name, i) { ix[name.trim().replace(/^﻿/, "")] = i; });
      papers.forEach(function (p) { known[p.t + "|" + p.id] = p.k; });
      rows.forEach(function (x) { var k = known[x[ix.task_id] + "|" + x[ix.paper_id]]; if (k) { decisions[k] = { decision: (x[ix.decision] || "").trim().toLowerCase(), comment: x[ix.comment] || "",
        data: ix.data != null ? (x[ix.data] || "").trim().toLowerCase() : undefined, model: ix.model != null ? (x[ix.model] || "").trim().toLowerCase() : undefined }; n++; } });
      persist(); rebuild(); $("sync").textContent = "Imported " + n + " decisions"; e.target.value = "";
    };
    r.readAsText(f);
  });
  $("statusSeg").addEventListener("click", function (e) { var b = e.target.closest("button"); if (!b) return; filter.status = b.dataset.v;
    Array.prototype.forEach.call($("statusSeg").children, function (x) { x.setAttribute("aria-pressed", String(x === b)); }); rebuild(); });
  $("taskSel").addEventListener("change", function (e) { filter.task = e.target.value; rebuild(); });
  var timer; $("q").addEventListener("input", function (e) { clearTimeout(timer); timer = setTimeout(function () { filter.q = e.target.value.trim(); rebuild(); }, 200); });
  var n = {}; papers.forEach(function (p) { n[p.t] = (n[p.t] || 0) + 1; });
  Object.keys(n).sort().forEach(function (t) { var o = el("option", null, t + " (" + n[t] + ")"); o.value = t; $("taskSel").appendChild(o); });
  $("sync").textContent = Object.keys(decisions).length ? "Decisions restored from this browser" : "";
  rebuild();
})();
</script></body></html>
"""

if __name__ == "__main__":
    main()

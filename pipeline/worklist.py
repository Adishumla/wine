"""Review loop, step 2: the review queue, worked by hand on a local page.

`build(n)` writes data/queue.html: wines asked for by article first, then open "Wrong match?" reports
(reports.py), then up to n wines in the review band that nobody has checked, the most widely carried first (then
the candidate's Vivino rating count). Each shows Systembolaget's facts beside the matcher's two best Vivino
candidates, with links to open, and takes one answer: candidate 1 or 2 is right, neither, not on Vivino, or
unsure, and optionally the right wine's Vivino link. The page holds Vivino data, so it stays in data/
(gitignored) and is opened as a local file.

`import_answers(text)` merges this page's "Copy answers" CSV (article, vivino_id, verdict, note, override), and the
audit page's (no override column): verdicts go to labels.csv, and an answer naming another wine, or none, to
overrides.csv, with the matched wine labelled wrong. A right answer on the matched wine is enough: the build then
shows its rating (band "checked"). Reports resolved by the answers move to fixed or kept.
"""

from __future__ import annotations

import csv
import json

from . import reports, review, state, vivino
from .net import DATA, Http, load_json
from .orders import catalog
from .report import group_of


def items(n: int, articles: list[str] | None = None) -> list[dict]:
    wines = review._wines()
    current = {str(p["productNumber"]): p for p in catalog().values()}
    details = load_json("match_details.json") if (DATA / "match_details.json").exists() else {}
    matches, ov, lab = state.matches(), state.overrides(), state.labels()
    order: list[tuple[str, str]] = [(a, "asked") for a in articles or []]
    order += [(r["article"], f"reported, issue #{r['issue']}")
              for r in sorted(state.reports().values(), key=lambda r: int(r["issue"])) if r["status"] == "open"]

    def unchecked(a: str, r: dict) -> bool:
        return (r["band"] == "review" and a not in ov and a in current and a in details
                and (a, r["vivino_id"]) not in lab)

    def reach(a: str) -> tuple:
        return -(current[a].get("ns") or 0), -int((details[a].get("best") or {}).get("algolia_count") or 0), a

    queue = sorted((a for a, r in matches.items() if unchecked(a, r)), key=reach)
    order += [(a, "review") for a in queue[:n]]
    out, seen = [], set()
    for a, why in order:
        if a in seen or a not in wines:
            continue
        seen.add(a)
        d, row = details.get(a, {}), matches.get(a, {})
        cands = [c for c in (d.get("best"), d.get("second")) if c]
        out.append({"article": a, "why": why, "current": row.get("vivino_id", ""),
                    "stores": (current.get(a) or {}).get("ns") or 0,
                    "sb": review.sb_side(wines[a], group_of(row) if row else ""),
                    "cands": [{"vivino_id": str(c["vivino_id"]), "score": c.get("total"),
                               "doubts": "; ".join(c.get("contradictions") or []) or "; ".join(c.get("unconfirmed") or []),
                               "vv": review.vv_side(c)} for c in cands]})
    return out


def build(n: int, articles: list[str] | None = None) -> tuple[int, str]:
    its = items(n, articles)
    page = PAGE.replace("__ITEMS__", json.dumps(its, ensure_ascii=False).replace("</", "<\\/"))
    path = DATA / "queue.html"
    path.write_text(page)
    return len(its), str(path)


def _wine_id(value: str, http: Http | None) -> str:
    """An override value: a wine id, "none", or "vintage:<id>" from a /wines/ link, looked up on api.vivino.com."""
    value = (value or "").strip().lower()
    if value.startswith("vintage:"):
        wid = vivino.wine_of_vintage(http or Http("queue"), int(value.split(":")[1]))
        if not wid:
            raise ValueError(f"vintage {value} not found on Vivino")
        return str(wid)
    return value


def import_answers(text: str, http: Http | None = None) -> dict:
    rows = list(csv.DictReader(text.strip().splitlines()))
    matches = state.matches()
    labels, overrides = [], []
    for r in rows:
        a, vid, note = r["article"].strip(), (r.get("vivino_id") or "").strip(), (r.get("note") or "").strip()
        if vid:
            labels.append({"article": a, "vivino_id": vid, "verdict": r["verdict"], "note": note})
        ov = _wine_id(r.get("override") or "", http)
        if not ov:
            continue
        overrides.append({"article": a, "vivino_id": ov, "note": note or "Adam, review queue"})
        if ov != "none":
            labels.append({"article": a, "vivino_id": ov, "verdict": "right", "note": note})
        current = (matches.get(a) or {}).get("vivino_id") or ""
        if current and current != ov:
            labels.append({"article": a, "vivino_id": current, "verdict": "wrong", "note": note})
    out = {"labels": state.add_labels(labels), "overrides": state.set_overrides(overrides)}
    rs = reports.resolve()
    out["reports_open"] = sum(r["status"] == "open" for r in rs.values())
    return out


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review Queue</title>
<style>
  :root { --bg:#f7f6f4; --card:#fff; --ink:#1f1a1c; --muted:#6c6367; --rule:#e4dee1; --accent:#7a1f3d;
          --right:#1f7a4d; --wrong:#b3261e; --unsure:#9a6700; }
  @media (prefers-color-scheme: dark) { :root { --bg:#151214; --card:#1e191c; --ink:#eee8ea; --muted:#a79ba1;
          --rule:#342a2f; --accent:#e08aa8; --right:#5fc794; --wrong:#f08a82; --unsure:#e0b35a; } }
  body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui, -apple-system, sans-serif; }
  .wrap { max-width:1180px; margin:0 auto; padding:24px 16px 80px; display:grid; gap:16px; }
  header { display:flex; flex-wrap:wrap; gap:12px; align-items:baseline; justify-content:space-between; }
  h1 { margin:0; font-size:22px; }
  .muted { color:var(--muted); }
  .bar { position:sticky; top:0; background:var(--bg); padding:8px 0; display:flex; gap:8px; flex-wrap:wrap;
         align-items:center; border-bottom:1px solid var(--rule); z-index:2; }
  .card { background:var(--card); border:1px solid var(--rule); border-radius:8px; padding:16px; display:grid; gap:12px; }
  .card.current { outline:2px solid var(--accent); }
  .card.done { opacity:.7; }
  .sides { display:grid; grid-template-columns:repeat(3, 1fr); gap:16px; }
  @media (max-width:900px) { .sides { grid-template-columns:1fr; } }
  .cand.on { outline:2px solid var(--right); outline-offset:6px; border-radius:4px; }
  h2 { margin:0 0 6px; font-size:13px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }
  dl { margin:0; display:grid; grid-template-columns:max-content 1fr; gap:2px 12px; }
  dt { color:var(--muted); } dd { margin:0; overflow-wrap:anywhere; }
  dd.name { font-weight:600; }
  .doubts { font-size:13px; color:var(--unsure); }
  .actions { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  button { font:600 14px/1 system-ui, sans-serif; border:1px solid var(--rule); background:var(--card); color:var(--ink);
           border-radius:6px; padding:9px 14px; cursor:pointer; }
  button:focus-visible, input:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
  button.on[data-v^=pick] { background:var(--right); border-color:var(--right); }
  button.on[data-v=unsure] { background:var(--unsure); border-color:var(--unsure); }
  input { flex:1; min-width:180px; font:14px system-ui, sans-serif; padding:8px; border:1px solid var(--rule);
          border-radius:6px; background:var(--bg); color:var(--ink); }
  input.bad { border-color:var(--wrong); }
  a { color:var(--accent); }
  textarea { width:100%; min-height:120px; font:12px ui-monospace, monospace; }
  kbd { font:12px ui-monospace, monospace; border:1px solid var(--rule); border-radius:4px; padding:1px 5px; }
</style></head><body><div class="wrap">
<header><h1>Review queue</h1><span class="muted" id="progress"></span></header>
<p class="muted">Which Vivino wine is the Systembolaget one? Different vintages of the same wine count as the same;
so do a box, PET bottle or can of the bottled wine. Organic and regular versions are different wines. Keys:
<kbd>1</kbd>/<kbd>2</kbd> that candidate, <kbd>W</kbd> neither, <kbd>N</kbd> not on Vivino, <kbd>U</kbd> unsure,
<kbd>L</kbd> paste the right wine's link, <kbd>J</kbd>/<kbd>K</kbd> next/previous, <kbd>E</kbd> note.</p>
<div class="bar"><button id="copy" type="button">Copy answers</button><span class="muted" id="copied"></span></div>
<div id="list"></div>
<textarea id="out" hidden readonly></textarea>
</div>
<script>
const ITEMS = __ITEMS__;
const KEY = "wine-v2-queue-" + ITEMS.map(i => i.article).join("-").slice(0, 400);
let answers = {};
try { answers = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
let cur = 0;
const list = document.getElementById("list");
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function dl(o) {
  return "<dl>" + Object.entries(o).filter(([, v]) => v !== "" && v != null)
    .map(([k, v], i) => `<dt>${esc(k)}</dt><dd class="${i === 0 ? "name" : ""}">${esc(v)}</dd>`).join("") + "</dl>";
}
// A Vivino link or id: /w/{id} is a wine; /wines/{id} is a vintage, looked up when the answers are imported.
function wineRef(s) {
  s = (s || "").trim();
  if (!s) return "";
  let m = s.match(/vivino\\.com\\/\\S*?\\bw\\/(\\d+)/); if (m) return m[1];
  m = s.match(/vivino\\.com\\/\\S*?\\bwines\\/(\\d+)/); if (m) return "vintage:" + m[1];
  m = s.match(/^(\\d{3,})$/); if (m) return m[1];
  return null;
}
function render() {
  list.innerHTML = ITEMS.map((it, i) => {
    const a = answers[it.article] || {};
    const btn = (v, label) => `<button type="button" data-i="${i}" data-v="${v}" class="${a.v === v ? "on" : ""}">${label}</button>`;
    const cands = it.cands.map((c, k) => `<div class="cand ${a.v === "pick" + k ? "on" : ""}">
        <h2>${k + 1} · Vivino ${esc(c.vivino_id)}${c.vivino_id === it.current ? " · matched" : ""}</h2>${dl(c.vv)}
        ${c.doubts ? `<p class="doubts">${esc(c.doubts)}</p>` : ""}
        <a href="https://www.vivino.com/w/${encodeURIComponent(c.vivino_id)}" target="_blank" rel="noopener">vivino.com</a></div>`).join("");
    return `<section class="card ${i === cur ? "current" : ""} ${a.v || a.link ? "done" : ""}" id="c${i}">
      <div class="muted">${esc(it.why)} · carried by ${esc(it.stores)} stores</div>
      <div class="sides"><div><h2>Systembolaget · ${esc(it.article)}</h2>${dl(it.sb)}
        <a href="https://www.systembolaget.se/produkt/vin/${encodeURIComponent(it.article)}/" target="_blank" rel="noopener">systembolaget.se</a></div>
        ${cands || '<div class="muted">No candidate found on Vivino.</div>'}</div>
      <div class="actions">${it.cands.map((c, k) => btn("pick" + k, `${k + 1} is right`)).join("")}
        ${btn("wrong", "Neither")}${btn("none", "Not on Vivino")}${btn("unsure", "Unsure")}</div>
      <div class="actions"><input class="link ${a.link && wineRef(a.link) === null ? "bad" : ""}" data-i="${i}"
          placeholder="Right wine: vivino.com link or id (optional)" value="${esc(a.link || "")}">
        <input class="note" data-i="${i}" placeholder="Note (optional)" value="${esc(a.note || "")}"></div>
    </section>`;
  }).join("");
  const done = ITEMS.filter(it => { const a = answers[it.article] || {}; return a.v || a.link; }).length;
  document.getElementById("progress").textContent = `${done} of ${ITEMS.length} answered`;
}
function save() { try { localStorage.setItem(KEY, JSON.stringify(answers)); } catch (e) {} }
function answer(i, v) {
  answers[ITEMS[i].article] = { ...(answers[ITEMS[i].article] || {}), v };
  save(); cur = Math.min(i + 1, ITEMS.length - 1); render(); focusCur();
}
function focusCur() { document.getElementById("c" + cur)?.scrollIntoView({ block: "center", behavior: "smooth" }); }
list.addEventListener("click", e => { const b = e.target.closest("button[data-v]"); if (b) answer(+b.dataset.i, b.dataset.v); });
list.addEventListener("input", e => {
  const n = e.target.closest("input[data-i]"); if (!n) return;
  const it = ITEMS[+n.dataset.i];
  answers[it.article] = { ...(answers[it.article] || {}), [n.classList.contains("link") ? "link" : "note"]: n.value };
  n.classList.toggle("bad", n.classList.contains("link") && wineRef(n.value) === null);
  save();
});
list.addEventListener("focusin", e => { const n = e.target.closest("[data-i]"); if (n) cur = +n.dataset.i; });
document.addEventListener("keydown", e => {
  if (e.target.matches("input, textarea") || e.metaKey || e.ctrlKey) {
    if (e.key === "Escape" || e.key === "Enter") { e.target.blur(); render(); } return;
  }
  const k = e.key.toLowerCase(), it = ITEMS[cur];
  if ((k === "1" || k === "2") && it.cands[+k - 1]) answer(cur, "pick" + (+k - 1));
  else if (k === "w") answer(cur, "wrong"); else if (k === "n") answer(cur, "none"); else if (k === "u") answer(cur, "unsure");
  else if (k === "j" || e.key === "ArrowDown") { cur = Math.min(cur + 1, ITEMS.length - 1); render(); focusCur(); e.preventDefault(); }
  else if (k === "k" || e.key === "ArrowUp") { cur = Math.max(cur - 1, 0); render(); focusCur(); e.preventDefault(); }
  else if (k === "l" || k === "e") { document.querySelector(`input.${k === "l" ? "link" : "note"}[data-i="${cur}"]`)?.focus(); e.preventDefault(); }
});
// One CSV row per answered wine: the pairing judged, the verdict, and an override when the answer names another
// wine (a candidate that isn't the matched one, or a pasted link) or none.
function row(it, a) {
  const matched = it.current || it.cands[0]?.vivino_id || "";
  const link = wineRef(a.link);
  let vid = matched, verdict = "", override = link || "";
  if (a.v && a.v.startsWith("pick")) {
    vid = it.cands[+a.v.slice(4)].vivino_id; verdict = "right";
    if (vid !== it.current) override = vid;
  } else if (a.v === "wrong") verdict = "wrong";
  else if (a.v === "none") { verdict = "wrong"; override = "none"; }
  else if (a.v === "unsure") verdict = "unsure";
  else if (link) verdict = "wrong";
  if (!verdict) return null;
  if (!vid) { if (!override) return null; }
  return [it.article, vid, vid ? verdict : "", '"' + (a.note || "").replace(/"/g, '""') + '"', override].join(",");
}
document.getElementById("copy").addEventListener("click", () => {
  const rows = ITEMS.map(it => row(it, answers[it.article] || {})).filter(Boolean);
  const text = ["article,vivino_id,verdict,note,override", ...rows].join("\\n");
  const out = document.getElementById("out");
  const shown = () => { out.hidden = false; out.value = text; out.select(); document.getElementById("copied").textContent = "Select all and copy the text below."; };
  try { navigator.clipboard.writeText(text).then(() => { document.getElementById("copied").textContent = `Copied ${rows.length} answers. Paste them to Claude, or: python -m pipeline.run labels < answers.csv`; }, shown); }
  catch (e) { shown(); }
});
render();
</script></body></html>
"""

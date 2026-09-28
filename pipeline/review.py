"""Hand-check audit of accepted matches, one assortment group at a time.

`build(group, n)` samples n accepted wines of that group without a label yet (seeded), or with no group, every
group that still needs checks per the report (a sample, or all accepts for a small group), and writes
data/review.html: both sides' details, links to systembolaget.se and vivino.com/w/{id} for Adam to open himself,
keys R / W / U. The page holds Vivino data, so it stays in data/ (gitignored) and is opened as a local file.
Its "Copy answers" CSV is merged by worklist.import_answers (`python -m pipeline.run labels`).
"""

from __future__ import annotations

import csv
import json
import random

from . import state
from .net import DATA, load_json
from .orders import catalog

SEED = 20260927


def _wines() -> dict[str, dict]:
    """Systembolaget's side by article: today's wines (orders.catalog), and store 1208's phase 2 file for wines since
    gone."""
    old = load_json("store_1208.json")["wines"] if (DATA / "store_1208.json").exists() else []
    return {str(w["productNumber"]): w for w in [*old, *catalog().values()]}


def items(group: str, n: int) -> list[dict]:
    from .report import group_of
    wines = _wines()
    details = load_json("match_details.json")
    # Claude's first pass (data/first_pass.csv), shown on the page only after Adam answers; never a label.
    first = {}
    if (DATA / "first_pass.csv").exists():
        with (DATA / "first_pass.csv").open(newline="") as f:
            first = {(r["article"], r["vivino_id"]): r for r in csv.DictReader(f)}
    labels = state.labels()
    # Wines matched elsewhere (the nightly on GitHub) have no evidence here until a local `match` run.
    pool = sorted(a for a, r in state.matches().items()
                  if r["band"] == "accept" and group_of(r) == group and (a, r["vivino_id"]) not in labels
                  and a in details and a in wines)
    picked = random.Random(f"{SEED}-{group}").sample(pool, min(n, len(pool)))
    out = []
    for a in picked:
        w, b = wines[a], details[a]["best"]
        s = details[a].get("second")
        fp = first.get((a, str(b["vivino_id"])), {"verdict": "not reviewed", "reason": "matcher accept"})
        out.append({"article": a, "vivino_id": str(b["vivino_id"]), "claude": fp["verdict"], "reason": fp["reason"],
                    "sb": sb_side(w, group), "vv": vv_side(b),
                    "second": f"{s['name']} ({s['winery']})" if s else ""})
    return out


def sb_side(w: dict, group: str) -> dict:
    """Systembolaget's facts for a hand check."""
    return {"Name": f"{w.get('productNameBold') or ''} {w.get('productNameThin') or ''}".strip(),
            "Producer": w.get("producerName"), "Supplier": w.get("supplierName"), "Vintage": w.get("vintage"),
            "Country": w.get("country"),
            "Region": " / ".join(x for x in (w.get("originLevel1"), w.get("originLevel2")) if x),
            "Grapes": ", ".join(g for g in w.get("grapes") or [] if isinstance(g, str)),
            "Alcohol": f"{w['alcoholPercentage']} %" if w.get("alcoholPercentage") else "",
            "Sugar": f"{w['sugarContentGramPer100ml'] * 10:g} g/l" if w.get("sugarContentGramPer100ml") else "",
            "Type": w.get("categoryLevel2"), "Volume": f"{w['volume']} ml" if w.get("volume") else "",
            "Packaging": w.get("packagingLevel1"), "Organic": "yes" if w.get("isOrganic") else "no",
            "Assortment": group}


def vv_side(c: dict) -> dict:
    """A Vivino candidate's facts (from the match evidence) for a hand check."""
    return {"Name": c["name"], "Winery": c["winery"], "Country": c["country"], "Region": c.get("region", ""),
            "Alcohol": f"{c['alcohol']} %" if c.get("alcohol") not in ("", 0, "0", None) else "",
            "Vintages": c.get("years", ""), "Ratings": c.get("algolia_count", "")}


def planned() -> list[dict]:
    """Every group that still needs checks, as many as the report says: a sample, or all accepts for a census."""
    from .report import groups
    its: list[dict] = []
    for g, v in groups().items():
        if v["accept"] and v["more_to_check"] > 0:
            its += items(g, v["more_to_check"])
    return its


def build(group: str | None, n: int) -> tuple[int, str]:
    its = items(group, n) if group else planned()
    page = PAGE.replace("__ITEMS__", json.dumps(its, ensure_ascii=False).replace("</", "<\\/"))
    path = DATA / "review.html"
    path.write_text(page)
    return len(its), str(path)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Match Review</title>
<style>
  :root { --bg:#f7f6f4; --card:#fff; --ink:#1f1a1c; --muted:#6c6367; --rule:#e4dee1; --accent:#7a1f3d;
          --right:#1f7a4d; --wrong:#b3261e; --unsure:#9a6700; }
  @media (prefers-color-scheme: dark) { :root { --bg:#151214; --card:#1e191c; --ink:#eee8ea; --muted:#a79ba1;
          --rule:#342a2f; --accent:#e08aa8; --right:#5fc794; --wrong:#f08a82; --unsure:#e0b35a; } }
  body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui, -apple-system, sans-serif; }
  .wrap { max-width:980px; margin:0 auto; padding:24px 16px 80px; display:grid; gap:16px; }
  header { display:flex; flex-wrap:wrap; gap:12px; align-items:baseline; justify-content:space-between; }
  h1 { margin:0; font-size:22px; }
  .muted { color:var(--muted); }
  .bar { position:sticky; top:0; background:var(--bg); padding:8px 0; display:flex; gap:8px; flex-wrap:wrap;
         align-items:center; border-bottom:1px solid var(--rule); z-index:2; }
  .card { background:var(--card); border:1px solid var(--rule); border-radius:8px; padding:16px; display:grid; gap:12px; }
  .card.current { outline:2px solid var(--accent); }
  .sides { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media (max-width:640px) { .sides { grid-template-columns:1fr; } }
  h2 { margin:0 0 6px; font-size:13px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }
  dl { margin:0; display:grid; grid-template-columns:max-content 1fr; gap:2px 12px; }
  dt { color:var(--muted); } dd { margin:0; overflow-wrap:anywhere; }
  dd.name { font-weight:600; }
  .actions { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  button { font:600 14px/1 system-ui, sans-serif; border:1px solid var(--rule); background:var(--card); color:var(--ink);
           border-radius:6px; padding:9px 14px; cursor:pointer; }
  button:focus-visible, input:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  button.on[data-v=right] { background:var(--right); color:#fff; border-color:var(--right); }
  button.on[data-v=wrong] { background:var(--wrong); color:#fff; border-color:var(--wrong); }
  button.on[data-v=unsure] { background:var(--unsure); color:#fff; border-color:var(--unsure); }
  input.note { flex:1; min-width:180px; font:14px system-ui, sans-serif; padding:8px; border:1px solid var(--rule);
               border-radius:6px; background:var(--bg); color:var(--ink); }
  .claude { font-size:13px; color:var(--muted); }
  a { color:var(--accent); }
  textarea { width:100%; min-height:120px; font:12px ui-monospace, monospace; }
  kbd { font:12px ui-monospace, monospace; border:1px solid var(--rule); border-radius:4px; padding:1px 5px; }
</style></head><body><div class="wrap">
<header><h1>Match review</h1><span class="muted" id="progress"></span></header>
<p class="muted">Is the Vivino wine on the right the same wine as the Systembolaget one on the left? Different
vintages of the same wine count as right. Keys: <kbd>R</kbd> right, <kbd>W</kbd> wrong, <kbd>U</kbd> unsure,
<kbd>J</kbd>/<kbd>K</kbd> next/previous, <kbd>N</kbd> note. Open the links only when the details aren't enough.</p>
<div class="bar"><button id="copy" type="button">Copy answers</button><span class="muted" id="copied"></span></div>
<div id="list"></div>
<textarea id="out" hidden readonly></textarea>
</div>
<script>
const ITEMS = __ITEMS__;
const KEY = "wine-v2-review-" + ITEMS.map(i => i.article).join("-");
let answers = {};
try { answers = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
let cur = 0;
const list = document.getElementById("list");
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function dl(o) {
  return "<dl>" + Object.entries(o).filter(([, v]) => v !== "" && v != null)
    .map(([k, v], i) => `<dt>${esc(k)}</dt><dd class="${i === 0 ? "name" : ""}">${esc(v)}</dd>`).join("") + "</dl>";
}
function render() {
  list.innerHTML = ITEMS.map((it, i) => {
    const a = answers[it.article] || {};
    const btn = v => `<button type="button" data-i="${i}" data-v="${v}" class="${a.verdict === v ? "on" : ""}">${v}</button>`;
    return `<section class="card ${i === cur ? "current" : ""}" id="c${i}">
      <div class="sides"><div><h2>Systembolaget · ${esc(it.article)}</h2>${dl(it.sb)}
        <a href="https://www.systembolaget.se/sok/?textQuery=${encodeURIComponent(it.article)}" target="_blank" rel="noopener">systembolaget.se</a></div>
      <div><h2>Vivino · ${esc(it.vivino_id)}</h2>${dl(it.vv)}
        <a href="https://www.vivino.com/w/${encodeURIComponent(it.vivino_id)}" target="_blank" rel="noopener">vivino.com</a></div></div>
      ${it.second ? `<div class="muted">Runner-up: ${esc(it.second)}</div>` : ""}
      <div class="actions">${btn("right")}${btn("wrong")}${btn("unsure")}
        <input class="note" data-i="${i}" placeholder="Note (optional)" value="${esc(a.note || "")}"></div>
      ${a.verdict ? `<div class="claude">Claude's first pass: ${esc(it.claude)} (${esc(it.reason)})</div>` : ""}
    </section>`;
  }).join("");
  const done = ITEMS.filter(it => (answers[it.article] || {}).verdict).length;
  document.getElementById("progress").textContent = `${done} of ${ITEMS.length} answered`;
}
function save() { try { localStorage.setItem(KEY, JSON.stringify(answers)); } catch (e) {} }
function setVerdict(i, v) {
  const it = ITEMS[i];
  answers[it.article] = { ...(answers[it.article] || {}), verdict: v, vivino_id: it.vivino_id };
  save(); cur = Math.min(i + 1, ITEMS.length - 1); render(); focusCur();
}
function focusCur() { document.getElementById("c" + cur)?.scrollIntoView({ block: "center", behavior: "smooth" }); }
list.addEventListener("click", e => {
  const b = e.target.closest("button[data-v]"); if (b) setVerdict(+b.dataset.i, b.dataset.v);
});
list.addEventListener("input", e => {
  const n = e.target.closest("input.note"); if (!n) return;
  const it = ITEMS[+n.dataset.i];
  answers[it.article] = { ...(answers[it.article] || {}), note: n.value, vivino_id: it.vivino_id }; save();
});
list.addEventListener("focusin", e => { const n = e.target.closest("[data-i]"); if (n && +n.dataset.i !== cur) { cur = +n.dataset.i; } });
document.addEventListener("keydown", e => {
  if (e.target.matches("input, textarea") || e.metaKey || e.ctrlKey) {
    if (e.key === "Escape") e.target.blur(); return;
  }
  const k = e.key.toLowerCase();
  if (k === "r") setVerdict(cur, "right"); else if (k === "w") setVerdict(cur, "wrong");
  else if (k === "u") setVerdict(cur, "unsure");
  else if (k === "j" || e.key === "ArrowDown") { cur = Math.min(cur + 1, ITEMS.length - 1); render(); focusCur(); e.preventDefault(); }
  else if (k === "k" || e.key === "ArrowUp") { cur = Math.max(cur - 1, 0); render(); focusCur(); e.preventDefault(); }
  else if (k === "n") { document.querySelector(`input.note[data-i="${cur}"]`)?.focus(); e.preventDefault(); }
});
document.getElementById("copy").addEventListener("click", () => {
  const lines = ["article,vivino_id,verdict,note"].concat(ITEMS.filter(it => (answers[it.article] || {}).verdict)
    .map(it => { const a = answers[it.article]; return [it.article, it.vivino_id, a.verdict, '"' + (a.note || "").replace(/"/g, '""') + '"'].join(","); }));
  const text = lines.join("\\n"); const out = document.getElementById("out");
  const shown = () => { out.hidden = false; out.value = text; out.select(); document.getElementById("copied").textContent = "Select all and copy the text below."; };
  try { navigator.clipboard.writeText(text).then(() => { document.getElementById("copied").textContent = `Copied ${lines.length - 1} answers. Paste them to Claude.`; }, shown); }
  catch (e) { shown(); }
});
render();
</script></body></html>
"""

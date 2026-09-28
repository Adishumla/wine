// Wine by Store: pick a Systembolaget store, filter and sort its wines by Vivino rating.
// A rating shows only for an accepted or hand-set match; anything else is "no rating", never a guess.

import { loadAll, loadStore, norm, status } from './data.js';
import { VList } from './vlist.js';

const $ = s => document.querySelector(s);
const ROW_H = 76;
const RATED = new Set(['accept', 'override', 'checked']);
const DEFAULTS = { q: '', sort: 'adj', type: '', price: 0, rating: 0, count: 0, country: '', stock: true, rated: false,
  organic: false, newdays: 0, gems: false };
const PRICES = [80, 100, 125, 150, 200, 250, 300, 400, 500, 750, 1000];
const RATINGS = [3.5, 3.7, 3.8, 3.9, 4.0, 4.1, 4.2, 4.4];
const COUNTS = [25, 100, 500, 1000, 5000, 10000];
const MINE = 'mine'; // the store choice "any of my stores"
const ORDERS = 'orders'; // the store choice "order only": the range ordered to any store for pickup
// Quick picks: one tap sets these filters (everything else back to defaults); tapping again clears them.
const PRESETS = [
  { id: 'under150', label: 'Best under 150 kr', f: { price: 150, sort: 'adj', rated: true } },
  { id: 'top', label: 'Top rated here', f: { sort: 'adj', rated: true } },
  { id: 'value', label: 'Best value', f: { sort: 'value', rated: true } },
  { id: 'new', label: 'New this month', f: { newdays: 30, sort: 'new' } },
  { id: 'crowd', label: 'Crowd favourites', f: { count: 10000, sort: 'adj' } },
  { id: 'gems', label: 'Hidden gems', f: { gems: true, sort: 'adj' } },
];

const nf = new Intl.NumberFormat('sv-SE');
const nf1 = new Intl.NumberFormat('sv-SE', { maximumFractionDigits: 1 });
const dateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
const today = new Date().toISOString().slice(0, 10);

const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(`wine.${k}`)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(`wine.${k}`, JSON.stringify(v)); } catch { /* private mode: not remembered */ } },
};

const S = { wines: [], stores: [], meta: {}, storeId: null, items: [], orders: {}, f: { ...DEFAULTS, ...store.get('filters'), q: '' },
  favs: new Set(store.get('favs') || []) };
const saveFavs = () => store.set('favs', [...S.favs]);

// "Wrong match?" files a prefilled issue in the private repo (meta.issues). The phone remembers the report and
// shows the wine without its rating until a build knows about it (meta.reported: "article:vivino id"), which then
// shows it as reported itself.
S.reports = store.get('reports') || {}; // article -> { viv, at }
const saveReports = () => store.set('reports', S.reports);
const HIDDEN = ['rating', 'count', 'adj', 'value', 'band'];
function hide(w) {
  if (w.saved) return;
  w.saved = Object.fromEntries(HIDDEN.map(k => [k, w[k]]));
  Object.assign(w, { rating: null, count: null, adj: null, value: null, band: w.viv ? 'reported' : w.band });
}
function unhide(w) {
  if (w.saved) Object.assign(w, w.saved);
  delete w.saved;
}
function applyReports() {
  const known = new Set(S.meta.reported || []);
  for (const w of S.wines) {
    const r = S.reports[w.art];
    if (!r) continue;
    if (known.has(`${w.art}:${r.viv ?? ''}`) || (w.viv ?? null) !== r.viv) delete S.reports[w.art];
    else hide(w);
  }
  saveReports();
}
function issueUrl(w) {
  const body = [`Article: ${w.art}`, `Wine: ${[w.title, w.prod, w.vint].filter(Boolean).join(', ')}`,
    `Matched to: ${w.viv ? `https://www.vivino.com/w/${w.viv} (${w.band})` : 'nothing'}`, '',
    "What's wrong (optional):", '', '', "Right wine on Vivino, a link or 'none' (optional):", '', ''].join('\n');
  return `${S.meta.issues}?title=${encodeURIComponent(`Match: ${w.art} ${w.title}`)}&body=${encodeURIComponent(body)}`;
}
function reportLine(w) {
  if (!S.meta.issues) return '';
  const r = S.reports[w.art];
  if (r) {
    return `<p class="report">You reported this match on ${esc(date(r.at))}${w.viv ? '; no rating until it’s checked' : ''}.
      <button type="button" class="linkbtn" data-undo="${w.i}">Undo</button></p>`;
  }
  if (w.band === 'reported') return '';
  const label = w.viv && RATED.has(w.band) ? 'Wrong match?' : 'Know it on Vivino?';
  return `<p class="report"><a href="${esc(issueUrl(w))}" target="_blank" rel="noopener" data-report="${w.i}">${label}</a></p>`;
}

// ---------- formatting

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const price = p => (p == null ? '' : `${Number.isInteger(p) ? nf.format(p) : p.toFixed(2).replace('.', ',')} kr`);
const compact = n => (n < 1000 ? String(n) : n < 10000 ? `${(n / 1000).toFixed(1)}k` : `${Math.round(n / 1000)}k`);
const volume = ml => (ml >= 1000 ? `${nf1.format(ml / 1000)} l` : `${nf.format(ml)} ml`);
const date = d => (d ? dateFmt.format(new Date(`${d}T12:00:00`)) : '');
const tier = r => (r == null ? 'r0' : r >= 4.2 ? 'r5' : r >= 4.0 ? 'r4' : r >= 3.8 ? 'r3' : r >= 3.5 ? 'r2' : 'r1');
const storeName = id => (id === MINE ? 'Any of my stores' : id === ORDERS ? 'Order only'
  : S.stores.find(s => s.id === id)?.name ?? id);
const daysAgo = n => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
const km = (lat1, lng1, lat2, lng2) => {
  const r = Math.PI / 180, x = (lng2 - lng1) * r * Math.cos(((lat1 + lat2) / 2) * r), y = (lat2 - lat1) * r;
  return 6371 * Math.hypot(x, y);
};
// Bottle photos: the 60 px copy on this site when the nightly has one (works offline), else Systembolaget's image
// CDN (widths 20-800 px). The large detail photo always comes from the CDN, online only.
const cdn = (id, width) => `https://product-cdn.systembolaget.se/productimages/${encodeURIComponent(id)}/${encodeURIComponent(id)}_${width}.webp`;
const thumb = w => (w.img ? `data/img/${encodeURIComponent(w.id)}.webp` : cdn(w.id, 60));
const shortDate = t => t.slice(5).replace(/^(\d\d)-(\d\d)/, (_, mo, d) => `${+d}/${+mo}`); // "2026-09-27 21:10" -> "27/9 21:10"

// Not launched yet: "arrives", even when bottles are already in the store (they can't be sold before launch day).
const upcoming = it => it.w.launched > today;
const inStock = it => (it.order || it.stock > 0) && !upcoming(it); // an order wine counts as available
function stockText(it) {
  if (upcoming(it)) return `Arrives ${date(it.w.launched).replace(/ \d{4}$/, '')}`;
  if (it.order) return it.w.stores ? `Order · on shelves in ${nf.format(it.w.stores)}` : 'Order to any store';
  if (it.stock == null) return 'Not in store yet';
  if (it.multi && it.stock > 0) {
    const n = it.multi.filter(x => x.stock > 0).length;
    return `${nf.format(it.stock)} in stock · ${n} ${n === 1 ? 'store' : 'stores'}`;
  }
  return it.stock > 0 ? `${nf.format(it.stock)} in stock` : 'Sold out';
}
const stockClass = it => (it.order && !upcoming(it) ? 'order' : upcoming(it) || it.stock == null ? 'soon' : it.stock > 0 ? 'ok' : 'out');

// ---------- sorting and filtering

const cmp = (key, dir) => (a, b) => {
  const x = a.w[key], y = b.w[key];
  if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1; // missing values last, both ways
  return x < y ? -dir : x > y ? dir : 0;
};
const byName = (a, b) => a.w.rank - b.w.rank;
const SORTS = {
  adj: [cmp('adj', -1), cmp('count', -1)],
  rating: [cmp('rating', -1), cmp('count', -1)],
  count: [cmp('count', -1), cmp('rating', -1)],
  price: [cmp('price', 1)],
  'price-desc': [cmp('price', -1)],
  new: [cmp('launched', -1)],
  value: [cmp('value', -1), cmp('adj', -1)],
};

function order(sort) {
  if (!S.orders[sort]) {
    const cs = [...(SORTS[sort] || SORTS.adj), byName];
    S.orders[sort] = [...S.items].sort((a, b) => { for (const c of cs) { const d = c(a, b); if (d) return d; } return 0; });
  }
  return S.orders[sort];
}

function passes(it, f, toks) {
  const w = it.w;
  if (f.stock && !inStock(it)) return false;
  if (f.rated && w.rating == null) return false;
  if (f.organic && !w.organic) return false;
  if (f.type && w.type !== f.type) return false;
  if (f.country && w.country !== f.country) return false;
  if (f.price && !(w.price <= f.price)) return false;
  if (f.rating && !(w.rating >= f.rating)) return false;
  if (f.count && !(w.count >= f.count)) return false;
  if (f.newdays && !(w.launched && w.launched <= today && w.launched >= daysAgo(f.newdays))) return false;
  if (f.gems && !(w.rating >= 4.0 && w.count < 500)) return false;
  return !toks.length || toks.every(t => w.hay.includes(t));
}

function apply({ keepScroll = false } = {}) {
  const f = S.f;
  const toks = norm(f.q).split(/\s+/).filter(Boolean);
  const out = [];
  let rated = 0;
  for (const it of order(f.sort)) {
    if (!passes(it, f, toks)) continue;
    out.push(it);
    if (it.w.rating != null) rated++;
  }
  if (!keepScroll && scrollY > 0) scrollTo(0, 0);
  list.setItems(out);
  $('#summary').textContent = S.storeId ? `${nf.format(out.length)} ${out.length === 1 ? 'wine' : 'wines'} · ${nf.format(rated)} rated` : '';
  $('#empty').hidden = out.length > 0 || !S.storeId;
  syncControls();
  store.set('filters', { ...f, q: '' });
}

// ---------- controls

function options(sel, opts, value) {
  sel.innerHTML = opts.map(([v, label]) => `<option value="${esc(v)}">${esc(label)}</option>`).join('');
  sel.value = String(value);
}

function countBy(key) {
  const m = new Map();
  for (const it of S.items) { const v = it.w[key]; if (v) m.set(v, (m.get(v) || 0) + 1); }
  return [...m].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'sv'));
}

function buildOptions() {
  const types = countBy('type'), countries = countBy('country');
  if (S.f.type && !types.some(([t]) => t === S.f.type)) S.f.type = '';
  if (S.f.country && !countries.some(([c]) => c === S.f.country)) S.f.country = '';
  options($('#type'), [['', 'All types'], ...types.map(([t, n]) => [t, `${t} (${n})`])], S.f.type);
  options($('#country'), [['', 'All countries'], ...countries.map(([c, n]) => [c, `${c} (${n})`])], S.f.country);
  options($('#price'), [[0, 'Any price'], ...PRICES.map(p => [p, `Up to ${nf.format(p)} kr`])], S.f.price);
  options($('#rating'), [[0, 'Any rating'], ...RATINGS.map(r => [r, `★ ${r.toFixed(1)}+`])], S.f.rating);
  options($('#count'), [[0, 'Any no. of ratings'], ...COUNTS.map(c => [c, `${nf.format(c)}+ ratings`])], S.f.count);
}

function syncControls() {
  $('#sort').value = S.f.sort;
  for (const sel of document.querySelectorAll('select[data-key]')) {
    sel.value = String(S.f[sel.dataset.key]);
    sel.parentElement.classList.toggle('on', S.f[sel.dataset.key] !== DEFAULTS[sel.dataset.key]);
  }
  for (const b of document.querySelectorAll('.tog')) b.setAttribute('aria-pressed', String(S.f[b.dataset.key]));
  const active = activePreset();
  for (const b of document.querySelectorAll('.qp')) b.setAttribute('aria-pressed', String(b.dataset.id === active?.id));
  $('#reset').hidden = Object.keys(DEFAULTS).every(k => k === 'sort' || S.f[k] === DEFAULTS[k]);
}

function activePreset() {
  return PRESETS.find(p => Object.keys(DEFAULTS).every(k => k === 'q' || S.f[k] === (k in p.f ? p.f[k] : DEFAULTS[k])));
}

$('#quick').innerHTML = PRESETS.map(p => `<button class="chip qp" type="button" data-id="${p.id}" aria-pressed="false">${esc(p.label)}</button>`).join('');
$('#quick').addEventListener('click', e => {
  const b = e.target.closest('.qp');
  if (!b) return;
  const p = PRESETS.find(x => x.id === b.dataset.id);
  S.f = activePreset() === p ? { ...DEFAULTS } : { ...DEFAULTS, ...p.f };
  $('#q').value = '';
  apply();
});

function reset() {
  S.f = { ...DEFAULTS, sort: S.f.sort };
  $('#q').value = '';
  apply();
}

$('#q').addEventListener('input', e => { S.f.q = e.target.value; apply(); });
$('#sort').addEventListener('change', e => { S.f.sort = e.target.value; apply(); });
for (const sel of document.querySelectorAll('select[data-key]')) {
  sel.addEventListener('change', () => {
    const k = sel.dataset.key;
    S.f[k] = typeof DEFAULTS[k] === 'number' ? Number(sel.value) : sel.value;
    apply();
  });
}
for (const b of document.querySelectorAll('.tog')) {
  b.addEventListener('click', () => { S.f[b.dataset.key] = !S.f[b.dataset.key]; apply(); });
}
$('#reset').addEventListener('click', reset);
$('#empty-reset').addEventListener('click', reset);
addEventListener('keydown', e => {
  if (e.key === '/' && !document.querySelector('dialog[open]') && document.activeElement !== $('#q')) {
    e.preventDefault();
    $('#q').focus();
  }
});

// ---------- list

const rowTpl = $('#row-tpl').content.firstElementChild;
const list = new VList($('#list'), ROW_H, () => {
  const n = rowTpl.cloneNode(true);
  n._f = { avg: n.querySelector('.avg'), cnt: n.querySelector('.cnt'), score: n.querySelector('.score'), n: n.querySelector('.n'),
    t: n.querySelector('.t'), sub: n.querySelector('.sub'), stock: n.querySelector('.stock'), st: n.querySelector('.st'),
    shelf: n.querySelector('.shelf'), price: n.querySelector('.p'), vol: n.querySelector('.vol'),
    img: n.querySelector('.bottle') };
  n._f.img.addEventListener('error', () => n._f.img.classList.add('none')); // no photo: an empty slot, not a broken icon
  return n;
}, (n, it) => {
  const w = it.w, f = n._f;
  const src = thumb(w);
  if (f.img.dataset.src !== src) {
    f.img.dataset.src = src;
    f.img.classList.remove('none');
    f.img.src = src;
  }
  f.score.className = `score ${tier(w.rating)}`;
  f.avg.textContent = w.rating != null ? w.rating.toFixed(1) : '–';
  f.cnt.textContent = w.rating != null ? compact(w.count) : 'no rating';
  f.n.textContent = w.name;
  f.t.textContent = w.thin || '';
  f.sub.textContent = [w.prod, w.vint, w.country].filter(Boolean).join(' · ');
  f.stock.className = `stock ${stockClass(it)}`;
  f.st.textContent = stockText(it);
  f.shelf.textContent = it.shelf ? `Shelf ${it.shelf}` : '';
  f.price.textContent = price(w.price);
  f.vol.textContent = w.vol && w.vol !== 750 ? volume(w.vol) : ''; // a half bottle or a box shouldn't pass for a bottle
});

$('#list').addEventListener('click', e => { const it = list.itemAt(e.target.closest('.row')); if (it) openDetail(it); });
$('#list').addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const it = list.itemAt(e.target.closest('.row'));
  if (it) { e.preventDefault(); openDetail(it); }
});

// ---------- dialogs (the phone's back button closes them)

history.scrollRestoration = 'manual'; // closing a sheet goes back one entry; that mustn't move the list

function openDialog(dlg) {
  if (dlg.open) return;
  dlg.showModal();
  history.pushState({ dlg: dlg.id }, '');
}
for (const dlg of document.querySelectorAll('dialog')) {
  dlg.addEventListener('close', () => { if (history.state?.dlg === dlg.id) history.back(); });
  dlg.addEventListener('click', e => { if (e.target === dlg || e.target.closest('.close')) dlg.close(); });
}
addEventListener('popstate', () => { for (const d of document.querySelectorAll('dialog[open]')) d.close(); });

// Report a match (the link opens the prefilled issue in a new tab), or take a report back.
$('#detail').addEventListener('click', e => {
  const it = S.detailItem, w = it?.w;
  if (!w) return;
  if (e.target.closest('[data-report]')) {
    S.reports[w.art] = { viv: w.viv ?? null, at: today };
    hide(w);
  } else if (e.target.closest('[data-undo]')) {
    delete S.reports[w.art];
    unhide(w);
  } else return;
  saveReports();
  apply({ keepScroll: true });
  openDetail(it);
});

// ---------- store picker

function storeRow(s) {
  const fav = S.favs.has(s.id);
  return `<div class="store-row">
    <button type="button" class="store${s.id === S.storeId ? ' current' : ''}" data-id="${esc(s.id)}">
      <span class="sn">${esc(s.name)}</span>
      <span class="sa">${esc([s.name === s.address ? '' : s.address, s.town].filter(Boolean).join(', '))}</span>
      <span class="sw">${nf.format(s.wines)} wines</span>
    </button>
    <button type="button" class="fav${fav ? ' on' : ''}" data-fav="${esc(s.id)}" aria-pressed="${fav}"
      aria-label="${fav ? 'Unpin' : 'Pin'} ${esc(s.name)}">${fav ? '★' : '☆'}</button>
  </div>`;
}

function renderStores() {
  const toks = norm($('#store-q').value).split(/\s+/).filter(Boolean);
  const rows = S.stores.filter(s => toks.every(t => s.hay.includes(t)));
  let html = '';
  const favs = S.stores.filter(s => S.favs.has(s.id));
  const orders = S.meta.orders && toks.every(t => 'order only bestallning'.includes(t)) ? `<div class="store-row">
    <button type="button" class="store${S.storeId === ORDERS ? ' current' : ''}" data-id="${ORDERS}">
      <span class="sn">Order only</span><span class="sa">Order on systembolaget.se, pick up in any store</span>
      <span class="sw">${nf.format(S.meta.orders.wines)} wines</span></button></div>` : '';
  if (!toks.length && favs.length) {
    html += `<h3 class="sl-head">Your stores</h3>
      <div class="store-row"><button type="button" class="store${S.storeId === MINE ? ' current' : ''}" data-id="${MINE}">
        <span class="sn">Any of my stores</span><span class="sa">In stock in at least one of ${favs.length}</span>
        <span class="sw">★</span></button></div>
      ${favs.map(storeRow).join('')}${orders}<h3 class="sl-head">All stores</h3>`;
  } else {
    html += orders;
  }
  html += rows.length ? rows.map(storeRow).join('') : '<p class="empty">No store matches.</p>';
  $('#store-list').innerHTML = html;
}

// Store compare: favourites and the nearest stores (GPS, asked only now), ranked by wines matching the filters.
async function compareStores() {
  const box = $('#store-list');
  box.innerHTML = '<p class="empty">Comparing…</p>';
  const pos = await new Promise(res => {
    if (!navigator.geolocation) return res(null);
    navigator.geolocation.getCurrentPosition(p => res(p.coords), () => res(null), { maximumAge: 600000, timeout: 10000 });
  });
  const dist = s => (pos && s.lat != null ? km(pos.latitude, pos.longitude, s.lat, s.lng) : null);
  const cands = new Set(S.stores.filter(s => S.favs.has(s.id)));
  if (pos) for (const s of S.stores.filter(s => s.lat != null).sort((a, b) => dist(a) - dist(b)).slice(0, 10)) cands.add(s);
  if (!cands.size) {
    box.innerHTML = '<p class="empty">Allow location, or pin stores with ☆, to compare.</p>';
    return;
  }
  const toks = norm(S.f.q).split(/\s+/).filter(Boolean);
  const rows = await Promise.all([...cands].map(async s => {
    const m = await availOf(s.id).catch(() => null);
    let n = null;
    if (m) {
      n = 0;
      for (const [i, [stock, shelf]] of m) if (passes({ w: S.wines[i], stock, shelf }, S.f, toks)) n++;
    }
    return { s, n, d: dist(s) };
  }));
  rows.sort((a, b) => (b.n ?? -1) - (a.n ?? -1) || (a.d ?? 1e9) - (b.d ?? 1e9));
  box.innerHTML = '<h3 class="sl-head">Wines matching your filters</h3>' + rows.map(({ s, n, d }) => `<div class="store-row">
    <button type="button" class="store${s.id === S.storeId ? ' current' : ''}" data-id="${esc(s.id)}">
      <span class="sn">${esc(s.name)}${S.favs.has(s.id) ? ' ★' : ''}</span>
      <span class="sa">${esc([s.town, d != null ? `${nf1.format(d)} km` : ''].filter(Boolean).join(' · '))}</span>
      <span class="sw">${n == null ? 'couldn’t load' : `${nf.format(n)} ${n === 1 ? 'wine' : 'wines'}`}</span>
    </button></div>`).join('');
}
$('#compare-btn').addEventListener('click', compareStores);

$('#store-btn').addEventListener('click', () => {
  $('#store-q').value = '';
  renderStores();
  openDialog($('#store-dlg'));
  const cur = $('#store-list .current'), box = $('#store-list');
  if (cur) box.scrollTop = cur.offsetTop - box.offsetTop - box.clientHeight / 2; // not scrollIntoView: that scrolls the page too
});
$('#store-q').addEventListener('input', renderStores);
$('#store-q').addEventListener('keydown', e => {
  if (e.key === 'Enter') $('#store-list .store')?.click();
});
$('#store-list').addEventListener('click', e => {
  const f = e.target.closest('.fav');
  if (f) {
    const id = f.dataset.fav;
    if (S.favs.has(id)) S.favs.delete(id); else S.favs.add(id);
    saveFavs();
    renderStores();
    return;
  }
  const b = e.target.closest('.store');
  if (!b) return;
  $('#store-dlg').close();
  pickStore(b.dataset.id);
});

// A store's wines, loaded once per session: wine index -> [stock, shelf] (stock null = not stocked yet).
const avail = new Map();
function availOf(sid) {
  if (!avail.has(sid)) {
    avail.set(sid, loadStore(sid)
      .then(rows => new Map(rows.map(([i, stock, shelf]) => [i, [stock, shelf]])))
      .catch(err => { avail.delete(sid); throw err; }));
  }
  return avail.get(sid);
}

// "Any of my stores": every wine carried by a pinned store; stock is the total over those stores.
async function mineItems() {
  const ids = [...S.favs].filter(id => S.stores.some(s => s.id === id));
  const maps = await Promise.all(ids.map(availOf));
  const by = new Map();
  maps.forEach((m, k) => {
    for (const [i, [stock, shelf]] of m) {
      let it = by.get(i);
      if (!it) by.set(i, (it = { w: S.wines[i], stock: null, shelf: null, multi: [] }));
      it.multi.push({ id: ids[k], stock, shelf });
      if (stock != null) it.stock = (it.stock ?? 0) + Math.max(0, stock);
    }
  });
  return [...by.values()];
}

async function pickStore(id) {
  S.storeId = id;
  store.set('store', id);
  $('#store-name').textContent = storeName(id);
  $('#summary').textContent = 'Loading…';
  let items;
  try {
    items = id === MINE ? await mineItems()
      : [...(await availOf(id))].map(([i, [stock, shelf]]) => ({ w: S.wines[i], stock, shelf, order: id === ORDERS }));
  } catch (err) {
    $('#summary').textContent = `Couldn't load this store (${err.message}).`;
    return;
  }
  if (S.storeId !== id) return; // another store was picked meanwhile
  S.items = items;
  S.orders = {};
  const st = S.stores.find(s => s.id === id);
  const note = $('#note');
  note.hidden = !st?.stale && !st?.partial;
  note.textContent = st?.stale
    ? `This store's list couldn't be checked in the last update; it's from ${st.chk ?? 'an earlier run'}.`
    : 'This store’s list may be missing a few wines: the last update couldn’t check it.';
  $('#fresh').textContent = st?.chk ? `Stock ${shortDate(st.chk)}`
    : id === ORDERS && S.meta.orders?.checked_at ? `Range ${shortDate(S.meta.orders.checked_at)}` : '';
  buildOptions();
  apply();
}

// ---------- detail

function ratingCard(w) {
  const vivino = w.viv && RATED.has(w.band);
  if (w.rating != null) {
    return `<div class="card ${tier(w.rating)}">
      <div class="k">Vivino</div>
      <div class="v">★ ${w.rating.toFixed(1)} <small>${nf.format(w.count)} ratings</small></div>
      <div class="s">Adjusted ${w.adj.toFixed(2)} · checked ${esc(date(w.checked))}</div></div>`;
  }
  const why = vivino ? (w.count ? 'Too few ratings on Vivino' : 'Rating not fetched yet')
    : { review: 'Match not confirmed', unchecked: 'Match not checked yet', reject: 'Not found on Vivino', override: 'Not on Vivino',
      reported: 'Reported as wrong' }[w.band] ?? 'Not matched yet';
  return `<div class="card r0"><div class="k">Vivino</div><div class="v">No rating</div><div class="s">${why}</div></div>`;
}

function matchText(w) {
  if (w.band === 'accept') return 'Matched to Vivino automatically (hand-checked samples: ≥ 95 % right).';
  if (w.band === 'override') return w.viv ? 'Matched to Vivino by hand.' : 'Checked by hand: not on Vivino.';
  if (w.band === 'checked') return 'Matched to Vivino and checked by hand.';
  if (w.band === 'reported') return 'Reported as a wrong match: no rating until it’s checked.';
  if (w.band === 'review') return 'A possible Vivino match isn’t confirmed yet, so no rating is shown.';
  if (w.band === 'unchecked') return 'Likely matched to Vivino, but this group of new wines hasn’t been hand-checked yet, so no rating is shown.';
  if (w.band === 'reject') return 'No matching wine found on Vivino.';
  return 'Not matched to Vivino yet.';
}

function openDetail(it) {
  const w = it.w;
  const here = storeName(S.storeId);
  const facts = [
    ['Country', w.country], ['Region', w.region], ['Grapes', w.grapes.join(', ')], ['Style', w.style],
    ['Vintage', w.vint], ['Volume', w.vol && volume(w.vol)], ['Alcohol', w.abv != null && `${nf1.format(w.abv)} %`],
    ['Sugar', w.sugar != null && `${nf1.format(w.sugar * 10)} g/l`], ['Packaging', w.packaging],
    ['Assortment', w.assortment], ['Organic', w.organic && 'Yes'], ['Launched', date(w.launched)],
    ['Article no.', w.art], ['Stores', w.stores && `Carried by ${nf.format(w.stores)} stores`],
  ].filter(([, v]) => v);
  $('#detail').innerHTML = `
    <div class="sheet-head">
      <p class="kind">${esc([w.type, w.organic ? 'Organic' : ''].filter(Boolean).join(' · '))}</p>
      <button class="close" type="button" aria-label="Close">×</button>
    </div>
    <div class="d-hero">
      <div class="d-text">
        <h2 class="d-name">${esc(w.name)} <span>${esc(w.thin || '')}</span></h2>
        <p class="d-prod">${esc([w.prod, w.vint].filter(Boolean).join(' · '))}</p>
        <p class="d-price">${esc(price(w.price))}${w.vol && w.vol !== 750 ? ` <small>${esc(volume(w.vol))}</small>` : ''}</p>
      </div>
      <img class="d-img" src="${esc(cdn(w.id, 200))}" alt="" width="52" height="184" decoding="async"
        onerror="if (!this.dataset.fb) { this.dataset.fb = 1; this.src = '${esc(thumb(w))}'; } else this.remove()">
    </div>
    <div class="cards">
      <div class="card stock ${stockClass(it)}">
        <div class="k">${esc(here)}</div>
        <div class="v">${esc(stockText(it))}</div>
        <div class="s">${it.order ? 'Order on systembolaget.se, pick up in any store' : it.multi ? 'Per store below' : it.shelf ? `Shelf ${esc(it.shelf)}` : it.stock == null ? 'Not stocked yet' : 'No shelf listed'}</div>
      </div>
      ${ratingCard(w)}
    </div>
    <div class="yours" id="yours" data-w="${w.i}" hidden></div>
    <p class="match">${matchText(w)}</p>
    <dl class="facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
    <div class="links">
      <a href="https://www.systembolaget.se/sok/?textQuery=${encodeURIComponent(w.art)}" target="_blank" rel="noopener">systembolaget.se</a>
      ${w.viv && RATED.has(w.band) ? `<a href="https://www.vivino.com/w/${encodeURIComponent(w.viv)}" target="_blank" rel="noopener">vivino.com</a>` : ''}
    </div>
    ${reportLine(w)}
    <p class="meta">Stock checked ${esc(S.stores.find(s => s.id === S.storeId)?.chk ?? S.meta.stock_checked_at)}.</p>`;
  S.detailItem = it;
  openDialog($('#detail-dlg'));
  $('#detail').scrollTop = 0;
  fillYours(it);
}

// Stock and shelf for this wine in each pinned store (other than the one shown).
async function fillYours(it) {
  const ids = [...S.favs].filter(id => id !== S.storeId && S.stores.some(s => s.id === id));
  const el = $('#yours');
  if (!ids.length || !el) return;
  el.hidden = false;
  el.innerHTML = '<h3>Your stores</h3><p class="meta">Loading…</p>';
  const maps = await Promise.all(ids.map(id => availOf(id).catch(() => null)));
  if (!el.isConnected || el.dataset.w !== String(it.w.i)) return; // another wine opened meanwhile
  el.innerHTML = '<h3>Your stores</h3>' + ids.map((id, k) => {
    const v = maps[k]?.get(it.w.i);
    const one = v ? { w: it.w, stock: v[0], shelf: v[1] } : null;
    const text = !maps[k] ? 'Couldn’t load' : !one ? 'Not carried' : stockText(one) + (one.shelf ? ` · Shelf ${one.shelf}` : '');
    return `<div class="ys"><span>${esc(storeName(id))}</span><span class="v ${one ? stockClass(one) : ''}">${esc(text)}</span></div>`;
  }).join('');
}

// ---------- start

async function start() {
  try {
    Object.assign(S, await loadAll());
  } catch (err) {
    $('#summary').textContent = `No data (${err.message}). Run: python -m pipeline.run build`;
    return;
  }
  for (const s of S.stores) s.hay = norm(`${s.name} ${s.address || ''} ${s.town} ${s.id}`);
  applyReports();
  S.stores.sort((a, b) => a.town.localeCompare(b.town, 'sv') || a.name.localeCompare(b.name, 'sv'));
  const m = S.meta;
  if (status.saved) {
    const note = $('#offline');
    note.hidden = false;
    note.textContent = `Couldn't reach the site (offline or signed out): showing saved data from ${m.stock_checked_at}.`;
  }
  $('#store-meta').textContent = `${nf.format(m.wines)} wines, ${nf.format(m.rated)} with a confirmed Vivino rating. `
    + `Stock checked ${m.stock_checked_at}; ratings checked ${m.ratings_checked?.join(' to ') ?? '–'}.`;
  syncControls();
  const last = store.get('store');
  if (last && (S.stores.some(s => s.id === last) || (last === MINE && S.favs.size) || (last === ORDERS && m.orders))) {
    pickStore(last);
  } else {
    $('#summary').textContent = 'Pick a store to see its wines.';
    $('#store-btn').click();
  }
}

start();

if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {}); // offline copies

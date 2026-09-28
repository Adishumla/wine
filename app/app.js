// Wine by Store: pick a Systembolaget store, filter and sort its wines by Vivino rating.
// A rating shows only for an accepted or hand-set match; anything else is "no rating", never a guess.

import { loadAll, loadStore, norm } from './data.js';
import { VList } from './vlist.js';

const $ = s => document.querySelector(s);
const ROW_H = 76;
const RATED = new Set(['accept', 'override']);
const DEFAULTS = { q: '', sort: 'adj', type: '', price: 0, rating: 0, count: 0, country: '', stock: true, rated: false, organic: false };
const PRICES = [80, 100, 125, 150, 200, 250, 300, 400, 500, 750, 1000];
const RATINGS = [3.5, 3.7, 3.8, 3.9, 4.0, 4.1, 4.2, 4.4];
const COUNTS = [25, 100, 500, 1000, 5000];

const nf = new Intl.NumberFormat('sv-SE');
const nf1 = new Intl.NumberFormat('sv-SE', { maximumFractionDigits: 1 });
const dateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
const today = new Date().toISOString().slice(0, 10);

const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(`wine.${k}`)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(`wine.${k}`, JSON.stringify(v)); } catch { /* private mode: not remembered */ } },
};

const S = { wines: [], stores: [], meta: {}, storeId: null, items: [], orders: {}, f: { ...DEFAULTS, ...store.get('filters'), q: '' } };

// ---------- formatting

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const price = p => (p == null ? '' : `${Number.isInteger(p) ? nf.format(p) : p.toFixed(2).replace('.', ',')} kr`);
const compact = n => (n < 1000 ? String(n) : n < 10000 ? `${(n / 1000).toFixed(1)}k` : `${Math.round(n / 1000)}k`);
const volume = ml => (ml >= 1000 ? `${nf1.format(ml / 1000)} l` : `${nf.format(ml)} ml`);
const date = d => (d ? dateFmt.format(new Date(`${d}T12:00:00`)) : '');
const tier = r => (r == null ? 'r0' : r >= 4.2 ? 'r5' : r >= 4.0 ? 'r4' : r >= 3.8 ? 'r3' : r >= 3.5 ? 'r2' : 'r1');
const storeName = id => S.stores.find(s => s.id === id)?.name ?? id;
// Bottle photos from Systembolaget's image CDN (widths 20-800 px; the only thing fetched at view time).
const bottle = (id, width) => `https://product-cdn.systembolaget.se/productimages/${encodeURIComponent(id)}/${encodeURIComponent(id)}_${width}.webp`;
const shortDate = t => t.slice(5).replace(/^(\d\d)-(\d\d)/, (_, mo, d) => `${+d}/${+mo}`); // "2026-09-27 21:10" -> "27/9 21:10"

// Not launched yet: "arrives", even when bottles are already in the store (they can't be sold before launch day).
const upcoming = it => it.w.launched > today;
const inStock = it => it.stock > 0 && !upcoming(it);
function stockText(it) {
  if (upcoming(it)) return `Arrives ${date(it.w.launched).replace(/ \d{4}$/, '')}`;
  if (it.stock == null) return 'Not in store yet';
  return it.stock > 0 ? `${nf.format(it.stock)} in stock` : 'Sold out';
}
const stockClass = it => (upcoming(it) || it.stock == null ? 'soon' : it.stock > 0 ? 'ok' : 'out');

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
};

function order(sort) {
  if (!S.orders[sort]) {
    const cs = [...(SORTS[sort] || SORTS.adj), byName];
    S.orders[sort] = [...S.items].sort((a, b) => { for (const c of cs) { const d = c(a, b); if (d) return d; } return 0; });
  }
  return S.orders[sort];
}

function apply({ keepScroll = false } = {}) {
  const f = S.f;
  const toks = norm(f.q).split(/\s+/).filter(Boolean);
  const out = [];
  let rated = 0;
  for (const it of order(f.sort)) {
    const w = it.w;
    if (f.stock && !inStock(it)) continue;
    if (f.rated && w.rating == null) continue;
    if (f.organic && !w.organic) continue;
    if (f.type && w.type !== f.type) continue;
    if (f.country && w.country !== f.country) continue;
    if (f.price && !(w.price <= f.price)) continue;
    if (f.rating && !(w.rating >= f.rating)) continue;
    if (f.count && !(w.count >= f.count)) continue;
    if (toks.length && !toks.every(t => w.hay.includes(t))) continue;
    out.push(it);
    if (w.rating != null) rated++;
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
  $('#reset').hidden = Object.keys(DEFAULTS).every(k => k === 'sort' || S.f[k] === DEFAULTS[k]);
}

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
  const src = bottle(w.id, 60);
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

// ---------- store picker

function renderStores() {
  const toks = norm($('#store-q').value).split(/\s+/).filter(Boolean);
  const rows = S.stores.filter(s => toks.every(t => s.hay.includes(t)));
  $('#store-list').innerHTML = rows.length ? rows.map(s => `
    <button type="button" class="store${s.id === S.storeId ? ' current' : ''}" data-id="${esc(s.id)}">
      <span class="sn">${esc(s.name)}</span>
      <span class="sa">${esc([s.name === s.address ? '' : s.address, s.town].filter(Boolean).join(', '))}</span>
      <span class="sw">${nf.format(s.wines)} wines</span>
    </button>`).join('') : '<p class="empty">No store matches.</p>';
}

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
  const b = e.target.closest('.store');
  if (!b) return;
  $('#store-dlg').close();
  pickStore(b.dataset.id);
});

async function pickStore(id) {
  S.storeId = id;
  store.set('store', id);
  $('#store-name').textContent = storeName(id);
  $('#summary').textContent = 'Loading…';
  let rows;
  try {
    rows = await loadStore(id);
  } catch (err) {
    $('#summary').textContent = `Couldn't load this store (${err.message}).`;
    return;
  }
  if (S.storeId !== id) return; // another store was picked meanwhile
  S.items = rows.map(([i, stock, shelf]) => ({ w: S.wines[i], stock, shelf }));
  S.orders = {};
  const st = S.stores.find(s => s.id === id);
  const note = $('#note');
  note.hidden = !st?.stale && !st?.partial;
  note.textContent = st?.stale
    ? `This store's list couldn't be checked in the last update; it's from ${st.chk ?? 'an earlier run'}.`
    : 'This store’s list may be missing a few wines: the last update couldn’t check it.';
  $('#fresh').textContent = st?.chk ? `Stock ${shortDate(st.chk)}` : '';
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
    : { review: 'Match not confirmed', unchecked: 'Match not checked yet', reject: 'Not found on Vivino', override: 'Not on Vivino' }[w.band] ?? 'Not matched yet';
  return `<div class="card r0"><div class="k">Vivino</div><div class="v">No rating</div><div class="s">${why}</div></div>`;
}

function matchText(w) {
  if (w.band === 'accept') return 'Matched to Vivino automatically (hand-checked samples: ≥ 95 % right).';
  if (w.band === 'override') return w.viv ? 'Matched to Vivino by hand.' : 'Checked by hand: not on Vivino.';
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
      <img class="d-img" src="${esc(bottle(w.id, 200))}" alt="" width="52" height="184" decoding="async" onerror="this.remove()">
    </div>
    <div class="cards">
      <div class="card stock ${stockClass(it)}">
        <div class="k">${esc(here)}</div>
        <div class="v">${esc(stockText(it))}</div>
        <div class="s">${it.shelf ? `Shelf ${esc(it.shelf)}` : it.stock == null ? 'Not stocked yet' : 'No shelf listed'}</div>
      </div>
      ${ratingCard(w)}
    </div>
    <p class="match">${matchText(w)}</p>
    <dl class="facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
    <div class="links">
      <a href="https://www.systembolaget.se/sok/?textQuery=${encodeURIComponent(w.art)}" target="_blank" rel="noopener">systembolaget.se</a>
      ${w.viv && RATED.has(w.band) ? `<a href="https://www.vivino.com/w/${encodeURIComponent(w.viv)}" target="_blank" rel="noopener">vivino.com</a>` : ''}
    </div>
    <p class="meta">Stock checked ${esc(S.stores.find(s => s.id === S.storeId)?.chk ?? S.meta.stock_checked_at)}.</p>`;
  openDialog($('#detail-dlg'));
  $('#detail').scrollTop = 0;
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
  S.stores.sort((a, b) => a.town.localeCompare(b.town, 'sv') || a.name.localeCompare(b.name, 'sv'));
  const m = S.meta;
  $('#store-meta').textContent = `${nf.format(m.wines)} wines, ${nf.format(m.rated)} with a confirmed Vivino rating. `
    + `Stock checked ${m.stock_checked_at}; ratings checked ${m.ratings_checked?.join(' to ') ?? '–'}.`;
  syncControls();
  const last = store.get('store');
  if (last && S.stores.some(s => s.id === last)) {
    pickStore(last);
  } else {
    $('#summary').textContent = 'Pick a store to see its wines.';
    $('#store-btn').click();
  }
}

start();

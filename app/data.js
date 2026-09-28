// Loads the build step's files (data/app/, reached through the app/data symlink) and expands the compact rows.

export const SCHEMA = 1;

export const norm = s => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

// True once any data file came from the service worker's saved copy (offline, or signed out of Access).
export const status = { saved: false };

async function getJSON(path) {
  const r = await fetch(path, { cache: 'no-cache' }); // revalidate, so a rebuild shows up on reload
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  if (r.headers.get('x-wine-cache') === 'saved') status.saved = true;
  return r.json();
}

export async function loadAll() {
  const [w, stores, meta] = await Promise.all(['wines', 'stores', 'meta'].map(n => getJSON(`data/${n}.json`)));
  if (w.schema !== SCHEMA || meta.schema !== SCHEMA) throw new Error(`data schema ${w.schema}, app expects ${SCHEMA}`);
  const L = w.lookups;
  const at = (k, i) => (i == null ? null : L[k][i]);
  // The build leaves out empty fields (build.py ROW_DEFAULTS): missing means null, no grapes, band "", 0 stores.
  const wines = w.wines.map((r, i) => {
    const x = {
      i, id: r.id, art: r.art, name: r.name, thin: r.thin ?? null, prod: r.prod ?? null, vint: r.vint ?? null,
      price: r.price, vol: r.vol, abv: r.abv ?? null, sugar: r.sugar ?? null, country: at('country', r.c),
      region: at('region', r.r), type: at('type', r.ty), style: at('style', r.st),
      grapes: (r.g ?? []).map(g => L.grape[g]), assortment: at('assortment', r.as), packaging: at('packaging', r.pk),
      organic: !!r.org, launched: r.new ?? null, viv: r.viv ?? null, band: r.band ?? '', rating: r.rat ?? null,
      count: r.cnt ?? null, adj: r.adj ?? null, value: r.val ?? null, checked: r.rchk ?? null, stores: r.ns ?? 0,
      img: !!r.img,
    };
    x.title = x.thin ? `${x.name} ${x.thin}` : x.name;
    x.hay = norm([x.title, x.prod, x.vint, x.country, x.region, x.style, x.type, x.art, ...x.grapes].join(' '));
    return x;
  });
  // Name order once, as a number, so every sort can break ties cheaply.
  const coll = new Intl.Collator('sv');
  [...wines].sort((a, b) => coll.compare(a.title, b.title)).forEach((x, k) => { x.rank = k; });
  return { wines, stores, meta };
}

// [[wine index, stock, shelf]]; stock null = in the assortment but not stocked yet.
export const loadStore = id => getJSON(`data/avail/${id}.json`);

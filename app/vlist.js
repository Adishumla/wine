// A windowed list on the page's own scroll: fixed-height rows, only the visible ones (plus a margin) in the DOM.
// Row nodes are recycled by index modulo the pool size, so scrolling refills only the rows that come into view.

const OVERSCAN = 6;

export class VList {
  constructor(el, rowHeight, make, fill) {
    Object.assign(this, { el, h: rowHeight, make, fill, items: [], pool: [], gen: 0 });
    const draw = () => this.draw(); // scroll events already come at most once per frame
    addEventListener('scroll', draw, { passive: true });
    addEventListener('resize', draw);
  }

  setItems(items) {
    this.items = items;
    this.el.style.height = `${items.length * this.h}px`;
    for (const n of this.pool) n._i = -1;
    this.draw();
  }

  itemAt(node) {
    return node && node._i >= 0 ? this.items[node._i] : null;
  }

  draw() {
    const size = Math.ceil(innerHeight / this.h) + 2 * OVERSCAN;
    if (this.pool.length < size) {
      while (this.pool.length < size) {
        const n = this.make();
        this.el.append(n);
        this.pool.push(n);
      }
      for (const n of this.pool) n._i = -1; // the modulo mapping changed
    }
    const P = this.pool.length;
    const top = this.el.getBoundingClientRect().top;
    const first = Math.max(0, Math.floor(-top / this.h) - OVERSCAN);
    const last = Math.min(this.items.length, first + P);
    const gen = ++this.gen;
    for (let i = first; i < last; i++) {
      const n = this.pool[i % P];
      if (n._i !== i) {
        n._i = i;
        this.fill(n, this.items[i]);
        n.style.transform = `translateY(${i * this.h}px)`;
      }
      n._gen = gen;
      n.hidden = false;
    }
    for (const n of this.pool) {
      if (n._gen !== gen) { n.hidden = true; n._i = -1; }
    }
  }
}

/* Shared scroll-pinned, capped line stream log buffer manager, and the
   cut over it: where in the run the reader is looking. */

import { el } from "../format.mjs";

/* Reading the scroll box is layout, and this file is the only caller,
   so it lives with the buffer rather than among the shared helpers.
   `toBottom` answers where the scroller actually landed. */
const atBottom = (node, slack) =>
  node.scrollHeight - node.scrollTop - node.clientHeight <= slack;

const toBottom = (node) => {
  node.scrollTop = node.scrollHeight;
  return node.scrollTop;
};

/* A batch's layout visits every box in the scroller and the cap allows
   thousands, so rows are held in chunks: one out of view is skipped
   whole and declares its height. A hundred is several screens — few
   enough chunks to be free, most of a full log skipped. */
const CHUNK_ROWS = 100;

/* The container's element children are this file's chunks, except
   whatever the caller put there itself — an empty-log notice. */
const isChunk = (node) => node?.classList?.contains("chunk") === true;

/* A row's moment on the run's clock. Rows without one — a session
   divider, this session's own remarks — sit on no such clock, and a
   cursor moved into the past never hides them. */
export const stampOf = (node) =>
  node.dataset.ts === undefined ? null : Number(node.dataset.ts);

export class StreamLog {
  constructor({ container, lineCap = 2000, slack = 12 }) {
    this.container = container;
    this.lineCap = lineCap;
    this.slack = slack;
    /* Following means the reader has not scrolled up from the bottom. The
       log's own pin scrolls too, and its event can arrive after more rows
       landed and the bottom moved on — so a scroll found where the pin
       left the position is not the reader's, and changes nothing. */
    this.stick = true;
    this.pinnedTop = 0;
    this.dirty = false;
    this.pinning = false; /* a pin already waiting for the frame */
    this.recheck = false; /* pin once more, on rendered rather than estimated heights */
    /* Where in the run the reader is looking, and the first row past
       it. `null` edge is "none is"; `undefined` is "not known", which
       one full pass answers. */
    this.cut = null;
    this.edge = undefined;
    /* How many rows this stream put in the container. Counted rather
       than asked for: `childElementCount` walks the children, and the
       append that precedes the question has just invalidated whatever
       the engine had cached — which made capping the log cost more than
       drawing it. */
    this.held = 0;
    /* The chunks whose shown row count may have moved since they last
       declared it. */
    this.touched = new Set();

    this.container.addEventListener("scroll", () => {
      if (this.container.scrollTop === this.pinnedTop) return;
      this.stick = atBottom(this.container, this.slack);
    });
  }

  *#all() {
    for (const chunk of [...this.container.children]) {
      if (isChunk(chunk)) yield chunk;
    }
  }

  /* The rows this stream holds, in order, whatever they are grouped in. */
  *rows() {
    for (const chunk of this.#all()) yield* [...chunk.children];
  }

  /* Append a row, stamped with the moment it describes, and say whether
     that moment is past the cut — a row printed after where the reader
     is looking arrives hidden rather than waiting for the next cursor
     move to notice it. */
  append(node, ts) {
    if (ts !== undefined) node.dataset.ts = String(ts);
    const past = ts !== undefined && ts > (this.cut ?? Infinity);
    const open = this.#open();
    open.append(node);
    this.held += 1;
    this.touched.add(open);
    if (past && this.edge === null) this.edge = node;
    while (this.held > this.lineCap) this.#drop();
    this.dirty = true;
    return past;
  }

  /* The chunk rows go into: the last one while it has room. Asked for
     rather than kept, so a trim that empties it leaves nothing stale.
     A chunk taking a row is no longer one showing nothing, and the next
     count says again whether it is. */
  #open() {
    const last = this.container.lastElementChild;
    if (isChunk(last) && last.childElementCount < CHUNK_ROWS) {
      last.hidden = false;
      return last;
    }
    const fresh = el("div", "chunk");
    this.container.append(fresh);
    return fresh;
  }

  /* The oldest row goes, and the chunk it empties goes with it. */
  #drop() {
    const chunk = this.#chunk(this.container.firstElementChild, true);
    if (!chunk) return;
    chunk.removeChild(chunk.firstElementChild);
    this.held -= 1;
    this.touched.add(chunk);
    if (chunk.childElementCount) return;
    this.container.removeChild(chunk);
    this.touched.delete(chunk);
  }

  /* From `node`, the first chunk in that direction, `node` included. */
  #chunk(node, forward) {
    const step = forward ? "nextElementSibling" : "previousElementSibling";
    for (let found = node; found; found = found[step]) {
      if (isChunk(found)) return found;
    }
    return null;
  }

  /* A row's neighbour across the chunk boundary, so a walk over rows
     never has to know the rows are grouped. */
  #step(row, forward) {
    const step = forward ? "nextElementSibling" : "previousElementSibling";
    const edge = forward ? "firstElementChild" : "lastElementChild";
    if (row[step]) return row[step];
    for (let chunk = this.#chunk(row.parentNode?.[step], forward); chunk; ) {
      if (chunk[edge]) return chunk[edge];
      chunk = this.#chunk(chunk[step], forward);
    }
    return null;
  }

  /* What a chunk out of view declares instead of being laid out: the
     rows in it that are shown. One showing none is itself nothing to
     show, or its estimate would stand as empty space under a cursor
     moved into the past. */
  #recount() {
    for (const chunk of this.touched) {
      if (chunk.parentNode !== this.container) continue;
      let shown = 0;
      for (const row of chunk.children) if (!row.hidden) shown += 1;
      chunk.style.setProperty("--rows", String(shown));
      chunk.hidden = shown === 0;
    }
    this.touched.clear();
  }

  /* The caller hid or showed rows by a rule of its own rather than by
     the cut, so every chunk has to say again how much of it is left. */
  restyled() {
    for (const chunk of this.#all()) this.touched.add(chunk);
    this.#recount();
    this.dirty = true;
  }

  /* Move the cut to `ts` and settle only the rows that changed side.

     Rows arrive in time order, so the ones past the cut are a suffix
     and the cut is a boundary rather than a test to run on every row.
     The walk starts where the boundary was and stops once it has
     crossed; `settle(row, past)` applies the caller's own rule, which
     may turn on more than the cut. */
  cutTo(ts, settle) {
    this.cut = ts;
    /* No cut is a bound nothing is past, so it needs no case of its
       own: the walk forward reaches the end and leaves no boundary. */
    const bound = ts ?? Infinity;
    if (this.edge && !this.edge.isConnected) this.edge = undefined;
    if (this.edge === undefined) {
      let first = null;
      for (const row of this.rows()) {
        const at = stampOf(row);
        if (at === null) continue;
        const past = at > bound;
        this.#settled(row, past, settle);
        if (past && !first) first = row;
      }
      this.edge = first;
      this.#recount();
      return;
    }
    /* Forward: rows the cut has moved past. */
    let row = this.edge;
    while (row) {
      const at = stampOf(row);
      if (at !== null && at > bound) break;
      if (at !== null) this.#settled(row, false, settle);
      row = this.#step(row, true);
    }
    if (row !== this.edge) {
      this.edge = row;
      this.#recount();
      return;
    }
    /* Backward: rows the cut has fallen behind. From the end when
       nothing was past it, which is where such rows would be. */
    let first = this.edge;
    for (
      let back = this.edge
        ? this.#step(this.edge, false)
        : this.#chunk(this.container.lastElementChild, false)?.lastElementChild;
      back;
      back = this.#step(back, false)
    ) {
      const at = stampOf(back);
      if (at === null) continue;
      if (at <= bound) break;
      this.#settled(back, true, settle);
      first = back;
    }
    this.edge = first;
    this.#recount();
  }

  /* A row the caller's rule has just been applied to: its chunk shows a
     different number of rows than it declared. */
  #settled(row, past, settle) {
    settle(row, past);
    this.touched.add(row.parentNode);
  }

  /* Back to the bottom, where a stream the reader has not scrolled away
     from belongs — after a batch, and when a hidden pane is shown. In the
     frame: reading the scroll height here would force the batch's layout
     now; the frame does it once, and one pin answers every ask before it. */
  pin({ recheck = false } = {}) {
    this.recheck ||= recheck;
    if (this.pinning) return;
    this.pinning = true;
    requestAnimationFrame(() => {
      this.pinning = false;
      const again = this.recheck;
      this.recheck = false;
      if (!this.stick) return;
      this.pinnedTop = toBottom(this.container);
      /* A pane shown for the first time was pinned on estimated row
         heights; the rows now in view take their rendered ones. */
      if (again) this.pin();
    });
  }

  settle() {
    /* What each chunk declares is owed whether or not this pane is on
       screen; only the scroll is not, and a hidden one has no geometry
       to read anyway — showing it pins it. */
    this.#recount();
    if (!this.dirty) return;
    this.dirty = false;
    if (!this.container.hidden) this.pin();
  }

  clear() {
    while (this.container.firstChild) {
      this.container.removeChild(this.container.firstChild);
    }
    this.stick = true;
    this.pinnedTop = 0;
    this.dirty = false;
    this.cut = null;
    this.edge = undefined;
    this.held = 0;
    this.touched.clear();
  }
}

/* Shared scroll-pinned, capped line stream log buffer manager, and the
   cut over it: where in the run the reader is looking. */

/* Reading the scroll box is layout, and this file is the only caller,
   so it lives with the buffer rather than among the shared helpers.
   `toBottom` answers where the scroller actually landed. */
const atBottom = (node, slack) =>
  node.scrollHeight - node.scrollTop - node.clientHeight <= slack;

const toBottom = (node) => {
  node.scrollTop = node.scrollHeight;
  return node.scrollTop;
};

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

    this.container.addEventListener("scroll", () => {
      if (this.container.scrollTop === this.pinnedTop) return;
      this.stick = atBottom(this.container, this.slack);
    });
  }

  /* Append a row, stamped with the moment it describes, and say whether
     that moment is past the cut — a row printed after where the reader
     is looking arrives hidden rather than waiting for the next cursor
     move to notice it. */
  append(node, ts) {
    if (ts !== undefined) node.dataset.ts = String(ts);
    const past = ts !== undefined && ts > (this.cut ?? Infinity);
    this.container.append(node);
    this.held += 1;
    if (past && this.edge === null) this.edge = node;
    while (this.held > this.lineCap) {
      this.container.removeChild(this.container.firstElementChild);
      this.held -= 1;
    }
    this.dirty = true;
    return past;
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
      for (const row of this.container.children) {
        const at = stampOf(row);
        if (at === null) continue;
        const past = at > bound;
        settle(row, past);
        if (past && !first) first = row;
      }
      this.edge = first;
      return;
    }
    /* Forward: rows the cut has moved past. */
    let row = this.edge;
    while (row) {
      const at = stampOf(row);
      if (at !== null && at > bound) break;
      if (at !== null) settle(row, false);
      row = row.nextElementSibling;
    }
    if (row !== this.edge) {
      this.edge = row;
      return;
    }
    /* Backward: rows the cut has fallen behind. From the end when
       nothing was past it, which is where such rows would be. */
    let first = this.edge;
    for (
      let back = this.edge ? this.edge.previousElementSibling : this.container.lastElementChild;
      back;
      back = back.previousElementSibling
    ) {
      const at = stampOf(back);
      if (at === null) continue;
      if (at <= bound) break;
      settle(back, true);
      first = back;
    }
    this.edge = first;
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
    if (!this.dirty) return;
    this.dirty = false;
    this.pin();
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
  }
}

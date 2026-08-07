/* The time axis: what the firmware recorded, in the order it happened.
 *
 * The board answers "what is the machine doing"; this answers "in what
 * order did it do it". That is the ring's one unique product, and until
 * now it reached the browser only as counts — `vgic.bind -> +111us
 * inject -> +325us eoi` existed in the CLI and nowhere a reader could
 * see it.
 *
 * Drawn on a canvas because the board's per-batch layout budget is
 * zero rects, and a few thousand marks as DOM nodes would spend it many
 * times over. A canvas costs one size query and forces no layout.
 *
 * The canvas is an output, never the storage. Live following moves the
 * x mapping every frame and a resize rescales it, so a strip that kept
 * its history in already-painted pixels would lose it the first time
 * the window changed and — since following only ever asks for the tail
 * — would never get it back. Records are held here and every frame is
 * drawn from them.
 */

/* Records held for redrawing, at 32 bytes each across the columns:
   about two megabytes, and at the measured rate roughly half a minute
   of a busy run. Beyond it the oldest go, the same way they go
   everywhere else in this layer. */
const HOLD = 1 << 16;
/* How often following asks for what has arrived since last time. */
const FOLLOW_MS = 250;
/* Resolution asked for on a tail request. Large on purpose: a response
   carries records *or* density, never both, so a generous number costs
   nothing and simply means "enumerate these if you can". */
const TAIL_BUCKETS = 8192;
/* Lane geometry. A lane thinner than this is a smudge; thicker than
   that and four lanes fill the strip. */
const LANE_MIN = 9;
const LANE_MAX = 20;
const GUTTER = 96; /* lane captions */
const MARK_W = 2;

const CPU_COLOURS = ["--vm0", "--vm1", "--vm2", "--vm3"];

export function createTimeline({ strip, canvas, foldButton, request }) {
  const context = canvas.getContext("2d");
  /* Parallel columns rather than objects: one array of 65536 records
     as objects is several megabytes of headers, and every field here
     is a number. */
  const held = {
    ts: new Float64Array(HOLD),
    code: new Uint16Array(HOLD),
    cpu: new Uint8Array(HOLD),
    a: new Float64Array(HOLD),
    b: new Float64Array(HOLD),
    c: new Float64Array(HOLD),
  };
  let head = 0; /* total ever appended; the live window is the last HOLD */

  let byCode = new Map(); /* firmware code -> catalogue entry */
  let order = []; /* catalogue order, for stable lane placement */
  const lanes = []; /* event ids seen this run, in catalogue order */
  let freq = 0; /* CNTFRQ, for the microsecond axis */
  let span = null; /* what the bridge still holds */
  let dense = null; /* a window too busy to enumerate: {from,to,hist} */
  let follow = true;
  let view = null; /* {from,to} being drawn; null means "the tail" */
  let painting = false;
  let pending = 0; /* ts already asked for, so a slow answer is not re-asked */
  let timer = null;

  const held_count = () => Math.min(head, HOLD);
  const slot = (index) => (head - held_count() + index) % HOLD;
  const newest = () => (held_count() ? held.ts[slot(held_count() - 1)] : 0);

  /* ---------------- data in ---------------- */

  function setCatalogue(stops) {
    byCode = new Map();
    order = [];
    for (const stop of stops || []) {
      if (!stop.code) continue;
      byCode.set(stop.code, stop);
      order.push(stop.id);
    }
  }

  function reset() {
    head = 0;
    seen.clear();
    lanes.length = 0;
    span = null;
    dense = null;
    view = null;
    pending = 0;
    follow = true;
    draw();
  }

  const seen = new Set(); /* event ids observed this run */

  /* A lane appears the first time its event does and then stays, in
     catalogue order. Every catalogued lane up front would start the
     strip mostly empty; lanes that came and went would move under the
     reader mid-read. */
  function noteLane(id) {
    if (!id || lanes.includes(id)) return;
    lanes.length = 0;
    for (const candidate of order) {
      if (seen.has(candidate)) lanes.push(candidate);
    }
  }

  function append(cols, from) {
    const count = cols.ts.length;
    for (let index = 0; index < count; index += 1) {
      const at = head % HOLD;
      held.ts[at] = from + cols.ts[index];
      held.code[at] = cols.code[index];
      held.cpu[at] = cols.cpu[index];
      held.a[at] = cols.a[index];
      held.b[at] = cols.b[index];
      held.c[at] = cols.c[index];
      head += 1;
      const entry = byCode.get(cols.code[index]);
      if (entry) {
        seen.add(entry.id);
        noteLane(entry.id);
      }
    }
  }

  /* The bridge's per-drain summary. Only the span matters here: it says
     what still exists to be asked about, and where the newest edge is. */
  function note(data) {
    if (!data || !data.span) return;
    const next = data.span;
    if (span && next.to < span.to) reset(); /* a new machine, a new epoch */
    span = next;
    if (freq === 0 && data.freq_hz) freq = data.freq_hz;
    schedule();
  }

  /* A window answer. Either the records or the density that stands in
     for them — the bridge sends one, never both. */
  function apply(data) {
    if (!data || !data.window) return;
    if (data.span) span = data.span;
    if (data.window.freq_hz) freq = data.window.freq_hz;
    pending = 0;
    if (data.cols) {
      dense = null;
      append(data.cols, data.window.from);
    } else if (data.hist) {
      /* Too many records in the window to enumerate at the resolution
         asked for. Drawn as density and said so, rather than shown as a
         handful of marks that would read as a quiet stretch. */
      dense = { from: data.window.from, to: data.window.to, hist: data.hist };
      for (const id of Object.keys(data.hist)) {
        seen.add(id);
        noteLane(id);
      }
    }
    draw();
  }

  /* ---------------- following ---------------- */

  function schedule() {
    if (timer !== null || !follow) return;
    timer = setTimeout(() => {
      timer = null;
      tick();
    }, FOLLOW_MS);
  }

  function tick() {
    if (!follow || !span || !span.n) return;
    const from = held_count() ? newest() + 1 : span.from;
    if (span.to < from || pending === span.to) {
      schedule();
      return;
    }
    /* Only what has arrived since last time. Re-asking for the whole
       visible window at this rate would move more bytes than streaming
       every record, which is the thing the summary exists to avoid. */
    pending = span.to;
    request({ op: "window", from, to: span.to, buckets: TAIL_BUCKETS });
    schedule();
  }

  function setFollow(on) {
    follow = on;
    if (follow) {
      view = null;
      schedule();
    }
    strip.dataset.follow = follow ? "on" : "off";
    draw();
  }

  /* ---------------- drawing ---------------- */

  function bounds() {
    if (view) return view;
    const count = held_count();
    if (dense) return { from: dense.from, to: dense.to };
    if (!count) return null;
    const last = newest();
    const first = held.ts[slot(0)];
    return { from: first, to: Math.max(last, first + 1) };
  }

  function measure() {
    const ratio = window.devicePixelRatio || 1;
    const box = canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(box.width * ratio));
    const height = Math.max(1, Math.round(box.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return { width, height, ratio };
  }

  function palette() {
    const style = getComputedStyle(canvas);
    return {
      ink: style.getPropertyValue("--ink3").trim() || "#888",
      line: style.getPropertyValue("--line").trim() || "#333",
      cpu: CPU_COLOURS.map((name) => style.getPropertyValue(name).trim() || "#888"),
      font: style.getPropertyValue("--mono").trim() || "monospace",
    };
  }

  /* Always from the held records, never from what is already painted:
     the x mapping moves every frame while following, and a resize
     rescales it. */
  function draw() {
    if (painting) return;
    painting = true;
    requestAnimationFrame(() => {
      painting = false;
      paint();
    });
  }

  /* Records binned into the columns the strip actually has.
     One path for every zoom, because the alternative is a mode switch
     that draws two hundred events per pixel as two hundred overlapping
     marks — a solid block, which reads as "continuously busy" whether
     it was two events or two thousand. Sparse windows fall out of the
     same arithmetic as single ticks. */
  function bin(window_, columns) {
    const width = Math.max(1, window_.to - window_.from);
    const counts = lanes.map(() => new Uint32Array(columns));
    const owner = lanes.map(() => new Uint8Array(columns));
    if (dense && !heldCovers(window_)) {
      for (const [id, column] of Object.entries(dense.hist)) {
        const lane = lanes.indexOf(id);
        if (lane < 0) continue;
        const step = (dense.to - dense.from) / Math.max(1, column.length);
        for (let index = 0; index < column.length; index += 1) {
          if (!column[index]) continue;
          const ts = dense.from + index * step;
          const at = Math.floor(((ts - window_.from) / width) * columns);
          if (at >= 0 && at < columns) counts[lane][at] += column[index];
        }
      }
      return { counts, owner };
    }
    const total = held_count();
    for (let index = 0; index < total; index += 1) {
      const at = slot(index);
      const ts = held.ts[at];
      if (ts < window_.from || ts > window_.to) continue;
      const entry = byCode.get(held.code[at]);
      if (!entry) continue;
      const lane = lanes.indexOf(entry.id);
      if (lane < 0) continue;
      const column = Math.min(columns - 1, Math.floor(((ts - window_.from) / width) * columns));
      counts[lane][column] += 1;
      owner[lane][column] = held.cpu[at];
    }
    return { counts, owner };
  }

  const heldCovers = (window_) =>
    held_count() > 0 && held.ts[slot(0)] <= window_.from && newest() >= window_.to;

  function paint() {
    const { width, height, ratio } = measure();
    const colours = palette();
    context.clearRect(0, 0, width, height);
    const window_ = bounds();
    if (!window_ || !lanes.length) return;

    const plot = width - GUTTER * ratio;
    const laneHeight = Math.min(
      LANE_MAX * ratio,
      Math.max(LANE_MIN * ratio, height / lanes.length),
    );

    context.font = `${9.5 * ratio}px ${colours.font}`;
    context.textBaseline = "middle";
    for (let lane = 0; lane < lanes.length; lane += 1) {
      const middle = lane * laneHeight + laneHeight / 2;
      context.fillStyle = colours.line;
      context.fillRect(GUTTER * ratio, Math.round(middle), plot, 1);
      context.fillStyle = colours.ink;
      context.fillText(lanes[lane], 4 * ratio, middle);
    }

    const bar = MARK_W * ratio;
    const columns = Math.max(1, Math.floor(plot / bar));
    const { counts, owner } = bin(window_, columns);
    const inner = laneHeight - 4 * ratio;
    for (let lane = 0; lane < lanes.length; lane += 1) {
      const column = counts[lane];
      let peak = 0;
      for (const value of column) if (value > peak) peak = value;
      if (!peak) continue;
      for (let index = 0; index < columns; index += 1) {
        const value = column[index];
        if (!value) continue;
        /* A lane whose columns all hold one event is a row of plain
           ticks; where they differ, height and weight say by how much,
           against that lane's own busiest column. A shared scale would
           flatten every lane but the loudest. */
        const share = peak > 1 ? 0.55 + 0.45 * (value / peak) : 1;
        const tall = inner * share;
        context.globalAlpha = peak > 1 ? 0.45 + 0.55 * (value / peak) : 1;
        context.fillStyle = colours.cpu[owner[lane][index] % colours.cpu.length];
        context.fillRect(
          GUTTER * ratio + index * bar,
          lane * laneHeight + laneHeight - 2 * ratio - tall,
          Math.max(1, bar - ratio),
          tall,
        );
      }
    }
    context.globalAlpha = 1;
  }

  /* ---------------- chrome ---------------- */

  function setFolded(folded) {
    strip.classList.toggle("folded", folded);
    foldButton.setAttribute("aria-expanded", String(!folded));
    foldButton.textContent = folded ? "펼치기" : "접기";
    if (!folded) draw();
  }

  foldButton.addEventListener("click", () => {
    setFolded(!strip.classList.contains("folded"));
  });

  new ResizeObserver(() => draw()).observe(canvas);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => draw());

  return { setCatalogue, note, apply, reset, setFollow, draw, freqHz: () => freq };
}

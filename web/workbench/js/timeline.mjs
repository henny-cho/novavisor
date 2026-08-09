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

/* Records held for redrawing, at 32 bytes each across the columns —
   about two megabytes. Beyond it the oldest go, as everywhere else in
   this layer. */
const HOLD = 1 << 16;
/* How often following asks for what has arrived since last time. */
const FOLLOW_MS = 250;
/* Resolution asked for when the point is to get the records rather than
   a picture of them: a response carries records *or* density, never
   both, so asking high costs nothing and just means "enumerate these if
   you can". The ceiling is the bridge's and travels with the topology —
   a copy of the number here is a copy to drift from. */
const FALLBACK_BUCKETS = 4096;
/* How much of the recent past a following strip shows. Following means
   following the end, so the window is a duration rather than "whatever
   has accumulated" — which would start at nothing and grow without
   bound. */
const LIVE_SECONDS = 5;
/* Lane geometry. A lane thinner than this is a smudge; thicker than
   that and four lanes fill the strip. */
const LANE_MIN = 9;
const LANE_MAX = 20;
const GUTTER = 96; /* lane captions */
const MARK_W = 2;
/* How far off a mark a click may land and still mean it. */
const SLACK_PX = 6;
/* Playback pacing. The order is exact and the real gap is always
   printed; what is compressed is idle time, because a run replayed at
   its true rate is either a blur or a wait, and one replayed at even
   spacing lies about the timing. Between these two bounds the delay
   tracks the real gap, so a burst still reads as a burst. */
const STEP_MIN_MS = 90;
const STEP_MAX_MS = 900;

const CPU_COLOURS = ["--vm0", "--vm1", "--vm2", "--vm3"];

export function createTimeline({ strip, canvas, foldButton, followButton, request, onSelect }) {
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

  let byCode = new Map(); /* record code -> catalogue entry */
  let byId = new Map(); /* event id -> catalogue entry */
  let order = []; /* catalogue order, for stable lane placement */
  const lanes = []; /* event ids seen this run, in catalogue order */
  let freq = 0; /* CNTFRQ, for the microsecond axis */
  let ceiling = FALLBACK_BUCKETS; /* what the bridge will answer in */
  let span = null; /* what the bridge still holds */
  /* A chosen window is kept apart from the follow tail. The tail is
     append-only in arrival order, which is time order because the drain
     merges the rings by CNTPCT; dropping an older window into it would
     make the newest record an old one and send following back to
     re-fetch everything since. */
  let chosen = null; /* {from,to,cols} for an explicit window */
  let dense = null; /* that window too busy to enumerate: {from,to,hist} */
  let follow = true;
  let view = null; /* {from,to} being drawn; null means "the tail" */
  let painting = false;
  let pending = 0; /* ts already asked for, so a slow answer is not re-asked */
  let timer = null;

  const cols_length = (cols) => (cols && cols.ts ? cols.ts.length : 0);
  const held_count = () => Math.min(head, HOLD);
  const slot = (index) => (head - held_count() + index) % HOLD;
  const newest = () => (held_count() ? held.ts[slot(held_count() - 1)] : 0);

  /* ---------------- data in ---------------- */

  function setLimits(limits) {
    if (limits && limits.buckets) ceiling = limits.buckets;
  }

  function setCatalogue(stops) {
    byCode = new Map();
    byId = new Map();
    order = [];
    for (const stop of stops || []) {
      if (!stop.code) continue;
      byCode.set(stop.code, stop);
      byId.set(stop.id, stop);
      order.push(stop.id);
    }
  }

  function reset() {
    head = 0;
    seen.clear();
    lanes.length = 0;
    span = null;
    chosen = null;
    dense = null;
    view = null;
    pending = 0;
    follow = true;
    /* A selection is an index into a set of records that no longer
       exists. Carrying it across would point the cursor at whatever
       happens to land in that slot on the new machine. */
    dropSelection();
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
    /* The clock those timestamps are in travels with them: a range of
       counter values is not a duration without it, and the live window
       is stated in seconds. */
    if (next.freq_hz) freq = next.freq_hz;
    schedule();
  }

  /* A window answer. Either the records or the density that stands in
     for them — the bridge sends one, never both. */
  function apply(data) {
    if (!data || !data.window) return;
    if (data.span) span = data.span;
    if (data.window.freq_hz) freq = data.window.freq_hz;
    pending = 0;
    const { from, to } = data.window;
    const forView = view && view.from === from && view.to === to;
    if (!forView && view) return; /* a tail answer that a drag overtook */
    if (data.cols) {
      if (forView) {
        /* A different set of records answers for this window now, and
           an index into the old one means nothing against it. */
        dropSelection();
        chosen = { from, to, cols: data.cols };
        dense = null;
        /* The tour's answer arrived: start at its first passage and
           walk. Through the same cursor as everything else. */
        if (touring) {
          touring = null;
          if (cols_length(data.cols)) {
            requestAnimationFrame(() => {
              select(0);
              play();
            });
          }
        }
      } else {
        append(data.cols, from);
      }
      noteLanes(data.cols.code);
    } else if (data.hist) {
      /* Even filtered to one path the stretch can be too busy to
         enumerate. Said rather than left as a tour that quietly never
         starts. */
      touring = null;
      /* Too many records in the window to enumerate at the resolution
         asked for. Drawn as density, rather than as a sample of its
         marks that would read as a quiet stretch. */
      dense = { from, to, hist: data.hist };
      chosen = null;
      for (const id of Object.keys(data.hist)) {
        seen.add(id);
        noteLane(id);
      }
    }
    draw();
  }

  function noteLanes(codes) {
    for (const code of codes) {
      const entry = byCode.get(code);
      if (!entry) continue;
      seen.add(entry.id);
      noteLane(entry.id);
    }
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
    /* From the present when there is nothing held yet. Asking from the
       start of the history instead would ask for the whole run, come
       back as density every time because a run does not fit in a
       screenful of columns, and never leave the client holding a single
       record to draw a mark from. */
    const from = held_count() ? newest() + 1 : span.to;
    if (span.to < from || pending === span.to) {
      schedule();
      return;
    }
    /* Only what has arrived since last time. Re-asking for the whole
       visible window at this rate would move more bytes than streaming
       every record, which is the thing the summary exists to avoid. */
    pending = span.to;
    request({ op: "window", from, to: span.to, buckets: ceiling });
    schedule();
  }

  function setFollow(on) {
    follow = on;
    if (follow) {
      view = null;
      chosen = null;
      dense = null;
      schedule();
    }
    strip.dataset.follow = follow ? "on" : "off";
    if (followButton) followButton.setAttribute("aria-pressed", String(follow));
    draw();
  }

  /* ---------------- drawing ---------------- */

  /* Following shows the last few seconds; a chosen window shows itself.
     Density stands in only while there are no records for the window —
     the first thing a fresh strip has, and what a zoomed-out one keeps. */
  function bounds() {
    if (view) return view;
    const count = held_count();
    if (!count) return dense ? { from: dense.from, to: dense.to } : null;
    const last = newest();
    const first = held.ts[slot(0)];
    const width = freq ? LIVE_SECONDS * freq : last - first;
    return { from: Math.max(first, last - width), to: Math.max(last, first + 1) };
  }

  /* Where everything is, in one place and one unit.
     The paint works in device pixels and a pointer arrives in CSS
     pixels, so the same three numbers — gutter, plot width, lane height
     — were being derived twice with a factor between them. Two
     derivations of one geometry drift the moment either is edited, and
     the symptom is a click that selects something other than what the
     reader aimed at. `scale` is the only place the two unit systems
     meet: `1` answers in CSS pixels, the device ratio in device ones. */
  function geometry(scale) {
    const box = canvas.getBoundingClientRect();
    const width = Math.max(1, box.width * scale);
    const height = Math.max(1, box.height * scale);
    const gutter = GUTTER * scale;
    const rows = Math.max(1, lanes.length);
    return {
      width,
      height,
      gutter,
      plot: Math.max(1, width - gutter),
      lane: Math.min(LANE_MAX * scale, Math.max(LANE_MIN * scale, height / rows)),
      scale,
    };
  }

  function measure() {
    const ratio = window.devicePixelRatio || 1;
    const view_ = geometry(ratio);
    const width = Math.round(view_.width);
    const height = Math.round(view_.height);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return view_;
  }

  function palette() {
    const style = getComputedStyle(canvas);
    return {
      ink: style.getPropertyValue("--ink3").trim() || "#888",
      line: style.getPropertyValue("--line").trim() || "#333",
      warn: style.getPropertyValue("--warn").trim() || "#a8770a",
      cpu: CPU_COLOURS.map((name) => style.getPropertyValue(name).trim() || "#888"),
      font: style.getPropertyValue("--mono").trim() || "monospace",
    };
  }

  /* Diagonal hatching, built once per colour. A gap is the one thing on
     this strip that is not an observation, and a solid band would read
     as one — the stripes say "nothing was here to draw". */
  let hatchFor = null;
  function hatch(colour, ratio) {
    if (hatchFor && hatchFor.colour === colour && hatchFor.ratio === ratio) return hatchFor.pattern;
    const step = Math.max(4, Math.round(5 * ratio));
    const tile = document.createElement("canvas");
    tile.width = tile.height = step;
    const pen = tile.getContext("2d");
    pen.strokeStyle = colour;
    pen.globalAlpha = 0.55;
    pen.lineWidth = Math.max(1, ratio);
    pen.beginPath();
    pen.moveTo(-step, step);
    pen.lineTo(step, -step);
    pen.moveTo(0, step * 2);
    pen.lineTo(step * 2, 0);
    pen.stroke();
    hatchFor = { colour, ratio, pattern: context.createPattern(tile, "repeat") };
    return hatchFor.pattern;
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
    const place = (id, ts, cpu) => {
      const lane = lanes.indexOf(id);
      if (lane < 0 || ts < window_.from || ts > window_.to) return;
      const column = Math.min(columns - 1, Math.floor(((ts - window_.from) / width) * columns));
      counts[lane][column] += 1;
      owner[lane][column] = cpu;
    };
    if (chosen) {
      const cols = chosen.cols;
      for (let index = 0; index < cols.ts.length; index += 1) {
        const entry = byCode.get(cols.code[index]);
        if (entry) place(entry.id, chosen.from + cols.ts[index], cols.cpu[index]);
      }
      return { counts, owner };
    }
    if (dense) {
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
      const entry = byCode.get(held.code[at]);
      if (entry) place(entry.id, held.ts[at], held.cpu[at]);
    }
    return { counts, owner };
  }

  function paint() {
    const at = measure();
    const { gutter, plot, lane: laneHeight, scale: ratio } = at;
    const colours = palette();
    context.clearRect(0, 0, at.width, at.height);
    const window_ = bounds();
    if (!window_ || !lanes.length) return;

    context.font = `${9.5 * ratio}px ${colours.font}`;
    context.textBaseline = "middle";
    for (let lane = 0; lane < lanes.length; lane += 1) {
      const middle = lane * laneHeight + laneHeight / 2;
      context.fillStyle = colours.line;
      context.fillRect(gutter, Math.round(middle), plot, 1);
      context.fillStyle = colours.ink;
      context.fillText(lanes[lane], 4 * ratio, middle);
    }

    bands(window_, at, colours);

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
          gutter + index * bar,
          lane * laneHeight + laneHeight - 2 * ratio - tall,
          Math.max(1, bar - ratio),
          tall,
        );
      }
    }
    context.globalAlpha = 1;
    cursorLine(window_, at, colours);
  }

  /* Where the selection is, taken from the record itself: an unasked-for
     paint (a resize, a theme change, an arriving batch) must not draw
     the line at whatever now sits at some index. */
  function cursorLine(window_, at, colours) {
    if (!picked) return;
    const width = Math.max(1, window_.to - window_.from);
    const x = at.gutter + ((picked.ts - window_.from) / width) * at.plot;
    if (x < at.gutter || x > at.gutter + at.plot) return;
    context.fillStyle = colours.ink;
    context.fillRect(Math.round(x), 0, Math.max(1, at.scale), at.height);
  }

  /* Records that cover a stretch rather than an instant, drawn as the
     stretch. A gap's whole content is how much of the axis nothing was
     watching, and a two-pixel tick at its far end says the opposite —
     that the strip either side of it is continuous.

     Painted before the marks, so a mark that survived inside a busy
     window still sits on top. A `from` of zero means the hole opened
     before anything was recorded, so the band runs off the left edge
     rather than claiming a start it does not have. */
  function bands(window_, at, colours) {
    if (!lanes.some((id) => byId.get(id)?.span)) return;
    const width = Math.max(1, window_.to - window_.from);
    const x = (ts) => at.gutter + ((ts - window_.from) / width) * at.plot;
    context.fillStyle = hatch(colours.warn, at.scale);
    for (const record of visible(window_)) {
      const entry = byCode.get(record.code);
      if (!entry || !entry.span) continue;
      const lane = lanes.indexOf(entry.id);
      if (lane < 0) continue;
      const from = Math.max(at.gutter, record.b ? x(record.b) : at.gutter);
      const to = Math.min(at.gutter + at.plot, x(record.ts));
      context.fillRect(
        from,
        lane * at.lane + 2 * at.scale,
        Math.max(1, to - from),
        at.lane - 4 * at.scale,
      );
    }
  }

  /* ---------------- interaction ---------------- */

  /* Timestamp under a pointer, and the record nearest it. Both work off
     the same geometry the paint uses, so what a reader clicks is what
     they saw rather than a second guess at where it was drawn. */
  function timeAt(event) {
    const window_ = bounds();
    if (!window_) return null;
    const box = canvas.getBoundingClientRect();
    const at = geometry(1);
    const share = Math.min(1, Math.max(0, (event.clientX - box.left - at.gutter) / at.plot));
    return window_.from + share * (window_.to - window_.from);
  }

  function laneAt(event) {
    if (!lanes.length) return null;
    const box = canvas.getBoundingClientRect();
    return lanes[Math.floor((event.clientY - box.top) / geometry(1).lane)] ?? null;
  }

  /* Every record in the window, from whichever buffer is answering for
     it. One reader for both so a click and the paint can never disagree
     about what is on screen. */
  function* visible(window_) {
    if (chosen) {
      const cols = chosen.cols;
      for (let index = 0; index < cols.ts.length; index += 1) {
        const ts = chosen.from + cols.ts[index];
        if (ts < window_.from || ts > window_.to) continue;
        yield { ts, code: cols.code[index], cpu: cols.cpu[index],
                a: cols.a[index], b: cols.b[index], c: cols.c[index] };
      }
      return;
    }
    const count = held_count();
    for (let index = 0; index < count; index += 1) {
      const at = slot(index);
      if (held.ts[at] < window_.from || held.ts[at] > window_.to) continue;
      yield { ts: held.ts[at], code: held.code[at], cpu: held.cpu[at],
              a: held.a[at], b: held.b[at], c: held.c[at] };
    }
  }

  /* Nearest in time, within the lane the pointer is on and within a few
     pixels of it. Marks are two pixels wide, so demanding an exact hit
     would put the fields out of reach of a mouse — but an unbounded
     nearest answers a click on empty space with a record from the far
     side of the strip, and in a window shown as density it would answer
     every click with the same one. */
  function nearest(ts, lane, window_, slack) {
    let best = null;
    let distance = slack;
    for (const record of visible(window_)) {
      const entry = byCode.get(record.code);
      if (!entry) continue;
      if (lane && entry.id !== lane) continue;
      const gap = Math.abs(record.ts - ts);
      if (gap <= distance) {
        distance = gap;
        best = { entry, record };
      }
    }
    if (!best) return null;
    return { id: best.entry.id, edge: best.entry.edge, fields: best.entry.fields || [],
             ...best.record };
  }

  let dragFrom = null;
  let marked = null; /* the record last clicked, for a second one to measure against */

  /* ---------------- selection ---------------- */

  /* One cursor over the records on screen, moved three ways: a click,
     an arrow key, and playback. Sharing it keeps the caption, the focus
     and the grade badge to one path.

     The cursor holds the *record*, not an index into the list it came
     from. That list is derived from the window and rebuilt on demand, so
     a resize, a drag or an arriving batch changes it and a stored index
     would then name a different record than the caption did.

     "The next one" stays answerable: rebuild the list, find where this
     record sits in it, and move. */
  let picked = null; /* the record the cursor is on */
  let playing = null; /* the playback timer */

  /* The records the cursor can be on: exactly the ones drawn. Filtered
     to what the catalogue names, since that is what `bin()` draws and
     `nearest()` clicks — an uncatalogued record would be an invisible
     mark that a step lands on and playback stops dead at, with nothing
     to caption it. A firmware hook ahead of this UI produces them. */
  function laid() {
    const window_ = bounds();
    if (!window_) return [];
    const rows = [];
    for (const record of visible(window_)) {
      if (byCode.has(record.code)) rows.push(record);
    }
    return rows.sort((a, b) => a.ts - b.ts);
  }

  /* Two yields of visible() are different objects for the same record,
     so identity cannot answer this. A record is its timestamp, its kind
     and the ring it came from. */
  const same = (a, b) =>
    Boolean(a) && Boolean(b) && a.ts === b.ts && a.code === b.code && a.cpu === b.cpu;
  const positionOf = (rows) => rows.findIndex((row) => same(row, picked));

  function named(record) {
    const entry = record && byCode.get(record.code);
    if (!entry) return null;
    return { id: entry.id, edge: entry.edge, fields: entry.fields || [], ...record };
  }

  /* Move the cursor to `index` and say what is there, with what came
     before and after it. The neighbours travel with the selection
     because the caption a reader wants is the chain — `bind -> +111 us
     inject` — and reassembling it from a bare record means keeping a
     second copy of the order somewhere. */
  function select(index, rows = laid()) {
    if (!rows.length) return false;
    const at = Math.min(rows.length - 1, Math.max(0, index));
    const record = named(rows[at]);
    if (!record) return false;
    picked = rows[at];
    marked = record;
    onSelect({
      kind: "mark",
      record,
      /* True of the list this was chosen from, which is what a reader
         is looking at. Not stored: the next move recomputes it. */
      index: at,
      total: rows.length,
      prev: named(rows[at - 1]),
      next: named(rows[at + 1]),
      /* The gap from the previous mark, in real microseconds, whatever
         speed the cursor is being moved at. */
      dt: at > 0 ? micros(record.ts - rows[at - 1].ts) : null,
      micros: micros(record.ts - (bounds()?.from ?? record.ts)),
    });
    draw();
    return true;
  }

  /* Returns where it landed, so playback does not rebuild the list to
     ask. Null when there was nowhere to go. */
  function step(by) {
    const rows = laid();
    if (!rows.length) return null;
    const from = positionOf(rows);
    const at = from < 0 ? (by > 0 ? 0 : rows.length - 1) : from + by;
    return select(at, rows) ? { rows, at: Math.min(rows.length - 1, Math.max(0, at)) } : null;
  }

  /* Auto-advance. Not a second renderer: it pushes the same cursor the
     click pushes, so everything downstream of a selection happens for
     free and cannot disagree with the manual case. */
  function play(speed = 1) {
    stop();
    const rows = laid();
    if (positionOf(rows) < 0) select(0, rows);
    const tick = () => {
      /* The step reports where it landed and in which list, so the
         pause is taken against the records actually there — a following
         strip grows underneath playback — without rebuilding it. */
      const landed = step(+1);
      if (!landed || landed.at >= landed.rows.length - 1) return stop();
      const gap = landed.rows[landed.at + 1].ts - landed.rows[landed.at].ts;
      const real = freq ? (gap * 1000) / freq : STEP_MIN_MS;
      playing = setTimeout(tick, Math.min(STEP_MAX_MS, Math.max(STEP_MIN_MS, real / speed)));
    };
    playing = setTimeout(tick, STEP_MIN_MS);
    return true;
  }

  function stop() {
    if (playing !== null) clearTimeout(playing);
    playing = null;
    return false;
  }

  /* Everything a path actually carried this run, in the order it
     carried it.
     Not a new mechanism. The bridge already answers a window filtered
     by event; the cursor already walks whatever the window returned.
     A tour is those two composed, which is why the board gained a
     click and this file gained no second way to draw a record.

     It replaces a scripted chain of numbered hops. A script can be
     wrong about the machine; a recording cannot be wrong about itself. */
  let touring = null; /* the request in flight, so its answer can start it */
  function tour(eventIds, label) {
    if (!span || !span.n || !eventIds.length) return false;
    dropSelection();
    setFollow(false);
    touring = { events: eventIds, label };
    view = { from: span.from, to: span.to };
    request({
      op: "window",
      from: view.from,
      to: view.to,
      buckets: ceiling,
      events: eventIds,
    });
    draw();
    return true;
  }

  const touringLabel = () => touring?.label ?? null;

  function dropSelection() {
    stop();
    picked = null;
    marked = null;
    /* Said, not just done: a line naming the mark and a drawer
       measuring from it are now describing one nothing is on. */
    onSelect({ kind: "none" });
  }

  const isPlaying = () => playing !== null;

  canvas.addEventListener("pointerdown", (event) => {
    dragFrom = timeAt(event);
    if (dragFrom !== null) canvas.setPointerCapture(event.pointerId);
  });

  canvas.addEventListener("pointerup", (event) => {
    const to = timeAt(event);
    const from = dragFrom;
    dragFrom = null;
    if (from === null || to === null) return;
    const window_ = bounds();
    const dragged = window_ && Math.abs(to - from) > (window_.to - window_.from) / 200;
    if (dragged) {
      /* Narrowing is a request for records the client may not hold: the
         window it leaves may reach back past the tail that following
         has been collecting. */
      view = { from: Math.round(Math.min(from, to)), to: Math.round(Math.max(from, to)) };
      setFollow(false);
      request({ op: "window", from: view.from, to: view.to, buckets: ceiling });
      draw();
      return;
    }
    /* Six pixels' worth of time: close enough to forgive a mouse,
       narrow enough that a click on empty space selects nothing. */
    const slack = ((window_.to - window_.from) / geometry(1).plot) * SLACK_PX;
    const hit = nearest(to, laneAt(event), window_, slack);
    if (!hit) return;
    if (event.shiftKey && marked) {
      onSelect({ kind: "delta", from: marked, to: hit, micros: micros(hit.ts - marked.ts) });
      return;
    }
    /* Through the cursor, not around it: a click is one of the three
       ways to move the same selection. */
    stop();
    const rows = laid();
    /* The same rule the cursor uses to find itself, rather than a
       second comparison here that forgot which ring the record came
       from — two records can share a timestamp across cores. */
    const index = rows.findIndex((row) => same(row, hit));
    if (index >= 0) select(index, rows);
  });

  /* Arrow keys walk the selection, Space plays it, Home and End are the
     two ends. On the canvas because that is what a reader has just
     clicked; the strip is focusable so the keys work before any click. */
  canvas.tabIndex = 0;
  canvas.addEventListener("keydown", (event) => {
    const acted = {
      ArrowRight: () => step(+1),
      ArrowLeft: () => step(-1),
      Home: () => select(0),
      End: () => select(laid().length - 1),
      " ": () => (isPlaying() ? stop() : play()),
    }[event.key];
    if (!acted) return;
    /* Space scrolls a page and the arrows scroll a strip; neither is
       what a reader stepping through events asked for. */
    event.preventDefault();
    if (follow) setFollow(false); /* stepping is not following */
    acted();
  });

  /* Everything the bridge still holds — the widest honest view, and not
     "the whole run", which stopped existing at the horizon. Following
     ends here by definition: this window has a fixed left edge. The
     follow button is how a reader comes back to the present. */
  canvas.addEventListener("dblclick", () => {
    if (!span || !span.n) return;
    marked = null;
    view = { from: span.from, to: span.to };
    setFollow(false);
    request({ op: "window", from: view.from, to: view.to, buckets: ceiling });
    draw();
  });

  const micros = (ticks) => (freq ? Math.round((ticks * 1e6) / freq) : null);

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

  return {
    setCatalogue,
    setLimits,
    note,
    apply,
    reset,
    setFollow,
    draw,
    freqHz: () => freq,
    /* One cursor, three movers. Exposed so the chrome can drive it the
       same way a key does — a second entry point into the board is how
       the caption, the focus and the grade end up existing twice. */
    select,
    step,
    play,
    stop,
    isPlaying,
    tour,
    touringLabel,
  };
}

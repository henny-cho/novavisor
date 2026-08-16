/* The board: the machine drawn as layers, with measured values in place.

   Three rules hold this together.

   Vertical position is privilege — EL1, EL2, the PE, the interconnect,
   devices, memory — so a path through the machine reads as a vertical
   traversal and an edge's direction means something on its own.

   Nothing structural is written here. Addresses, sizes, interrupt
   numbers and the CPU count all arrive in `topo.board`, generated from
   the same headers the linker script and the DTB generator read.

   No value without evidence. A block shows what a topic actually
   carries and says which layer it came from; what is not observed today
   says so rather than being filled in plausibly. */

import { accentOf, clear, el, stamp, vmSlot } from "./format.mjs";

const SIZE_KEY = "nv-wb-view-h";
const FOLD_KEY = "nv-wb-view-folded";
const VM_SLOTS = 4; /* accent classes v0..v3 cycle */
const NS = "http://www.w3.org/2000/svg";
const MIN_HEIGHT = 200;
/* What the console keeps when the board opens itself: its header, a
   readable number of lines, and the input row. Dragging the split may
   go past this — that is the reader's decision, not the layout's. */
const CONSOLE_FLOOR = 300;
const DRAG_FLOOR = 220;
/* Below this the console has no room left worth sharing. */
const SHORT_WINDOW = 800;

/* S-layer topics the board reads, each with the sections it can change.

   A snapshot repaints those sections and nothing else. Twenty scheduler
   samples a second must not rewrite the address strip, and a topic that
   paints nothing has no business being subscribed at all.

   The rates are not here. How often a topic is sampled is the
   manifest's to state, and it arrives in `topo.observations`. */
const TOPICS = {
  "sched.cpu": ["routes", "vcpus", "cores"],
  "sched.run": ["vcpus"],
  "sched.affinity": ["vcpus"],
  "sched.slice": ["sched"],
  "timer.queue": ["timer"],
  "timer.programmed": ["cores"],
  "vm.generation": ["guests"],
  "ctx.syndrome": ["trap"],
  "vgic.lr": ["lrs"],
  "vgic.token": ["vgic"],
  "vgic.capacity": ["lrs", "vgic"],
  "vgic.dist": ["vgic"],
  "vgic.resident": ["lrs", "routes"],
  "dev.uart": ["vuart"],
  "dev.dma": ["devices"],
  "ivc.page": ["ivc"],
  "smp.online": ["routes", "cores"],
};

/* A list register's state, as a mark that survives being printed in
   one colour. The bridge names the states; these are the marks. */
const LR_GLYPH = {
  pending: "▲",
  active: "■",
  "pending+active": "◆",
};

/* What each path is called, keyed by the edge id the bridge assigns.
   Same split as the segment captions below: the bridge states which
   blocks a path joins and what watches it, the UI supplies the words.
   An edge with no caption here still draws — it just goes unnamed. */
const EDGE_TEXT = {
  trap: "게스트 트랩 → EL2",
  phys: "물리 IRQ → PE",
  post: "장치 SPI → 분배기",
  inject: "vIRQ 주입 → 게스트",
  mmio: "MMIO 트랩 → 에뮬레이션",
  dma: "장치 DMA → SMMU",
  walk: "SMMU 변환 → 메모리",
  cross: "코어 간 크로스콜",
  ivc: "IVC 도어벨 → 공유 페이지",
  psci: "PSCI 기동 → PE",
  uart: "vuart → 물리 UART",
};

/* The classes a pulse cycles through: two per grade.

   It alternates because re-adding a class the element already has does
   not restart a CSS animation — the style change is coalesced and the
   browser sees nothing change. The usual fix is to read a layout
   property in between, which is the one thing this view never does.

   Each class must resolve to a *differently named* set of keyframes, not
   just a different selector: an animation is identified by its name, so
   two classes sharing one name leave the running animation alone. The
   CSS carries the pair; this only decides which is next.

   The failure without it is silent — a second piece of evidence during a
   pulse would show nothing, and the screen would read as though nothing
   had happened. */
const PULSE = ["lit-a", "lit-b", "hit-a", "hit-b"];

/* Address-map segment captions, keyed by the kind the bridge assigns.
   The map states structure; the words for it belong to the UI. */
const KIND_TEXT = {
  el2: "EL2 이미지",
  guest: "게스트 창",
  shared: "공유 페이지",
  trace: "트레이스 링",
  pristine: "pristine 사본",
  hole: "미사용",
  trap: "미매핑 → 트랩",
  assigned: "직접 할당",
};

const hex = (value) => `0x${Number(value).toString(16).toUpperCase().padStart(8, "0")}`;

function bytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size)) return "?";
  const units = ["B", "KiB", "MiB", "GiB"];
  let at = 0;
  let scaled = size;
  while (scaled >= 1024 && at < units.length - 1) {
    scaled /= 1024;
    at += 1;
  }
  return `${Number.isInteger(scaled) ? scaled : scaled.toFixed(1)} ${units[at]}`;
}

/* The bridge decodes the firmware's all-bits-set "none" to null, so
   there is one thing to test for and no width to know. */
const present = (value) => value !== undefined && value !== null;

/* Text is written through here so an unchanged tick costs no layout:
   twenty snapshots a second would otherwise rebuild the whole board. */
function put(node, text) {
  const next = String(text);
  if (node.textContent !== next) node.textContent = next;
}

function evidence(kind, label) {
  return el("span", `src ${kind}`, label);
}

export function createBoard({ view, board, bands, wires, split, foldButton, onFocus, onTour }) {
  const latest = new Map(); // topic -> value
  /* Nodes the tick writes into, filled in while the skeleton is built.
     Rebuilding markup instead would drop hover, selection and focus. */
  let live = {};
  const dirty = new Set(); // section names awaiting a repaint
  /* Measured geometry, kept until something that can move it happens.
     Null means "re-measure on the next draw". */
  let geometry = null;
  let where = new Map(); // vcpu slot -> cpu index it is resident on
  let whereSig = null;
  let byTopic = new Map(); // topic -> paths it is evidence for
  let byBadge = new Map(); // console badge -> paths it is evidence for
  let byId = new Map(); // path id -> the path, for measured stops
  const lit = new Set(); // topics that arrived this batch
  let focused = null; // anchor id the reader is looking at
  let topology = null;
  let signature = null;
  let userSized = false;
  let fitting = false;

  const value = (topic) => latest.get(topic);
  const folded = () => view.classList.contains("folded");
  /* How coarse a sample is, and whether anything holds a run to it,
     both belong to the manifests that know. A value on screen with no
     demo checking it is a claim; one with a predicate is a guarantee,
     and a reader cannot tell them apart from the number alone. */
  const about = (topic) => topology?.observations?.[topic];
  const sampled = (topic) => {
    const said = about(topic);
    const badge = evidence("s", said?.rate ? `S ${said.rate}Hz` : "S");
    if (said?.asserted) {
      badge.classList.add("held");
      badge.title = "a demo checks this reading";
    }
    return badge;
  };
  /* Firmware identifiers with the k trimmed: kHvcAa64 reads as HvcAa64.
     Trimming a prefix is a rule; a table of prettier names would be a
     second vocabulary to keep in step with the first. */
  const className = (ec) => (topology?.taxonomy?.esr_ec?.[ec] || "").replace(/^k/, "");

  /* ---------------- geometry: split, fold, fit ---------------- */

  function setHeight(px, persist = true) {
    const room = view.parentElement.getBoundingClientRect().height;
    const max = Math.max(MIN_HEIGHT, room - DRAG_FLOOR);
    const next = Math.round(Math.min(max, Math.max(MIN_HEIGHT, px)));
    view.style.setProperty("--wb-view", `${next}px`);
    if (persist) {
      try {
        localStorage.setItem(SIZE_KEY, String(next));
      } catch (error) {
        /* private mode: the size simply does not persist */
      }
    }
    drawOverlay();
  }

  /* Open to what the content needs, capped at most of the column. A
     fixed height silently clips the bottom band on a short window, and
     an overflow delta cannot detect "too large" — scrollHeight never
     drops below clientHeight — so the target is computed directly. */
  function fitHeight(passes = 3) {
    if (userSized || folded() || fitting) return;
    fitting = true;
    requestAnimationFrame(() => {
      fitting = false;
      if (userSized || folded()) return;
      const style = getComputedStyle(board);
      const pad = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      const chrome = view.getBoundingClientRect().height - board.clientHeight;
      const cap = Math.max(MIN_HEIGHT,
        view.parentElement.getBoundingClientRect().height - CONSOLE_FLOOR);
      setHeight(Math.min(bands.scrollHeight + pad + chrome, cap), false);
      if (passes > 1) fitHeight(passes - 1);
    });
  }

  function setFolded(on, persist = true) {
    view.classList.toggle("folded", on);
    split.hidden = on;
    foldButton.textContent = on ? "펼치기" : "접기";
    foldButton.setAttribute("aria-expanded", String(!on));
    if (persist) {
      try {
        localStorage.setItem(FOLD_KEY, on ? "1" : "");
      } catch (error) {
        /* private mode */
      }
    }
    if (!on) {
      /* Everything was measured at zero while the body was hidden. */
      invalidate();
      paintAll();
      fitHeight();
    }
  }

  function invalidate() {
    geometry = null;
  }

  /* Wires and height both depend on the laid-out box, which is only
     right after the browser has reflowed — a resize handler that reads
     it synchronously catches the previous frame's geometry, and
     widening the window never fires a second event to correct it.

     Blocks are watched alongside the grid: a band can keep its height
     while a block inside it shrinks, which moves an anchor without
     moving `bands`. */
  const watch = new ResizeObserver(() => {
    invalidate();
    drawOverlay();
    fitHeight();
  });

  let dragging = false;
  split.addEventListener("pointerdown", (event) => {
    dragging = true;
    split.setPointerCapture(event.pointerId);
  });
  split.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    userSized = true;
    setHeight(event.clientY - view.getBoundingClientRect().top);
  });
  split.addEventListener("pointerup", () => {
    dragging = false;
  });
  split.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    userSized = true;
    const step = event.shiftKey ? 40 : 12;
    const now = view.getBoundingClientRect().height;
    setHeight(now + (event.key === "ArrowUp" ? -step : step));
  });
  foldButton.addEventListener("click", () => setFolded(!folded()));

  /* ---------------- wiring ---------------- */

  /* Where each block sits, relative to the grid's own origin. Keeping
     both edges and the centre means a caller picks an endpoint without
     measuring again. */
  function boxOf(node, base) {
    const box = node.getBoundingClientRect();
    return {
      cx: box.left - base.left + box.width / 2,
      top: box.top - base.top,
      bottom: box.bottom - base.top,
      left: box.left - base.left,
      right: box.right - base.left,
    };
  }

  const endpoint = (box, bottom) => (box ? [box.cx, bottom ? box.bottom : box.top] : null);

  /* The board is measured here and nowhere else, so a snapshot can
     never force a layout. What is read: the grid's own box, the EL2
     band the wires thread through, the gaps between its chips, and one
     box per registered block.

     Blocks register unconditionally at build time, so which boxes exist
     follows the topology and never a value. A box that appeared only
     while some badge was shown would turn that badge's every flicker
     into a full re-measure — which is exactly how the injection-mismatch
     badge cost fourteen forced layouts a batch before B2. */
  function measure() {
    const hyp = live.hypBand;
    if (!hyp) return null;
    const base = bands.getBoundingClientRect();
    const band = hyp.getBoundingClientRect();
    const at = {};
    for (const { id, node } of live.anchors || []) at[id] = boxOf(node, base);
    const gaps = [];
    const kids = [...hyp.children].map((kid) => kid.getBoundingClientRect());
    for (let i = 1; i < kids.length; i += 1) {
      const left = kids[i - 1].right - base.left;
      const right = kids[i].left - base.left;
      if (right - left >= 4) gaps.push([left, right]);
    }
    /* Each band and the columns in it nothing occupies. A path crossing
       a band drops down one of those columns instead of over a block,
       and the horizontal jogs happen between bands, where there is
       nothing to cross by construction. */
    const rows = [];
    for (const { id, node } of live.anchors || []) {
      if (!id.startsWith("band:")) continue;
      const box = at[id];
      const spans = [...node.children]
        .map((kid) => kid.getBoundingClientRect())
        .map((kid) => [kid.left - base.left, kid.right - base.left])
        .sort((a, b) => a[0] - b[0]);
      const free = [];
      let edge = box.left;
      for (const [left, right] of spans) {
        if (left - edge >= 7) free.push([edge, left]);
        edge = Math.max(edge, right);
      }
      if (box.right - edge >= 7) free.push([edge, box.right]);
      rows.push({ top: box.top, bottom: box.bottom, free });
    }
    rows.sort((a, b) => a.top - b.top);
    return {
      width: base.width,
      height: base.height,
      top: band.top - base.top - 3,
      bottom: band.bottom - base.top + 3,
      gaps,
      rows,
      at,
      from: (live.links || []).map((link) => endpoint(at[link.anchor], true)),
      cores: (live.cores || []).map((core) => endpoint(at[core.anchor], false)),
    };
  }

  /* Route through the widest gap between the EL2 chips rather than over
     them: a straight drop crosses one mid-word. A line already clear of
     them is left alone. */
  function lane(want) {
    let best = null;
    for (const [left, right] of geometry.gaps) {
      if (want > left && want < right) return want;
      const at = (left + right) / 2;
      if (best === null || Math.abs(at - want) < Math.abs(best - want)) best = at;
    }
    return best === null ? want : best;
  }

  function stroke(path, color, dashed) {
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", color);
    path.setAttribute("stroke-width", dashed ? "1.5" : "4");
    if (dashed) path.setAttribute("stroke-dasharray", "4 3");
    path.setAttribute("opacity", dashed ? ".8" : ".9");
  }

  /* One set of SVG nodes per wire, created with the skeleton. A redraw
     then rewrites `d` and two centres and nothing else: rebuilding
     sixteen elements on every context switch is exactly the churn `put`
     exists to avoid. */
  function buildWires() {
    clear(wires);
    for (const link of live.links) {
      const casing = document.createElementNS(NS, "path");
      /* A casing in the background colour first, so the line stays
         legible where it passes over a block. */
      stroke(casing, "var(--bg)", false);
      const line = document.createElementNS(NS, "path");
      stroke(line, "var(--hyp)", true);
      const tip = document.createElementNS(NS, "title");
      line.append(tip);
      const dots = [0, 1].map(() => {
        const dot = document.createElementNS(NS, "circle");
        dot.setAttribute("r", "2.4");
        dot.setAttribute("fill", "var(--hyp)");
        return dot;
      });
      Object.assign(link, { casing, line, tip, dots, nodes: [casing, line, ...dots] });
      wires.append(...link.nodes);
    }
  }

  function showWire(link, on) {
    if (link.shown === on) return;
    link.shown = on;
    for (const node of link.nodes) node.style.display = on ? "" : "none";
  }

  /* The overlay is two layers over one geometry: residency wires, which
     say where a vCPU lives, and paths, which say what the machine can
     do. Measuring once here means neither layer can force a layout of
     its own, and both stay in the same coordinate space. */
  function drawOverlay() {
    if (!wires || folded()) return;
    if (!geometry) geometry = measure();
    if (!geometry) {
      for (const link of live.links || []) showWire(link, false);
      for (const edge of live.edges || []) showEdge(edge, false);
      return;
    }
    /* Rewriting the viewBox invalidates the whole overlay even when the
       value is identical, so it is only touched when it moves. */
    const box = `0 0 ${geometry.width} ${geometry.height}`;
    if (wires.getAttribute("viewBox") !== box) wires.setAttribute("viewBox", box);
    drawWires();
    drawEdges();
  }

  function drawWires() {
    const { top, bottom } = geometry;
    (live.links || []).forEach((link, index) => {
      /* A vCPU that is not resident anywhere has no core to point at,
         and an absent edge is the honest drawing of that. */
      const start = geometry.from[index];
      const end = link.cpu === null ? null : geometry.cores[link.cpu];
      if (!start || !end) {
        showWire(link, false);
        return;
      }
      const [x1, y1] = start;
      const [x2, y2] = end;
      /* Right angles with filleted corners, laid in the gaps between
         bands: a diagonal jog reads as a stray mark and a sharp corner
         reads as a bracket, but a fillet reads as wiring. */
      const gap = lane((x1 + x2) / 2);
      const r = 6;
      const bend = (x, y, target, dir) =>
        `Q${x},${y} ${x + dir * r},${y} H${target - dir * r} Q${target},${y} ${target},${y + r}`;
      const d =
        Math.abs(gap - x1) < 2 * r || Math.abs(x2 - gap) < 2 * r
          ? `M${x1},${y1} V${y2}`
          : `M${x1},${y1} V${top - r} ${bend(x1, top, gap, Math.sign(gap - x1))}` +
            ` V${bottom - r} ${bend(gap, bottom, x2, Math.sign(x2 - gap))} V${y2}`;

      if (link.d !== d) {
        link.d = d;
        link.casing.setAttribute("d", d);
        link.line.setAttribute("d", d);
        [[x1, y1], [x2, y2]].forEach(([cx, cy], side) => {
          link.dots[side].setAttribute("cx", cx);
          link.dots[side].setAttribute("cy", cy);
        });
      }
      put(link.tip, link.title);
      showWire(link, true);
    });
  }

  /* ---------------- paths ---------------- */

  /* One thin path per edge, appended after the wires so it draws in
     front of them. Which blocks it joins and what watches it arrive in
     topo.board; only the caption and the drawing are decided here. */
  function buildEdges() {
    live.edges = [];
    byTopic = new Map();
    byBadge = new Map();
    byId = new Map();
    const specs = topology?.board?.edges || [];
    /* Two paths may join the same pair of blocks in opposite directions
       — the guest's MMIO out and the vGIC's injection back. Drawn on one
       column they would be a single line, so each pair is counted and
       its members fanned apart. */
    const crowd = new Map();
    const key = (spec) => [spec.from, spec.to].sort().join("|");
    for (const spec of specs) crowd.set(key(spec), (crowd.get(key(spec)) || 0) + 1);
    const seen = new Map();
    for (const spec of specs) {
      const group = key(spec);
      const rank = seen.get(group) || 0;
      seen.set(group, rank + 1);
      /* An invisible companion, wide enough to be aimed at. A path is
         drawn 1.25px thin on purpose — the width is what says how well
         it is observed — so the click target cannot be the line itself
         without changing what a reader reads from it. */
      const hit = document.createElementNS(NS, "path");
      hit.setAttribute("class", "edge-hit");
      hit.setAttribute("fill", "none");
      hit.dataset.edge = spec.id;
      wires.append(hit);
      const line = document.createElementNS(NS, "path");
      line.setAttribute("class", `edge ${spec.grade}`);
      line.setAttribute("fill", "none");
      /* The colour of the badge whose rows explain it, so a reader
         crossing between board and log follows one colour. An edge with
         no badge takes the board's own line colour. */
      if (spec.badges?.length) line.style.setProperty("--ec", accentOf(spec.badges[0]));
      const tip = document.createElementNS(NS, "title");
      line.append(tip);
      wires.append(line);
      const edge = {
        ...spec,
        line,
        hit,
        tip,
        shown: false,
        d: "",
        turn: 0,
        /* Centred on zero: one path stays straight, two split evenly. */
        fan: (rank - (crowd.get(group) - 1) / 2) * 11,
      };
      live.edges.push(edge);
      byId.set(edge.id, edge);
      if (spec.topic) {
        if (!byTopic.has(spec.topic)) byTopic.set(spec.topic, []);
        byTopic.get(spec.topic).push(edge);
      }
      /* A badge may reach more than one path: an unclaimed MMIO really
         is both a trap and an emulation miss, so both light. */
      for (const badge of spec.badges || []) {
        if (!byBadge.has(badge)) byBadge.set(badge, []);
        byBadge.get(badge).push(edge);
      }
    }
  }

  /* Light a path because evidence for it just arrived.

     `exact` picks the motion, not the colour: a console line is precise
     in time and runs smooth, while a snapshot delta marches in steps
     because that is what a sample is. A path graded as polled still
     gets the smooth motion when a console line is what lit it — the
     better evidence wins for that one pulse. */
  function flash(edge, exact) {
    edge.turn ^= 1;
    const next = PULSE[(exact ? 2 : 0) + edge.turn];
    edge.line.classList.remove(...PULSE);
    edge.line.classList.add(next);
  }

  /* A band spans its whole row, so pinning a path to the band's centre
     would stack every path touching that layer on one column. The block
     end supplies the x instead: each drop is vertical, separate, and
     still lands inside the layer it names. */
  const spans = (id) => id.startsWith("band:") || id === "mem";

  /* The column of a band nearest `want` that no block sits in. */
  function freeColumn(row, want) {
    let best = null;
    for (const [left, right] of row.free) {
      if (want > left + 2 && want < right - 2) return want;
      const at = (left + right) / 2;
      if (best === null || Math.abs(at - want) < Math.abs(best - want)) best = at;
    }
    return best === null ? want : best;
  }

  function routeEdge(edge, from, to) {
    const ay = (from.top + from.bottom) / 2;
    const by = (to.top + to.bottom) / 2;
    const x1 = (spans(edge.from) ? to.cx : from.cx) + edge.fan;
    const x2 = (spans(edge.to) ? from.cx : to.cx) + edge.fan;
    if (Math.abs(ay - by) < 8) {
      /* Same row: an arc below both, rather than a line straight through
         whatever sits between them. */
      const under = Math.max(from.bottom, to.bottom) + 14;
      return `M${x1},${from.bottom} Q${(x1 + x2) / 2},${under} ${x2},${to.bottom}`;
    }
    const down = ay < by;
    const y1 = down ? from.bottom : from.top;
    const y2 = down ? to.top : to.bottom;
    const lo = Math.min(y1, y2);
    const hi = Math.max(y1, y2);
    /* Bands wholly between the two ends: the ones this path has to get
       past. Adjacent blocks cross none, and the loop below collapses to
       a single jog in the gutter between them. */
    const crossed = geometry.rows.filter((row) => row.top > lo + 2 && row.bottom < hi - 2);
    if (!down) crossed.reverse();
    let cursor = y1;
    let x = x1;
    let d = `M${x1},${y1}`;
    for (const row of crossed) {
      const column = freeColumn(row, x);
      if (Math.abs(column - x) > 1) {
        d += ` V${(cursor + (down ? row.top : row.bottom)) / 2} H${column}`;
        x = column;
      }
      cursor = down ? row.bottom : row.top;
    }
    if (Math.abs(x - x2) > 1) d += ` V${(cursor + y2) / 2} H${x2}`;
    return `${d} V${y2}`;
  }

  function showEdge(edge, on) {
    if (edge.shown === on) return;
    edge.shown = on;
    edge.line.style.display = on ? "" : "none";
    /* Together, always: a hit area left behind by a hidden path is a
       click target for something not on screen. */
    edge.hit.style.display = on ? "" : "none";
  }

  function drawEdges() {
    for (const edge of live.edges || []) {
      const from = geometry.at[edge.from];
      const to = geometry.at[edge.to];
      if (!from || !to) {
        showEdge(edge, false);
        continue;
      }
      const d = routeEdge(edge, from, to);
      if (edge.d !== d) {
        edge.d = d;
        edge.line.setAttribute("d", d);
        edge.hit.setAttribute("d", d);
      }
      put(edge.tip, edgeTitle(edge));
      showEdge(edge, true);
    }
  }

  /* What the path is, how well it is seen, and what it last carried.

     Never how often: the syndrome latches only the last trap and the
     in-flight list is a sample, so a count would be unmeasured. "Last
     seen at" is exactly as much as the evidence supports. */
  function edgeTitle(edge) {
    const name = edge.label || EDGE_TEXT[edge.id] || edge.id;
    const seen = edge.last ? ` · 마지막 ${stamp(edge.last.ts)} ${edge.last.message || ""}` : "";
    if (edge.grade === "direct") return `${name} — 정지 가능 · 실측${seen}`;
    if (edge.grade === "console") return `${name} — 콘솔 이벤트 · 시각 정확${seen}`;
    if (edge.grade === "poll") {
      const hz = about(edge.topic)?.rate;
      return `${name} — ${edge.topic} 표본${hz ? ` · S ${hz}Hz` : ""}${seen}`;
    }
    return `${name} — 관측 없음 · 구조만 표시`;
  }


  /* A classified console line, routed to the paths it is evidence for.

     The board holds no regex. Reading the firmware's text is the
     bridge's job and one contract test already ties every rule there to
     a real firmware string; a second parser here would sit outside it. */
  function note(ts, data) {
    const edges = byBadge.get(String(data && data.badge));
    if (!edges) return;
    for (const edge of edges) {
      /* A path the firmware records for itself does not also get lit by
         a line of its own log output. Two sources for one fact means
         the pulse count is neither, and the console is the weaker of
         the two — it says a message was printed, not that the event
         happened. */
      if (edge.grade === "direct") continue;
      edge.last = { ts, message: data.message };
      put(edge.tip, edgeTitle(edge));
      /* Exact motion even on a sampled path: for this one pulse the
         console is the better evidence, and it should look like it. */
      if (!folded()) flash(edge, true);
    }
  }

  /* What the firmware recorded for itself, drained from its rings.

     The counts are per drain window rather than per event: the bridge
     sees every record and sends the tally, because a browser cannot be
     handed a few thousand frames a second and a cap with a silent drop
     would make the number a lie. So the pulse says "this path was used
     in the last window", and the caption says how many times. */
  function traced(ts, data) {
    const counts = (data && data.edges) || {};
    const last = (data && data.last) || {};
    for (const id of Object.keys(counts)) {
      const edge = byId.get(id);
      if (!edge) continue;
      const values = last[id];
      const named = values
        ? Object.keys(values)
            .filter((key) => key !== "event" && key !== "ts")
            .map((key) => `${key}=${values[key]}`)
            .join(" ")
        : "";
      edge.last = { ts, message: `${counts[id]}회${named ? ` · ${named}` : ""}` };
      put(edge.tip, edgeTitle(edge));
      if (!folded()) flash(edge, true);
    }
  }

  /* The machine stopped on this path, and here is what it was carrying.

     Unlike every other route into this view, nothing here is inferred.
     The values are the event's own arguments, read out of the argument
     registers while the machine was held still — so the caption states
     them rather than describing how closely they were watched. */
  function stopped(ts, data) {
    const edge = byId.get(String(data && data.edge));
    if (!edge) return;
    const args = (data && data.args) || {};
    const named = Object.keys(args)
      .map((key) => `${key}=${args[key]}`)
      .join(" ");
    edge.last = { ts, message: named || data.event || "" };
    put(edge.tip, edgeTitle(edge));
    if (!folded()) flash(edge, true);
  }

  /* ---------------- skeleton ---------------- */

  /* A band is an anchor too. A path that belongs to a whole privilege
     layer — every guest traps — points at the band rather than at eight
     rows, because eight lines saying the same thing hide the one thing
     they have in common. */
  function layer(title, caption, className) {
    const label = el("div", `layer-label${className === "live" ? " live" : ""}`, title);
    label.append(el("small", "", caption));
    const band = el("div", `band${className ? ` ${className}` : ""}`);
    bands.append(label, band);
    anchor(`band:${title.toLowerCase()}`, band);
    return band;
  }

  function blockHead(parent, title, meta, src) {
    const head = el("div", "bh");
    head.append(el("span", "bt", title));
    const metaNode = el("span", "bm", meta);
    head.append(metaNode);
    if (src) head.append(src);
    parent.append(head);
    return metaNode;
  }

  function guests() {
    const list = topology && Array.isArray(topology.guests) ? topology.guests : [];
    return list.map((guest, index) => ({ ...guest, slot: vmSlot(guest, index) }));
  }

  const stride = () => Number(topology?.board?.vcpu_stride) || 1;
  const cpuCount = () => Number(topology?.board?.cpus) || 1;
  const slotOf = (vm, index) => vm * stride() + index;
  const vmOfSlot = (slot) => Math.floor(slot / stride());

  /* Name a block so something can point at it later.

     Called once per block while building, never from a paint: the set
     of geometry is a property of the machine, not of what it is doing.
     An id registered here is the only kind of endpoint a wire or an
     edge may name, so a typo fails at build rather than drawing a line
     to the origin. */
  function anchor(id, node) {
    live.anchors.push({ id, node });
    node.dataset.anchor = id;
    return id;
  }

  /* ---------------- focus ---------------- */

  /* What a path joins, read off the published table. Focusing a block
     keeps its neighbours lit; naming a band keeps everything in that
     band lit, because a path to a layer is a path to all of it. */
  function neighbours(id) {
    const near = new Set([id]);
    for (const edge of live.edges || []) {
      if (edge.from === id) near.add(edge.to);
      else if (edge.to === id) near.add(edge.from);
    }
    return near;
  }

  /* Badges of every path touching the block, so the log can be narrowed
     to the subsystems that explain it. Derived from the same table the
     lines are drawn from — a second list here would drift from it. */
  function badgesAt(id) {
    const names = new Set();
    for (const edge of live.edges || []) {
      if (edge.from !== id && edge.to !== id) continue;
      for (const badge of edge.badges || []) names.add(badge);
    }
    return [...names];
  }

  function setFocus(id) {
    focused = id;
    bands.classList.toggle("focusing", Boolean(id));
    const near = id ? neighbours(id) : null;
    for (const entry of live.anchors || []) {
      const node = entry.node;
      if (node.classList.contains("band")) continue;
      const band = node.parentElement?.dataset?.anchor;
      const lit = !near || near.has(entry.id) || (band && near.has(band));
      node.classList.toggle("dim", !lit);
      node.classList.toggle("on", Boolean(id) && entry.id === id);
    }
    for (const edge of live.edges || []) {
      edge.line.classList.toggle("off", Boolean(id) && edge.from !== id && edge.to !== id);
    }
    if (onFocus) onFocus(id ? badgesAt(id) : null);
  }

  function buildRoutes() {
    const band = layer("LIVE", "RESIDENCY", "map-layer");
    const span = cpuCount() <= 2 ? "c6" : "c4";
    live.routes = [];
    for (let cpu = 0; cpu < cpuCount(); cpu += 1) {
      const route = el("div", `route ${span}`);
      const left = el("span", "route-node");
      const who = el("b", "", "—");
      const state = el("small", "route-state", "idle");
      left.append(who, state);
      const right = el("span", "route-node cpu");
      const core = el("b", "", `pCPU${cpu}`);
      const detail = el("small", "", "—");
      right.append(core, detail);
      const flow = el("span", "route-flow");
      flow.setAttribute("aria-hidden", "true");
      const disagree = el("span", "disagree");
      disagree.hidden = true;
      route.append(left, flow, right, disagree);
      band.append(route);
      live.routes.push({ route, who, state, detail, disagree });
    }
  }

  function buildGuests() {
    const band = layer("EL1", "GUEST", "guest-layer");
    const list = guests();
    live.guests = [];
    if (!list.length) {
      band.append(el("div", "empty c12", "게스트 없음 — 타깃을 실행하면 채워집니다."));
      return;
    }
    const span = list.length <= 2 ? "c6" : list.length === 3 ? "c4" : "c3";
    for (const guest of list) {
      const node = el("div", `blk vmc ${span} v${guest.slot % VM_SLOTS}`);
      const name = String(guest.name || `vm${guest.slot}`);
      const meta = blockHead(node, `VM${guest.slot} ${name}`, "", sampled("vm.generation"));
      /* Where the guest was loaded is a property of the run, not of any
         sample: it is written once here and never touched by a tick. */
      const placement = el("div", "bv");
      if (Number.isFinite(Number(guest.ipa))) {
        placement.append(
          document.createTextNode("IPA "),
          el("em", "", hex(guest.ipa)),
          document.createTextNode(` +${bytes(guest.size)} → PA `),
          el("em", "", hex(guest.pa)),
        );
      } else {
        placement.textContent = "창 정보 없음";
      }
      node.append(placement);
      const vcpus = [];
      const count = Math.max(1, Number(guest.vcpus) || 1);
      for (let index = 0; index < count; index += 1) {
        const slot = slotOf(guest.slot, index);
        const row = el("div", "vcpu");
        const dot = el("i", "dot");
        const state = el("span", "", "—");
        const affinity = el("span", "vaf", "");
        /* How many cells is the machine's answer, not a guess: it
           arrives in vgic.capacity once EL2 has read ICH_VTR. Until
           then there is nothing honest to draw. */
        const lrs = el("span", "lrs");
        const label = el("span", "ll", "LR");
        const waiting = el("span", "pending", "대기");
        waiting.title = "EL2가 ICH_VTR을 읽으면 칸 수가 정해집니다";
        lrs.append(label, waiting);
        row.append(el("span", "vn", `s${slot}`), dot, state, affinity, lrs);
        node.append(row);
        vcpus.push({ slot, row, dot, state, affinity, lrs, waiting, cells: [], anchor: anchor(`vcpu${slot}`, row) });
      }
      band.append(node);
      anchor(`vm${guest.slot}`, node);
      live.guests.push({ guest, node, meta, vcpus });
    }
  }

  /* The chip's body is what a paint writes into; its outer node is what
     an edge points at. Returning the body keeps every caller unchanged. */
  function chip(band, id, title) {
    const node = el("div", "chip c2");
    node.append(el("div", "bc", title));
    const body = el("div", "cv");
    node.append(body);
    band.append(node);
    anchor(id, node);
    return body;
  }

  function buildHypervisor() {
    const band = layer("EL2", "HYPERVISOR", "hyp-layer");
    live.hypBand = band;
    live.chips = {
      trap: chip(band, "trap", "trap_router"),
      sched: chip(band, "sched", "scheduler"),
      timer: chip(band, "timer", "soft_timer"),
      vgic: chip(band, "vgic", "vgic"),
      vuart: chip(band, "vuart", "vuart"),
      ivc: chip(band, "ivc", "ivc"),
    };
    /* Two trap lines, built once: the chip is a sixth of the band and a
       third line makes the whole EL2 row taller than its neighbours. */
    live.trapLines = [el("em"), el("br"), el("em")];
    live.chips.trap.append(...live.trapLines);
  }

  function buildCores() {
    const band = layer("PE", "PHYSICAL", "pe-layer");
    const span = cpuCount() <= 2 ? "c6" : "c3";
    live.cores = [];
    for (let cpu = 0; cpu < cpuCount(); cpu += 1) {
      const node = el("div", `blk ${span}`);
      blockHead(node, `pCPU${cpu}`, topology?.board?.cpu || "", sampled("sched.cpu"));
      const body = el("div", "bv");
      const home = el("em", "", "없음");
      const rest = document.createTextNode("");
      body.append(document.createTextNode("거주 "), home, rest);
      const row = el("div", "perow");
      node.append(body, row);
      band.append(node);
      live.cores.push({ node, home, rest, row, anchor: anchor(`core${cpu}`, node) });
    }
  }

  /* A block the board did not invent: its id is the one the bridge
     assigned in topo.board, so an edge naming `gicd` or `smmu` names
     the same thing the platform headers do. */
  function staticBlock(band, block, span) {
    const node = el("div", `blk ${span}`);
    const detail = [];
    if (block.base !== undefined) detail.push(hex(block.base));
    if (block.size) detail.push(`+${bytes(block.size)}`);
    blockHead(node, block.label, detail.join(" "), null);
    band.append(node);
    anchor(String(block.id), node);
    return node;
  }

  function buildInterconnect() {
    const blocks = (topology?.board?.blocks || []).filter((block) => block.layer === "ic");
    const band = layer("IC", "INTERRUPT", "");
    const span = blocks.length <= 3 ? "c4" : "c3";
    for (const block of blocks) {
      const node = staticBlock(band, block, span);
      const body = el("div", "bv");
      if (block.id === "smmu") {
        body.append(
          document.createTextNode(`SID ${block.sid_bits ?? "?"}b · SPI ${(block.intids || []).join(" ")}`),
        );
        node.querySelector(".bh").append(evidence("con", "콘솔"));
      } else if (block.cpu !== undefined) {
        body.textContent = `pCPU${block.cpu} 프레임`;
      } else {
        body.textContent = block.note || "";
      }
      node.append(body);
    }
  }

  function buildDevices() {
    const blocks = (topology?.board?.blocks || []).filter((block) => block.layer === "dev");
    const band = layer("DEV", "DEVICE", "");
    const span = blocks.length <= 3 ? "c4" : "c3";
    live.devices = [];
    for (const block of blocks) {
      const node = staticBlock(band, block, span);
      const body = el("div", "bv");
      node.append(body);
      if (block.intid !== undefined) {
        node.querySelector(".bm").textContent += ` · INTID ${block.intid}`;
      }
      if (block.owner === "el2") {
        body.textContent = "호스트 콘솔 — EL2 소유";
      } else if (block.device_id !== undefined) {
        /* Assignment is per run, not per board: the DMA registry says
           whether this demo took the device, so the block waits for it. */
        live.devices.push({ block, node, body });
        node.classList.add("unused");
        body.textContent = "미할당";
      } else {
        node.classList.add("unused");
        body.textContent = "패스스루 없음";
      }
    }
  }

  /* Split the board's guest window by what this run actually loaded, so
     the strip shows guests rather than one undivided block. */
  function physicalSegments() {
    const regions = topology?.board?.regions?.pa || [];
    const list = guests().filter((guest) => Number.isFinite(Number(guest.pa)));
    const out = [];
    for (const region of regions) {
      const inside = list
        .filter(
          (guest) =>
            Number(guest.pa) >= region.base &&
            Number(guest.pa) < region.base + region.size,
        )
        .sort((a, b) => Number(a.pa) - Number(b.pa));
      if (region.kind !== "guest" || !inside.length) {
        out.push(region);
        continue;
      }
      let cursor = region.base;
      for (const guest of inside) {
        const base = Number(guest.pa);
        const size = Number(guest.size) || 0;
        if (base > cursor) {
          out.push({ base: cursor, size: base - cursor, kind: "hole", name: "" });
        }
        out.push({ base, size, kind: "guest", name: `vm${guest.slot} ${guest.name || ""}`.trim(), slot: guest.slot });
        cursor = base + size;
      }
      const end = region.base + region.size;
      if (cursor < end) {
        out.push({ base: cursor, size: end - cursor, kind: "hole", name: "게스트 창 여유" });
      }
    }
    return out;
  }

  function strip(parent, label, regions) {
    const row = el("div", "striprow");
    row.append(el("div", "sl", label));
    const bar = el("div", "strip");
    /* One anchor per kind of segment, the first of its kind. A caller
       wanting "where does shared memory sit" gets an answer without
       knowing how many guests split the window this run. */
    const named = new Set();
    for (const region of regions) {
      const seg = el("div", "seg");
      if (region.kind && !named.has(region.kind)) {
        named.add(region.kind);
        anchor(`${label.toLowerCase()}:${region.kind}`, seg);
      }
      if (region.kind === "hole") seg.classList.add("hole");
      if (region.slot !== undefined) {
        seg.classList.add("vm", `v${region.slot % VM_SLOTS}`);
      }
      const caption = region.name || KIND_TEXT[region.kind] || region.kind;
      seg.append(el("span", "sa", hex(region.base)));
      const name = el("span", "sn", caption);
      name.title = `${caption} · ${hex(region.base)} +${bytes(region.size)}`;
      seg.append(name, el("span", "ss", region.sizeText || bytes(region.size)));
      bar.append(seg);
    }
    row.append(bar);
    parent.append(row);
  }

  function buildMemory() {
    const band = layer("MEM", "ADDRESS", "mem-layer");
    const column = el("div", "memcol c12");
    const head = el("div", "striph", "주소 공간");
    head.append(
      el("span", "note", "축척 아님 — 크기는 표기로 읽습니다 · IPA는 모든 게스트가 같은 창을 봅니다"),
    );
    column.append(head);
    band.append(column);
    anchor("mem", column);
    strip(column, "PA", physicalSegments());
    /* The IPA regions already name themselves; what the reader cannot
       see is how each is mapped, so only that is appended. */
    const mapping = { trap: KIND_TEXT.trap, assigned: KIND_TEXT.assigned };
    /* The map states the ABI's minimum guest window. What each guest
       actually got is per run, so the sizes come from the run. */
    const windows = [...new Set(guests().map((guest) => Number(guest.size)).filter(Boolean))]
      .sort((a, b) => a - b)
      .map((size) => bytes(size));
    const ipa = (topology?.board?.regions?.ipa || []).map((region) => ({
      ...region,
      name: mapping[region.kind] ? `${region.name} · ${mapping[region.kind]}` : region.name,
      sizeText: region.kind === "guest" && windows.length ? windows.join(" / ") : undefined,
    }));
    strip(column, "IPA", ipa);
  }

  /* Rebuild only when the machine or the guest set changed; every tick
     after that writes text into the nodes this left behind. */
  function build() {
    clear(bands);
    bands.append(wires);
    live = { anchors: [] };
    focused = null;
    bands.classList.remove("focusing");
    if (!topology?.board) {
      bands.append(el("div", "empty", "보드 정보를 기다리는 중입니다."));
      return;
    }
    buildRoutes();
    buildGuests();
    buildHypervisor();
    buildCores();
    buildInterconnect();
    buildDevices();
    buildMemory();
    /* One wire per vCPU, its lower end assigned by residency. Both ends
       are anchor ids, so the draw path never queries the document. */
    live.links = [];
    for (const entry of live.guests || []) {
      for (const vcpu of entry.vcpus) {
        live.links.push({ anchor: vcpu.anchor, cpu: null, slot: vcpu.slot, title: "" });
      }
    }
    buildWires();
    buildEdges();
    whereSig = null;
    invalidate();
    watch.disconnect();
    watch.observe(bands);
    if (live.hypBand) watch.observe(live.hypBand);
    for (const entry of live.guests || []) watch.observe(entry.node);
    for (const core of live.cores || []) watch.observe(core.node);
  }

  /* ---------------- measured values ---------------- */

  /* Residency is the one measured value that moves geometry: it decides
     which core each wire lands on. Refreshing it here lets a paint tell
     whether the wires need touching at all, so twenty identical
     scheduler samples a second redraw nothing. */
  function residency() {
    const cpus = value("sched.cpu") || [];
    const next = new Map(); // vcpu slot -> cpu index
    const parts = [];
    cpus.forEach((cpu, index) => {
      const current = present(cpu?.current) ? Number(cpu.current) : null;
      if (current !== null) next.set(current, index);
      parts.push(current === null ? "-" : current);
    });
    where = next;
    const sig = parts.join(",");
    if (sig === whereSig) return false;
    whereSig = sig;
    return true;
  }

  function relink() {
    for (const link of live.links || []) {
      const cpu = where.get(link.slot);
      link.cpu = cpu === undefined ? null : cpu;
      link.title =
        cpu === undefined
          ? ""
          : `s${link.slot} 거주 @ pCPU${cpu} — sched.cpu[${cpu}].current (S ${about("sched.cpu")?.rate}Hz)`;
    }
  }

  function renderRoutes() {
    const cpus = value("sched.cpu") || [];
    (live.routes || []).forEach((entry, index) => {
      const cpu = cpus[index];
      const current = cpu && present(cpu.current) ? Number(cpu.current) : null;
      const online = (value("smp.online") || [])[index];
      entry.route.classList.toggle("idle", current === null);
      put(entry.who, current === null ? "—" : `vm${vmOfSlot(current)}.s${current}`);
      put(
        entry.state,
        current === null
          ? cpu?.idling
            ? "idle — 대기 중"
            : online === false
              ? "offline"
              : "거주 vCPU 없음"
          : "RUN",
      );
      const fp = cpu && present(cpu.fp) ? `FP s${cpu.fp}` : "FP —";
      put(entry.detail, `${fp}${cpu?.fp_trap ? " · trap" : ""}`);
      /* Two independent views of one fact: the scheduler's current
         slot and the one the vGIC switched its state to. They cannot
         legitimately differ, so a difference is the finding. */
      const claimed = (value("vgic.resident") || [])[index];
      const agree = (claimed ?? null) === current;
      entry.disagree.hidden = agree;
      if (!agree) {
        const tip = `스케줄러 ${current === null ? "없음" : `s${current}`}` +
          ` / vGIC ${claimed == null ? "없음" : `s${claimed}`}`;
        put(entry.disagree, "불일치");
        if (entry.disagree.title !== tip) entry.disagree.title = tip;
      }
    });
  }

  function renderGuestMeta() {
    const generation = value("vm.generation") || [];
    for (const entry of live.guests || []) {
      const { guest } = entry;
      const bits = [`vm${guest.slot}`];
      if (present(generation[guest.slot])) bits.push(`gen ${generation[guest.slot]}`);
      if (guest.uart && guest.uart !== "none") bits.push(guest.uart);
      put(entry.meta, bits.join(" · "));
    }
  }

  /* The cell shows the scheduler's own run state and nothing else.
     Falling back to the published power state would substitute a
     different fact under the same word — the Scheduler panel keeps the
     two in separate columns, which is where the comparison belongs. */
  function renderVcpus() {
    const run = value("sched.run") || [];
    const affinity = value("sched.affinity") || [];
    for (const entry of live.guests || []) {
      for (const vcpu of entry.vcpus) {
        const cpu = where.get(vcpu.slot);
        const state = run[vcpu.slot]?.state ?? "—";
        put(vcpu.state, String(state).replace(/^k/, ""));
        const pinned = affinity[vcpu.slot];
        put(
          vcpu.affinity,
          cpu === undefined
            ? present(pinned) && pinned
              ? `affinity 0b${Number(pinned).toString(2)}`
              : ""
            : `@pCPU${cpu}`,
        );
        vcpu.row.classList.toggle("off", cpu === undefined);
      }
    }
  }

  /* One line per vCPU, at most two — see the chip's two prebuilt rows. */
  function trapText() {
    /* The bridge splits the class out of the syndrome, so no bit
       position is written here and no name is invented. */
    const seen = [];
    (value("ctx.syndrome") || []).forEach((entry, slot) => {
      if (entry) seen.push({ slot, ec: entry.ec, far: entry.far });
    });
    if (!seen.length) return { lines: ["트랩 관측 없음"], title: "" };
    const lines = seen
      .slice(0, 2)
      .map((hit) => `s${hit.slot} EC 0x${hit.ec.toString(16)} ${className(hit.ec)}`.trim());
    if (seen.length > 2) lines[1] += ` +${seen.length - 2}`;
    return {
      lines,
      title: seen
        .map((hit) => `s${hit.slot}: EC 0x${hit.ec.toString(16)} FAR ${hit.far}`)
        .join("\n"),
    };
  }

  /* Cell count is geometry, and it settles once: EL2 reads ICH_VTR at
     init and the answer never moves again. Resizing here rather than at
     build time is what lets the row wait for the machine's answer
     instead of guessing one. */
  function fitCells(vcpu, capacity) {
    if (vcpu.cells.length === capacity) return;
    vcpu.waiting.hidden = capacity > 0;
    while (vcpu.cells.length > capacity) vcpu.lrs.removeChild(vcpu.cells.pop());
    while (vcpu.cells.length < capacity) {
      const cell = el("span", "lr", "·");
      vcpu.lrs.append(cell);
      vcpu.cells.push(cell);
    }
    invalidate();
  }

  function renderLrs() {
    const inflight = value("vgic.lr") || [];
    const capacity = Number(value("vgic.capacity")) || 0;
    const resident = value("vgic.resident") || [];
    for (const entry of live.guests || []) {
      for (const vcpu of entry.vcpus) {
        fitCells(vcpu, capacity);
        /* While a vCPU is resident its shadow is whatever EL2 last
           wrote: sync_resident_lrs() reads the hardware back only on
           the next entry. Marking the row is the honest alternative to
           showing a stale value as if it were current. */
        vcpu.lrs.classList.toggle("stale", resident.includes(vcpu.slot));
        const held = inflight[vcpu.slot] || [];
        const bySlot = new Map(held.map((entry_) => [entry_.slot, entry_]));
        vcpu.cells.forEach((cell, at) => {
          const carried = bySlot.get(at);
          put(cell, carried ? LR_GLYPH[carried.state] || "?" : "·");
          cell.classList.toggle("held", Boolean(carried));
          /* Where the interrupt came from, when the firmware knows. A
             bound EoI token means real silicon is still waiting to be
             deactivated; no token means the hypervisor made this one up
             — a timer, a doorbell, a vSGI — and there is no physical
             number to show. Inventing one would be the easy lie. */
          const origin = carried?.pintid === undefined
            ? "하이퍼바이저 생성 · 물리 대응 없음"
            : `물리 SPI ${carried.pintid} · gen ${carried.generation}`;
          const tip = carried
            ? `LR${at} vINTID ${carried.vintid} · ${carried.state} · prio ${carried.prio}` +
              ` · ${carried.group1 ? "Group1" : "Group0"}${carried.eoi ? " · EoI 유지보수" : ""}` +
              ` · ${origin}`
            : `LR${at} 비어 있음`;
          if (cell.title !== tip) cell.title = tip;
        });
      }
    }
  }

  function renderVgic() {
    const capacity = Number(value("vgic.capacity")) || 0;
    const carried = (value("vgic.lr") || []).reduce((sum, list) => sum + list.length, 0);
    const dist = value("vgic.dist") || [];
    const pending = dist
      .map((vm, index) => ({ index, bits: Number.parseInt(String(vm?.spi_pending ?? "0"), 16) }))
      .filter((vm) => vm.bits)
      .map((vm) => `vm${vm.index} SPI 0b${vm.bits.toString(2)}`);
    /* Tracked SPIs a device has posted that no register has taken yet.
       refill() moves the token out, so this and the in-flight count
       never double-count the same interrupt: together they read as one
       journey, distributor then register. */
    const posted = (value("vgic.token") || []).reduce((sum, list) => sum + list.length, 0);
    put(
      live.chips.vgic,
      capacity
        ? [
            `LR ${carried}/${capacity}`,
            ...(posted ? [`posted ${posted}`] : []),
            ...(pending.length ? pending : ["SPI pending 없음"]),
          ].join(" · ")
        : "vGIC 관측 대기",
    );
  }

  function renderTrap() {
    const { lines, title } = trapText();
    const [first, brk, second] = live.trapLines;
    const more = lines.length > 1;
    put(first, lines[0] || "");
    if (more) put(second, lines[1]);
    brk.hidden = !more;
    second.hidden = !more;
    const tip = title
      ? `${title}\n\n각 vCPU의 마지막 트랩만 래치됩니다 — 빈도가 아닙니다.`
      : "";
    const chip = live.chips.trap.parentElement;
    if (chip.title !== tip) chip.title = tip;
  }

  function renderSched() {
    const slice = value("sched.slice");
    put(live.chips.sched, present(slice) ? `RR · slice ${slice} ticks` : "RR");
  }

  function renderTimer() {
    const queues = value("timer.queue") || [];
    /* Only armed slots travel, so the size of the table they sit in
       comes from the slot list the topology already publishes. */
    const total = (topology?.timer_slots || []).length;
    /* Per core, named: joined by a slash the two counts read as one
       fraction of the slot table, which is not what they are. */
    const armed = queues.map((slots, cpu) => `cpu${cpu} ${(slots || []).length}`);
    put(
      live.chips.timer,
      armed.length ? `${armed.join(" · ")} / ${total} armed` : "타이머 관측 없음",
    );
  }

  function renderVuart() {
    const uarts = value("dev.uart") || [];
    const busy = uarts
      .map((uart, vm) => ({ vm, count: Number(uart?.count) || 0 }))
      .filter((entry) => entry.count > 0);
    put(
      live.chips.vuart,
      busy.length ? busy.map((entry) => `vm${entry.vm} FIFO ${entry.count}`).join(" · ") : "FIFO 비어 있음",
    );
  }

  function renderIvc() {
    const page = value("ivc.page");
    if (!page) {
      put(live.chips.ivc, "IVC 관측 없음");
      return;
    }
    const rings = Object.entries(page).map(([name, ring]) => {
      const width = ring.slots?.length || 0;
      const used = (parseInt(ring.widx, 16) - parseInt(ring.ridx, 16)) >>> 0;
      return `${name} ${Math.min(used, width)}/${width}`;
    });
    put(live.chips.ivc, rings.join(" · "));
  }

  function renderCores() {
    const cpus = value("sched.cpu") || [];
    const online = value("smp.online") || [];
    const programmed = value("timer.programmed") || [];
    (live.cores || []).forEach((entry, index) => {
      const cpu = cpus[index] || {};
      const resident = [...where.entries()].find(([, at]) => at === index);
      put(entry.home, resident ? `vm${vmOfSlot(resident[0])}.s${resident[0]}` : "없음");
      put(
        entry.rest,
        ` · FP ${present(cpu.fp) ? `s${cpu.fp}` : "—"} · idling ${cpu.idling ? "예" : "아니오"}`,
      );
      const deadline = programmed[index];
      put(
        entry.row,
        `${online[index] === false ? "offline" : "online"} · CNTHP ${
          present(deadline) ? hex(deadline) : "—"
        }`,
      );
    });
  }

  function renderDevices() {
    const registry = value("dev.dma");
    const entries = registry?.entries_ || [];
    for (const device of live.devices || []) {
      const found = entries.find(
        (entry) => Number(entry?.device_id) === Number(device.block.device_id),
      );
      device.node.classList.toggle("unused", !found);
      if (!found) {
        put(device.body, "미할당 — 이 데모에 없음");
        continue;
      }
      put(
        device.body,
        `owner vm${found.owner_vm} · ${String(found.state).replace(/^k/, "")} · gen ${found.generation}`,
      );
    }
  }

  /* The sections a snapshot can repaint, and the only place a topic is
     turned into work. Adding a value to the board means adding a
     painter here and naming it in TOPICS — never widening an existing
     one until it redraws the whole machine again. */
  const painters = {
    routes: renderRoutes,
    guests: renderGuestMeta,
    vcpus: renderVcpus,
    trap: renderTrap,
    sched: renderSched,
    timer: renderTimer,
    vuart: renderVuart,
    ivc: renderIvc,
    cores: renderCores,
    lrs: renderLrs,
    vgic: renderVgic,
    devices: renderDevices,
  };

  function paint(sections) {
    if (folded() || !live.routes) return;
    const moved = residency();
    for (const name of sections) painters[name]();
    /* A vCPU that is nowhere loses its wire until the next switch-in. */
    if (moved) {
      relink();
      drawOverlay();
    }
  }

  function paintAll() {
    paint(Object.keys(painters));
  }

  try {
    const saved = Number(localStorage.getItem(SIZE_KEY));
    if (saved > 0) {
      userSized = true;
      setHeight(saved, false);
    }
    if (localStorage.getItem(FOLD_KEY)) setFolded(true, false);
  } catch (error) {
    /* private mode: the board opens at its default size */
  }
  if (!folded() && window.innerHeight < SHORT_WINDOW) setFolded(true, false);

  /* One listener for the whole overlay rather than one per path: a pulse
     drops its own class when it ends, so nothing accumulates. Swapping
     classes mid-flight cancels rather than ends an animation, so this
     never fires for a pulse that has already been replaced. */
  wires.addEventListener("animationend", (event) => {
    if (event.target.classList.contains("edge")) event.target.classList.remove(...PULSE);
  });

  /* Click a block to see only what touches it; click the background or
     press Escape to put everything back. Delegated, so blocks built and
     rebuilt with the skeleton need no listeners of their own. */
  bands.addEventListener("click", (event) => {
    const hit = event.target.closest?.("[data-anchor]");
    const id = hit && !hit.classList.contains("band") ? hit.dataset.anchor : null;
    setFocus(id === focused ? null : id);
  });

  /* Click a path to walk what actually went down it. The board knows
     which path was clicked and nothing about the trace, so it says so
     and lets the strip answer — the recorded order lives there, and a
     second way of reading it here would be a second answer. */
  wires.addEventListener("click", (event) => {
    const id = event.target.dataset?.edge;
    if (!id) return;
    const edge = byId.get(id);
    if (edge) setFocus(edge.from);
    if (onTour) onTour(id);
  });
  view.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && focused) {
      event.stopPropagation();
      setFocus(null);
    }
  });

  return {
    /* Some topics are read for their value, some only as evidence that a
       path was used, and some for both. `smp.mail` paints nothing and
       still has to arrive, or the crosscall never lights. */
    accepts: (topic) => topic in TOPICS || byTopic.has(topic),
    /* A path named from somewhere else — a timeline mark, say. The
       board focuses by anchor, so pointing at the path's source is what
       leaves it lit with everything not touching it dimmed; the focus
       vocabulary stays one thing rather than two. */
    focusPath(id) {
      const edge = byId.get(id);
      setFocus(edge ? edge.from : null);
      return Boolean(edge);
    },
    /* Shown again after the view slot held something else: the body
       had no size, so everything measured at zero — the same state
       unfolding leaves behind. */
    reveal() {
      invalidate();
      paintAll();
      fitHeight();
    },
    note,
    stopped,
    traced,
    apply(frame) {
      if (frame.kind !== "snapshot") return;
      const data = frame.data && typeof frame.data === "object" ? frame.data : null;
      if (!data || data.values === undefined) return;
      latest.set(frame.topic, data.values);
      /* A folded board costs nothing: no paint, layout, wires or
         animation. Unfolding repaints every section from `latest`, so
         skipping the section tracking while hidden loses nothing. */
      if (folded()) return;
      for (const section of TOPICS[frame.topic] || []) dirty.add(section);
      /* Arrival is the delta. The bridge's change gate only sends a
         topic whose value moved, so there is nothing to compare here. */
      if (byTopic.has(frame.topic)) lit.add(frame.topic);
    },
    settle() {
      /* At most one pulse per path per batch, which falls out of the
         batch being the unit: fifty milliseconds of evidence is one
         piece of news. */
      for (const topic of lit) {
        for (const edge of byTopic.get(topic) || []) flash(edge, false);
      }
      lit.clear();
      if (!dirty.size) return;
      const sections = [...dirty];
      dirty.clear();
      try {
        paint(sections);
      } catch (error) {
        /* Values decode straight out of live guest RAM; a shape the
           board cannot walk redraws on the next tick instead of taking
           the whole view down. The wires go rather than stay pointing
           at a residency that was never finished being read. */
        for (const link of live.links || []) showWire(link, false);
      }
    },
    setTopology(topo) {
      topology = topo && typeof topo === "object" ? topo : null;
      const next = JSON.stringify([
        topology?.board?.name,
        topology?.board?.cpus,
        (topology?.guests || []).map((guest, index) => [
          vmSlot(guest, index),
          guest.name,
          guest.vcpus,
          guest.pa,
        ]),
      ]);
      if (next === signature) return;
      signature = next;
      build();
      paintAll();
      fitHeight();
    },
    /* Topics the run had not read yet at the point the reader is looking
       at. Removed rather than blanked: an absent topic is the state this
       board starts every session in and already paints correctly, so
       nothing new has to handle it — and leaving the later value would
       put a reading on screen at a moment the machine had not produced
       it, which the panels beside it are careful not to do.

       The seek republishes every topic it *does* have a reading for, so
       moving the cursor forward brings them straight back. */
    setUnread(topics) {
      for (const topic of topics || []) {
        if (!latest.delete(topic)) continue;
        for (const section of TOPICS[topic] || []) dirty.add(section);
      }
    },
    clearAll() {
      latest.clear();
      dirty.clear();
      lit.clear();
      /* The topology may come back identical, in which case the paths
         are never rebuilt — so what they last saw has to go from here,
         or the new session opens showing the old one's last event. */
      for (const edge of live.edges || []) edge.last = null;
      whereSig = null;
      paintAll();
    },
  };
}

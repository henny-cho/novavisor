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

import { clear, el, vmSlot } from "./format.mjs";

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

/* Address-map segment captions, keyed by the kind the bridge assigns.
   The map states structure; the words for it belong to the UI. */
const KIND_TEXT = {
  el2: "EL2 이미지",
  guest: "게스트 창",
  shared: "공유 페이지",
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

export function createBoard({ view, board, bands, wires, split, foldButton }) {
  const latest = new Map(); // topic -> value
  /* Nodes the tick writes into, filled in while the skeleton is built.
     Rebuilding markup instead would drop hover, selection and focus. */
  let live = {};
  const dirty = new Set(); // section names awaiting a repaint
  /* Measured geometry, kept until something that can move it happens.
     Null means "re-measure on the next draw". */
  let anchors = null;
  let where = new Map(); // vcpu slot -> cpu index it is resident on
  let whereSig = null;
  let topology = null;
  let signature = null;
  let userSized = false;
  let fitting = false;

  const value = (topic) => latest.get(topic);
  const folded = () => view.classList.contains("folded");
  /* How coarse a sample is belongs to the manifest that takes it. */
  const rate = (topic) => topology?.observations?.[topic];
  const sampled = (topic) => {
    const hz = rate(topic);
    return evidence("s", hz ? `S ${hz}Hz` : "S");
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
    drawWires();
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
    anchors = null;
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
    drawWires();
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

  /* The board is measured here and nowhere else, so a snapshot can
     never force a layout. What is read: the grid's own box, the EL2
     band the wires thread through, the gaps between its chips, and one
     endpoint per vCPU row and per core. */
  function measure() {
    const hyp = live.hypBand;
    if (!hyp) return null;
    const base = bands.getBoundingClientRect();
    const band = hyp.getBoundingClientRect();
    const at = (node, bottom) => {
      const box = node.getBoundingClientRect();
      return [box.left - base.left + box.width / 2, (bottom ? box.bottom : box.top) - base.top];
    };
    const gaps = [];
    const kids = [...hyp.children].map((kid) => kid.getBoundingClientRect());
    for (let i = 1; i < kids.length; i += 1) {
      const left = kids[i - 1].right - base.left;
      const right = kids[i].left - base.left;
      if (right - left >= 4) gaps.push([left, right]);
    }
    return {
      width: base.width,
      height: base.height,
      top: band.top - base.top - 3,
      bottom: band.bottom - base.top + 3,
      gaps,
      from: (live.links || []).map((link) => at(link.from, true)),
      cores: (live.cores || []).map((core) => at(core.node, false)),
    };
  }

  /* Route through the widest gap between the EL2 chips rather than over
     them: a straight drop crosses one mid-word. A line already clear of
     them is left alone. */
  function lane(want) {
    let best = null;
    for (const [left, right] of anchors.gaps) {
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

  function drawWires() {
    if (!wires || folded()) return;
    if (!anchors) anchors = measure();
    const links = live.links || [];
    if (!anchors) {
      for (const link of links) showWire(link, false);
      return;
    }
    /* Rewriting the viewBox invalidates the whole overlay even when the
       value is identical, so it is only touched when it moves. */
    const box = `0 0 ${anchors.width} ${anchors.height}`;
    if (wires.getAttribute("viewBox") !== box) wires.setAttribute("viewBox", box);
    const { top, bottom } = anchors;
    links.forEach((link, index) => {
      /* A vCPU that is not resident anywhere has no core to point at,
         and an absent edge is the honest drawing of that. */
      const start = anchors.from[index];
      const end = link.cpu === null ? null : anchors.cores[link.cpu];
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

  /* ---------------- skeleton ---------------- */

  function layer(title, caption, className) {
    const label = el("div", `layer-label${className === "live" ? " live" : ""}`, title);
    label.append(el("small", "", caption));
    const band = el("div", `band${className ? ` ${className}` : ""}`);
    bands.append(label, band);
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
        vcpus.push({ slot, row, dot, state, affinity, lrs, waiting, cells: [] });
      }
      band.append(node);
      live.guests.push({ guest, node, meta, vcpus });
    }
  }

  function chip(band, title) {
    const node = el("div", "chip c2");
    node.append(el("div", "bc", title));
    const body = el("div", "cv");
    node.append(body);
    band.append(node);
    return body;
  }

  function buildHypervisor() {
    const band = layer("EL2", "HYPERVISOR", "hyp-layer");
    live.hypBand = band;
    live.chips = {
      trap: chip(band, "trap_router"),
      sched: chip(band, "scheduler"),
      timer: chip(band, "soft_timer"),
      vgic: chip(band, "vgic"),
      vuart: chip(band, "vuart"),
      ivc: chip(band, "ivc"),
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
      live.cores.push({ node, home, rest, row });
    }
  }

  function staticBlock(band, block, span) {
    const node = el("div", `blk ${span}`);
    const detail = [];
    if (block.base !== undefined) detail.push(hex(block.base));
    if (block.size) detail.push(`+${bytes(block.size)}`);
    blockHead(node, block.label, detail.join(" "), null);
    band.append(node);
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
    for (const region of regions) {
      const seg = el("div", "seg");
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
    live = {};
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
    /* One wire per vCPU, its lower end assigned by residency. The row
       is held here so the draw path never queries the document. */
    live.links = [];
    for (const entry of live.guests || []) {
      for (const vcpu of entry.vcpus) {
        live.links.push({ from: vcpu.row, cpu: null, slot: vcpu.slot, title: "" });
      }
    }
    buildWires();
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
          : `s${link.slot} 거주 @ pCPU${cpu} — sched.cpu[${cpu}].current (S ${rate("sched.cpu")}Hz)`;
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
      /* Two independent views of the same fact: the scheduler's own
         current slot and the one the vGIC switched its state to. They
         cannot legitimately differ, so a difference is the finding —
         the reasserted-INTID loss behind demo 14's flakiness was
         exactly this kind, and nothing on screen would have shown it. */
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
          const tip = carried
            ? `LR${at} vINTID ${carried.vintid} · ${carried.state} · prio ${carried.prio}` +
              ` · ${carried.group1 ? "Group1" : "Group0"}${carried.eoi ? " · EoI 유지보수" : ""}`
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
    put(
      live.chips.vgic,
      capacity
        ? [`LR ${carried}/${capacity}`, ...(pending.length ? pending : ["SPI pending 없음"])].join(
            " · ",
          )
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
      drawWires();
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

  return {
    accepts: (topic) => topic in TOPICS,
    apply(frame) {
      if (frame.kind !== "snapshot") return;
      const data = frame.data && typeof frame.data === "object" ? frame.data : null;
      if (!data || data.values === undefined) return;
      latest.set(frame.topic, data.values);
      /* A folded board costs nothing: no paint, no layout, no wires.
         Unfolding paints every section from `latest`, so nothing is
         lost by not tracking sections while hidden. */
      if (folded()) return;
      for (const section of TOPICS[frame.topic] || []) dirty.add(section);
    },
    settle() {
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
    clearAll() {
      latest.clear();
      dirty.clear();
      whereSig = null;
      paintAll();
    },
  };
}

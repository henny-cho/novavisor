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
/* JSON numbers past 2^53 lost precision anyway; every such value in the
   observed structs is a "none" sentinel (kNoVcpu, kNoOwner). */
const SENTINEL = 9e15;
const MIN_HEIGHT = 200;
/* What the console keeps when the board opens itself: its header, a
   readable number of lines, and the input row. Dragging the split may
   go past this — that is the reader's decision, not the layout's. */
const CONSOLE_FLOOR = 300;
const DRAG_FLOOR = 220;
/* Below this the console has no room left worth sharing. */
const SHORT_WINDOW = 800;

/* S-layer topics the board reads. Each is listed with the rate the
   observation manifest polls it at, because a block that shows a
   sampled value has to be able to say how coarse the sample is. */
const TOPICS = {
  "sched.cpu": 20,
  "sched.slots": 20,
  "sched.run": 20,
  "sched.affinity": 2,
  "sched.valid": 2,
  "sched.slice": 10,
  "timer.queue": 10,
  "timer.programmed": 10,
  "vm.generation": 2,
  "ctx.trap": 2,
  "dev.uart": 5,
  "dev.dma": 5,
  "ivc.page": 10,
  "smp.online": 2,
};

/* Exception classes worth naming on sight; anything else shows its raw
   EC, which is still the truth and still searchable. */
const EC_NAMES = {
  0x01: "WFx",
  0x07: "SIMD trap",
  0x16: "HVC64",
  0x17: "SMC64",
  0x18: "MSR/MRS",
  0x20: "IABT",
  0x24: "DABT",
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

const present = (value) => value !== undefined && value !== null && value < SENTINEL;

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
  let dirty = false;
  let topology = null;
  let signature = null;
  let userSized = false;
  let fitting = false;

  const value = (topic) => latest.get(topic);
  const folded = () => view.classList.contains("folded");

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
      render();
      fitHeight();
    }
  }

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

  /* Route through the widest gap between blocks rather than over them:
     a straight drop crosses the EL2 chips mid-word. A line already
     clear of them is left alone. */
  function channel(band, base, want) {
    const kids = [...band.children].map((kid) => kid.getBoundingClientRect());
    let best = null;
    for (let i = 1; i < kids.length; i += 1) {
      const left = kids[i - 1].right - base.left;
      const right = kids[i].left - base.left;
      if (right - left < 4) continue;
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

  function drawWires() {
    if (!wires || folded()) return;
    clear(wires);
    const links = live.links || [];
    const hyp = live.hypBand;
    if (!links.length || !hyp) return;
    const base = bands.getBoundingClientRect();
    wires.setAttribute("viewBox", `0 0 ${base.width} ${base.height}`);
    const band = hyp.getBoundingClientRect();
    for (const link of links) {
      /* A vCPU that is not resident anywhere has no core to point at,
         and an absent edge is the honest drawing of that. */
      if (!link.to) continue;
      const from = link.from.getBoundingClientRect();
      const to = link.to.getBoundingClientRect();
      const x1 = from.left - base.left + from.width / 2;
      const y1 = from.bottom - base.top;
      const x2 = to.left - base.left + to.width / 2;
      const y2 = to.top - base.top;
      /* Right angles with filleted corners, laid in the gaps between
         bands: a diagonal jog reads as a stray mark and a sharp corner
         reads as a bracket, but a fillet reads as wiring. */
      const top = band.top - base.top - 3;
      const bottom = band.bottom - base.top + 3;
      const lane = channel(hyp, base, (x1 + x2) / 2);
      const r = 6;
      const bend = (x, y, target, dir) =>
        `Q${x},${y} ${x + dir * r},${y} H${target - dir * r} Q${target},${y} ${target},${y + r}`;
      const d =
        Math.abs(lane - x1) < 2 * r || Math.abs(x2 - lane) < 2 * r
          ? `M${x1},${y1} V${y2}`
          : `M${x1},${y1} V${top - r} ${bend(x1, top, lane, Math.sign(lane - x1))}` +
            ` V${bottom - r} ${bend(lane, bottom, x2, Math.sign(x2 - lane))} V${y2}`;

      /* A casing in the background colour first, so the line stays
         legible where it passes over a block. */
      const casing = document.createElementNS(NS, "path");
      casing.setAttribute("d", d);
      stroke(casing, "var(--bg)", false);
      wires.append(casing);

      const path = document.createElementNS(NS, "path");
      path.setAttribute("d", d);
      stroke(path, "var(--hyp)", true);
      const tip = document.createElementNS(NS, "title");
      tip.textContent = link.title;
      path.append(tip);
      wires.append(path);

      for (const [cx, cy] of [[x1, y1], [x2, y2]]) {
        const dot = document.createElementNS(NS, "circle");
        dot.setAttribute("cx", cx);
        dot.setAttribute("cy", cy);
        dot.setAttribute("r", "2.4");
        dot.setAttribute("fill", "var(--hyp)");
        wires.append(dot);
      }
    }
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
      route.append(left, flow, right);
      band.append(route);
      live.routes.push({ route, who, state, detail });
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
      const meta = blockHead(node, `VM${guest.slot} ${name}`, "", evidence("s", "S 2Hz"));
      const placement = el("div", "bv");
      node.append(placement);
      const vcpus = [];
      const count = Math.max(1, Number(guest.vcpus) || 1);
      for (let index = 0; index < count; index += 1) {
        const slot = slotOf(guest.slot, index);
        const row = el("div", "vcpu");
        const dot = el("i", "dot");
        const state = el("span", "", "—");
        const affinity = el("span", "vaf", "");
        const lrs = el("span", "lrs");
        lrs.append(el("span", "ll", "LR"));
        for (let cell = 0; cell < 4; cell += 1) lrs.append(el("span", "lr"));
        /* The LR shadow is an EL2 global nobody observes yet, and the
           gdb stub carries no ICH_* registers, so there is no second
           route to it either. Saying so beats four empty cells. */
        const pending = el("span", "pending", "미관측");
        pending.title = "vGIC LR 섀도는 아직 관측 대상이 아닙니다";
        lrs.append(pending);
        row.append(el("span", "vn", `s${slot}`), dot, state, affinity, lrs);
        node.append(row);
        vcpus.push({ slot, row, dot, state, affinity });
      }
      band.append(node);
      live.guests.push({ guest, node, meta, placement, vcpus });
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
  }

  function buildCores() {
    const band = layer("PE", "PHYSICAL", "pe-layer");
    const span = cpuCount() <= 2 ? "c6" : "c3";
    live.cores = [];
    for (let cpu = 0; cpu < cpuCount(); cpu += 1) {
      const node = el("div", `blk ${span}`);
      blockHead(node, `pCPU${cpu}`, topology?.board?.cpu || "", evidence("s", "S 20Hz"));
      const body = el("div", "bv");
      const row = el("div", "perow");
      node.append(body, row);
      band.append(node);
      live.cores.push({ node, body, row });
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
    /* Anchor pairs for the residency wires, resolved once here so the
       draw path never queries the document. */
    live.links = [];
    for (const entry of live.guests || []) {
      for (const vcpu of entry.vcpus) {
        live.links.push({ from: vcpu.row, to: null, slot: vcpu.slot, title: "" });
      }
    }
  }

  /* ---------------- measured values ---------------- */

  function residency() {
    const cpus = value("sched.cpu") || [];
    const map = new Map(); // vcpu slot -> cpu index
    cpus.forEach((cpu, index) => {
      if (present(cpu?.current)) map.set(Number(cpu.current), index);
    });
    return map;
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
    });
  }

  function renderGuests(where) {
    const generation = value("vm.generation") || [];
    const run = value("sched.run") || [];
    const power = value("sched.slots") || [];
    const affinity = value("sched.affinity") || [];
    for (const entry of live.guests || []) {
      const { guest } = entry;
      const bits = [`vm${guest.slot}`];
      if (present(generation[guest.slot])) bits.push(`gen ${generation[guest.slot]}`);
      if (guest.uart && guest.uart !== "none") bits.push(guest.uart);
      put(entry.meta, bits.join(" · "));
      clear(entry.placement);
      if (Number.isFinite(Number(guest.ipa))) {
        entry.placement.append(
          document.createTextNode("IPA "),
          el("em", "", hex(guest.ipa)),
          document.createTextNode(` +${bytes(guest.size)} → PA `),
          el("em", "", hex(guest.pa)),
        );
      } else {
        entry.placement.textContent = "창 정보 없음";
      }
      for (const vcpu of entry.vcpus) {
        const cpu = where.get(vcpu.slot);
        const state = run[vcpu.slot]?.state ?? power[vcpu.slot] ?? "—";
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

  /* One line per vCPU, at most two: the chip is a sixth of the band and
     a third line makes the whole EL2 row taller than its neighbours. */
  function trapText() {
    const traps = value("ctx.trap") || [];
    const seen = [];
    traps.forEach((entry, slot) => {
      const esr = entry?.ctx?.esr;
      if (!esr) return;
      const raw = Number.parseInt(String(esr), 16);
      if (!raw) return;
      const ec = (raw >>> 26) & 0x3f;
      seen.push({ slot, ec, far: entry.ctx.far });
    });
    if (!seen.length) return { lines: ["트랩 관측 없음"], title: "" };
    const lines = seen
      .slice(0, 2)
      .map((hit) => `s${hit.slot} EC 0x${hit.ec.toString(16)} ${EC_NAMES[hit.ec] || ""}`.trim());
    if (seen.length > 2) lines[1] += ` +${seen.length - 2}`;
    return {
      lines,
      title: seen
        .map((hit) => `s${hit.slot}: EC 0x${hit.ec.toString(16)} FAR ${hit.far}`)
        .join("\n"),
    };
  }

  function renderChips() {
    const chips = live.chips;
    if (!chips) return;

    const trap = trapText();
    clear(chips.trap);
    trap.lines.forEach((line, index) => {
      if (index) chips.trap.append(el("br"));
      chips.trap.append(el("em", "", line));
    });
    chips.trap.parentElement.title = trap.title
      ? `${trap.title}\n\n각 vCPU의 마지막 트랩만 래치됩니다 — 빈도가 아닙니다.`
      : "";

    const slice = value("sched.slice");
    put(chips.sched, present(slice) ? `RR · slice ${slice} ticks` : "RR");

    const queues = value("timer.queue") || [];
    const armed = queues.map((slots) => (slots || []).filter((slot) => slot?.armed).length);
    const total = queues[0]?.length ?? 0;
    put(
      chips.timer,
      armed.length ? `armed ${armed.join(" / ")} of ${total}` : "타이머 관측 없음",
    );

    /* ICH_VTR is not readable from either layer: the S manifest has no
       vGIC entry and the gdb stub's register set has no ICH_*. */
    clear(chips.vgic);
    chips.vgic.append(el("span", "pending", "LR · SPI 미관측"));

    const uarts = value("dev.uart") || [];
    const busy = uarts
      .map((uart, vm) => ({ vm, count: Number(uart?.count) || 0 }))
      .filter((entry) => entry.count > 0);
    put(
      chips.vuart,
      busy.length ? busy.map((entry) => `vm${entry.vm} FIFO ${entry.count}`).join(" · ") : "FIFO 비어 있음",
    );

    const page = value("ivc.page");
    if (page) {
      const rings = Object.entries(page).map(([name, ring]) => {
        const width = ring.slots?.length || 0;
        const used = (parseInt(ring.widx, 16) - parseInt(ring.ridx, 16)) >>> 0;
        return `${name} ${Math.min(used, width)}/${width}`;
      });
      put(chips.ivc, rings.join(" · "));
    } else {
      put(chips.ivc, "IVC 관측 없음");
    }
  }

  function renderCores(where) {
    const cpus = value("sched.cpu") || [];
    const online = value("smp.online") || [];
    const programmed = value("timer.programmed") || [];
    (live.cores || []).forEach((entry, index) => {
      const cpu = cpus[index] || {};
      const resident = [...where.entries()].find(([, at]) => at === index);
      clear(entry.body);
      entry.body.append(
        document.createTextNode("거주 "),
        el("em", "", resident ? `vm${vmOfSlot(resident[0])}.s${resident[0]}` : "없음"),
        document.createTextNode(
          ` · FP ${present(cpu.fp) ? `s${cpu.fp}` : "—"} · idling ${cpu.idling ? "예" : "아니오"}`,
        ),
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

  function render() {
    if (folded() || !live.routes) return;
    const where = residency();
    renderRoutes();
    renderGuests(where);
    renderChips();
    renderCores(where);
    renderDevices();
    /* Residency changes which core each wire points at, and a vCPU that
       is nowhere loses its wire until the next switch-in. */
    for (const link of live.links || []) {
      const cpu = where.get(link.slot);
      link.to = cpu === undefined ? null : live.cores[cpu]?.node;
      link.title =
        cpu === undefined
          ? ""
          : `s${link.slot} 거주 @ pCPU${cpu} — sched.cpu[${cpu}].current (S ${TOPICS["sched.cpu"]}Hz)`;
    }
    drawWires();
  }

  /* Wires and height both depend on the laid-out box, which is only
     right after the browser has reflowed — a resize handler that reads
     it synchronously catches the previous frame's geometry, and
     widening the window never fires a second event to correct it. */
  new ResizeObserver(() => {
    drawWires();
    fitHeight();
  }).observe(bands);

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
      /* A folded board costs nothing: no render, no layout, no wires. */
      if (!folded()) dirty = true;
    },
    settle() {
      if (!dirty) return;
      dirty = false;
      try {
        render();
      } catch (error) {
        /* Values decode straight out of live guest RAM; a shape the
           board cannot walk redraws on the next tick instead of taking
           the whole view down. */
        clear(wires);
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
      render();
      fitHeight();
    },
    clearAll() {
      latest.clear();
      render();
    },
  };
}

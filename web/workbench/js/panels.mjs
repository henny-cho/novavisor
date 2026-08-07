/* Panel drawer: live tables fed by S-layer snapshot topics. Values render
   exactly as decoded (field names come from the firmware's own debug
   info); the only display rule is that a saturated 64-bit sentinel reads
   as "—". Panels re-render from the latest per-topic value, so frame
   order and rate never matter here.

   Panels are independent toggles rather than tabs: the sizes differ by an
   order of magnitude (Sysreg is ten rows and only moves on a pause, the
   context dump is forty), so which ones fit together is the reader's
   call, not a fixed cap. Only visible panels render, and only those a
   changed topic actually feeds. */

import { clear, el, stamp } from "./format.mjs";

function fmt(value) {
  if (typeof value === "boolean") return value ? "●" : "·";
  return String(value ?? "—");
}

function table(headers, rows) {
  const node = el("table", "ptable");
  const head = el("tr");
  for (const header of headers) head.append(el("th", "", header));
  node.append(head);
  for (const cells of rows) {
    const row = el("tr");
    for (const cell of cells) row.append(el("td", "", fmt(cell)));
    node.append(row);
  }
  return node;
}

function section(title) {
  return el("div", "psec-h", title);
}

/* Any decoded value, without knowing what it is. The field names come
   from the firmware's own debug info, so a table of them is already
   readable — what a hand-written panel adds is ordering, units and
   which columns matter, not the ability to show the value at all. */
function generic(held) {
  if (Array.isArray(held)) {
    const rows = held.map((item, index) => [index, item]);
    const shaped = held.find((item) => item && typeof item === "object" && !Array.isArray(item));
    if (!shaped) return table(["#", "value"], rows.map(([at, item]) => [at, format(item)]));
    const columns = [...new Set(held.flatMap((item) => Object.keys(item || {})))];
    return table(
      ["#", ...columns],
      held.map((item, index) => [index, ...columns.map((key) => format(item?.[key]))]),
    );
  }
  if (held && typeof held === "object") {
    return table(["key", "value"], Object.entries(held).map(([key, item]) => [key, format(item)]));
  }
  return el("div", "pnote", fmt(held));
}

/* A nested value has no column of its own; its shape is still the
   truth, so it travels as JSON rather than as "[object Object]". */
function format(item) {
  return item !== null && typeof item === "object" ? JSON.stringify(item) : item;
}

/* Which panels were open, so a reload does not undo the choice. */
const OPEN_KEY = "nv-wb-panels";

export function createPanels({ tabs, host }) {
  const latest = new Map(); // topic -> {value, ts, src}
  const visible = new Set(); // panels switched on; screen order is PANELS order
  const dirty = new Set(); // panels whose topics changed since the last settle
  let timerSlots = [];
  let ctxSlot = 0;

  function restore(known) {
    try {
      const saved = JSON.parse(localStorage.getItem(OPEN_KEY) || "[]");
      const kept = Array.isArray(saved) ? saved.filter((id) => known.includes(id)) : [];
      return kept.length ? kept : ["sched"];
    } catch (error) {
      return ["sched"];
    }
  }

  function remember() {
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify([...visible]));
    } catch (error) {
      /* private mode: the choice simply lasts this session */
    }
  }

  const value = (topic) => latest.get(topic)?.value;

  const PANELS = [
    {
      id: "sched",
      title: "Scheduler",
      topics: ["sched.cpu", "sched.slots", "sched.run", "sched.affinity", "sched.valid", "sched.slice"],
      render(body) {
        const cpus = value("sched.cpu") || [];
        body.append(section("pCPU"));
        body.append(
          table(
            ["cpu", "current", "fp", "fp_trap", "idling"],
            cpus.map((cpu, index) => [index, cpu.current, cpu.fp, cpu.fp_trap, cpu.idling]),
          ),
        );
        const power = value("sched.slots") || [];
        const run = value("sched.run") || [];
        const affinity = value("sched.affinity") || [];
        const valid = value("sched.valid") || [];
        const rows = power.map((state, slot) => [
          slot,
          state,
          run[slot]?.state,
          affinity[slot],
          valid[slot],
        ]);
        body.append(section("vCPU 슬롯"));
        body.append(table(["slot", "power", "run", "aff", "valid"], rows));
        const slice = value("sched.slice");
        if (slice !== undefined) body.append(el("div", "pnote", `slice ticks: ${fmt(slice)}`));
      },
    },
    {
      id: "timer",
      title: "Timer",
      topics: ["timer.queue", "timer.programmed", "timer.cntvoff", "vm.generation"],
      render(body) {
        const queues = value("timer.queue") || [];
        const programmed = value("timer.programmed") || [];
        queues.forEach((slots, cpu) => {
          body.append(section(`cpu${cpu} — programmed ${fmt(programmed[cpu])}`));
          body.append(
            table(
              ["slot", "owner", "deadline"],
              slots.map((slot) => [slot.slot, timerSlots[slot.slot] ?? "?", slot.deadline]),
            ),
          );
          if (!slots.length) body.append(el("div", "pnote", "armed 슬롯 없음"));
        });
        const cntvoff = value("timer.cntvoff") || [];
        const generation = value("vm.generation") || [];
        body.append(section("per-VM"));
        body.append(
          table(
            ["vm", "cntvoff", "generation"],
            cntvoff.map((offset, vm) => [vm, offset, generation[vm]]),
          ),
        );
      },
    },
    {
      id: "ctx",
      title: "Context",
      topics: ["ctx.trap", "ctx.el1", "sched.valid"],
      render(body) {
        const valid = value("sched.valid") || [];
        const traps = value("ctx.trap") || [];
        const banks = value("ctx.el1") || [];
        const picker = el("div", "pslots");
        const count = Math.max(traps.length, banks.length);
        for (let slot = 0; slot < count; slot += 1) {
          const pick = el("button", "pslot", `s${slot}`);
          pick.type = "button";
          if (slot === ctxSlot) pick.classList.add("on");
          if (valid[slot] === false) pick.classList.add("off");
          pick.addEventListener("click", () => {
            ctxSlot = slot;
            render("ctx");
          });
          picker.append(pick);
        }
        body.append(picker);
        const trap = traps[ctxSlot]?.ctx;
        if (trap) {
          const named = trap.x.map((reg, index) => [`x${index}`, reg]);
          named.push(["sp", trap.sp], ["elr", trap.elr], ["spsr", trap.spsr]);
          named.push(["esr", trap.esr], ["far", trap.far]);
          const rows = [];
          for (let at = 0; at < named.length; at += 2) rows.push(named.slice(at, at + 2).flat());
          body.append(section(`s${ctxSlot} TrapContext — 마지막 EL2 진입 시점`));
          body.append(table(["reg", "value", "reg", "value"], rows));
        }
        const bank = banks[ctxSlot]?.el1;
        if (bank) {
          body.append(section(`s${ctxSlot} EL1 뱅크 — 마지막 스위치 아웃 시점`));
          body.append(table(["reg", "value"], Object.entries(bank)));
        }
      },
    },
    {
      id: "ivc",
      title: "IVC",
      topics: ["ivc.page"],
      render(body) {
        const page = value("ivc.page");
        if (!page) return;
        for (const name of Object.keys(page)) {
          const ring = page[name];
          const width = ring.slots.length;
          const used = (parseInt(ring.widx, 16) - parseInt(ring.ridx, 16)) >>> 0;
          const tail = parseInt(ring.ridx, 16) % width;
          body.append(section(`${name} — ${used}/${width} 사용 · widx ${ring.widx} ridx ${ring.ridx}`));
          const strip = el("div", "pcells");
          ring.slots.forEach((slot, index) => {
            const cell = el("div", "pcell");
            if ((index - tail + width) % width < Math.min(used, width)) cell.classList.add("on");
            cell.title = `slot ${index}: ${slot}`;
            strip.append(cell);
          });
          body.append(strip);
        }
      },
    },
    {
      id: "smp",
      title: "PSCI·SMP",
      topics: ["smp.lifecycle", "smp.mode", "smp.online", "smp.mail", "smp.budget"],
      render(body) {
        const life = value("smp.lifecycle") || [];
        const mode = value("smp.mode") || [];
        const budget = value("smp.budget") || [];
        const online = value("smp.online") || [];
        const mail = value("smp.mail") || [];
        const bits = Math.max(online.length, 1);
        body.append(section("VM 라이프사이클"));
        body.append(
          table(
            ["vm", "mode", "epoch", "pending", "retries", "active", "budget"],
            life.map((vm, index) => [
              index,
              mode[index],
              vm.epoch_,
              `0b${(vm.pending_mask_ ?? 0).toString(2).padStart(bits, "0")}`,
              vm.retries_,
              vm.active_,
              budget[index],
            ]),
          ),
        );
        body.append(section("코어"));
        body.append(
          table(
            ["cpu", "online", "mail"],
            online.map((state, cpu) => [cpu, state, mail[cpu]?.count]),
          ),
        );
      },
    },
    {
      id: "dev",
      title: "Devices",
      topics: ["dev.uart", "dev.dma", "dev.watchdog"],
      render(body) {
        const uarts = value("dev.uart") || [];
        body.append(section("vUART FIFO"));
        body.append(
          table(
            ["vm", "count", "head", "imsc"],
            uarts.map((uart, vm) => [vm, uart.count, uart.head, uart.imsc]),
          ),
        );
        const registry = value("dev.dma");
        if (registry) {
          const entries = registry.entries_ || [];
          const known = Number.isInteger(registry.count_)
            ? entries.slice(0, registry.count_)
            : entries.filter((entry) => entry.state !== "kUnavailable");
          body.append(section(`DMA 레지스트리 — ${known.length} 등록`));
          body.append(
            table(
              ["dev", "owner", "state", "gen", "deadline", "blocked"],
              known.map((entry) => [
                entry.device_id,
                entry.owner_vm,
                entry.state,
                entry.generation,
                entry.deadline,
                entry.bus_master_blocked,
              ]),
            ),
          );
        }
        const sequence = value("dev.watchdog") || [];
        body.append(section("워치독 갱신 시퀀스"));
        body.append(table(["vm", "seq"], sequence.map((seq, vm) => [vm, seq])));
      },
    },
    {
      id: "sysreg",
      title: "Sysreg",
      topics: ["sysreg"],
      render(body) {
        const data = latest.get("sysreg")?.value;
        if (!data) return;
        const registers = data.registers || [];
        const cpus = data.cpus || [];
        body.append(section("정지 시점 실측 (H)"));
        body.append(
          table(
            ["reg", ...cpus.map((_, index) => `cpu${index}`)],
            registers.map((name) => [name, ...cpus.map((cpu) => cpu[name] ?? "—")]),
          ),
        );
      },
    },
  ];

  /* Whatever no panel above claims. A new row in the observation
     manifest is then already on screen, and the default is that an
     observation is visible rather than that it needs a panel written
     for it — which is the same choice inverted, and the other way round
     a value can be polled for months with nobody able to see it.

     Anything worth a shape of its own graduates to a panel above and
     leaves here on its own. */
  const FALLBACK = {
    id: "other",
    title: "기타",
    topics: [],
    render(body) {
      for (const topic of FALLBACK.topics) {
        const held = value(topic);
        if (held === undefined) continue;
        body.append(section(topic));
        body.append(generic(held));
      }
    },
  };
  PANELS.push(FALLBACK);

  const interest = new Map(); // topic -> Set(panel ids); sched.valid feeds two panels
  function index() {
    interest.clear();
    for (const panel of PANELS) {
      for (const topic of panel.topics) {
        if (!interest.has(topic)) interest.set(topic, new Set());
        interest.get(topic).add(panel.id);
      }
    }
  }
  index();

  /* Per topic, what moved between the last two stops. Cleared when the
     machine resumes: a delta is only true of the pair it came from. */
  const moved = new Map();
  const bodies = new Map();
  for (const panel of PANELS) {
    /* A toggle, not a tab: several panels may be open at once, so the
       control reports aria-pressed and the strip is a plain group. */
    const chip = el("button", "tab");
    chip.type = "button";
    chip.setAttribute("aria-pressed", "false");
    chip.title = `${panel.title} 표시 전환`;
    chip.append(el("span", "tt", panel.title));
    /* How many of this panel's fields moved since the previous stop.
       A stop publishes the whole machine; between two consecutive binds
       three or four values actually changed, and this is what says
       which drawer to open for them. */
    chip.append(el("b", "tmoved", ""));
    chip.addEventListener("click", () => toggle(panel.id));
    /* Nothing is unclaimed until a topology says what is published. */
    chip.hidden = panel === FALLBACK;
    tabs.append(chip);

    const body = el("div", "panel-body");
    body.hidden = true;
    host.append(body);
    bodies.set(panel.id, { panel, tab: chip, body });
  }

  /* Bodies sit in declaration order, so what is on screen always reads
     top-to-bottom in that order however the panels were switched on. */
  const placeholder = el("div", "pnote", "표시할 패널을 위에서 선택하세요");
  host.append(placeholder);

  function sync() {
    for (const [id, entry] of bodies) {
      const on = visible.has(id);
      entry.body.hidden = !on;
      entry.tab.setAttribute("aria-pressed", String(on));
      if (!on) clear(entry.body); /* a hidden panel keeps no stale DOM */
    }
    placeholder.hidden = visible.size > 0;
  }

  function toggle(id) {
    if (visible.has(id)) visible.delete(id);
    else visible.add(id);
    remember();
    sync();
    if (visible.has(id)) render(id);
  }

  /* Leaves in a mask: how many values actually moved. The mask is
     shaped like the value it describes, so this is the same walk a
     renderer does — and the only arithmetic the client needs over it. */
  function movedCount(mask) {
    if (mask === true) return 1;
    if (!mask || typeof mask !== "object") return 0;
    let total = 0;
    for (const key of Object.keys(mask)) total += movedCount(mask[key]);
    return total;
  }

  function markMoved() {
    for (const entry of bodies.values()) {
      let count = 0;
      for (const topic of entry.panel.topics) count += movedCount(moved.get(topic));
      const badge = entry.tab.querySelector(".tmoved");
      if (badge) badge.textContent = count ? String(count) : "";
      entry.tab.classList.toggle("moved", count > 0);
    }
  }

  function render(id) {
    const entry = bodies.get(id);
    if (!entry || entry.body.hidden) return;
    /* The drawer is the scroller and this rebuild empties it, which
       drops the reader's offset — a wide table could never be read to
       its right edge. Restore what they were looking at. */
    const left = host.scrollLeft;
    const top = host.scrollTop;
    clear(entry.body);
    const newest = entry.panel.topics
      .map((topic) => latest.get(topic))
      .filter(Boolean)
      .reduce((a, b) => (a && a.ts > b.ts ? a : b), null);
    /* Stacked panels need to name themselves; the freshness stamp rides
       the same line so a panel costs one header row, not two. */
    const head = el("div", "phead");
    head.append(el("span", "pt", entry.panel.title));
    if (newest) head.append(el("span", "pfresh", `src ${newest.src} · ${stamp(newest.ts, 1)}`));
    entry.body.append(head);
    if (!newest) {
      entry.body.append(el("div", "pnote", "실측 대기 중 — 세션이 실행되면 채워집니다"));
    } else {
      try {
        entry.panel.render(entry.body);
      } catch {
        /* Values decode straight out of live guest RAM; a shape this
           table cannot walk must not take the drawer down with it. */
        entry.body.append(el("div", "pnote", "표시할 수 없는 값 — 다음 갱신에서 다시 그립니다"));
      }
    }
    host.scrollLeft = left;
    host.scrollTop = top;
  }

  function renderAll() {
    for (const id of visible) render(id);
    dirty.clear();
  }

  for (const id of restore(PANELS.map((panel) => panel.id))) visible.add(id);
  sync();
  renderAll();

  return {
    accepts: (topic) => interest.has(topic),
    apply(frame) {
      /* Snapshots only: a future delta would clobber the whole value. */
      if (frame.kind !== "snapshot") return;
      const data = frame.data && typeof frame.data === "object" ? frame.data : null;
      if (!data || data.values === undefined) return;
      latest.set(frame.topic, { value: data.values, ts: frame.ts, src: frame.src });
      /* Absent on the first stop of a run; `false` or `{}` when a stop
         genuinely moved nothing, which is a different answer. */
      if (data.changed !== undefined) moved.set(frame.topic, data.changed);
      /* Coalesced to one render per flush window, and only for the
         panels this topic actually feeds: six topics at 20 Hz would
         otherwise rebuild the same table over a hundred times a second,
         throwing away hover, text selection and the slot picker each
         time. Every panel still draws the newest value. */
      for (const id of interest.get(frame.topic) ?? []) {
        if (visible.has(id)) dirty.add(id);
      }
    },
    settle() {
      markMoved();
      if (!dirty.size) return;
      for (const id of dirty) render(id);
      dirty.clear();
    },
    /* A delta belongs to the pair of stops it was measured across, so
       resuming retires it rather than leaving a stale count on a tab. */
    clearMoved() {
      moved.clear();
      markMoved();
    },
    setTopology(topo) {
      timerSlots = Array.isArray(topo.timer_slots) ? topo.timer_slots : [];
      /* The manifest states what is published; everything a panel above
         does not claim falls to the fallback. */
      const claimed = new Set(
        PANELS.filter((panel) => panel !== FALLBACK).flatMap((panel) => panel.topics),
      );
      FALLBACK.topics = Object.keys(topo.observations || {})
        .filter((topic) => !claimed.has(topic))
        .sort();
      index();
      bodies.get(FALLBACK.id).tab.hidden = FALLBACK.topics.length === 0;
      renderAll(); /* owner labels may resolve without a new frame */
    },
    clearAll() {
      latest.clear();
      moved.clear();
      ctxSlot = 0; /* the new run may not have the old slot */
      renderAll();
    },
  };
}

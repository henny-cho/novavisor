/* Panel drawer: live tables fed by S-layer snapshot topics. Values render
   exactly as decoded (field names come from the firmware's own debug
   info); the only display rule is that a saturated 64-bit sentinel reads
   as "—". Panels re-render from the latest per-topic value, so frame
   order and rate never matter here. */

import { clear, el, stamp } from "./format.mjs";

/* JSON numbers past 2^53 lost precision anyway; every such value in the
   observed structs is a "none" sentinel (kNoVcpu, kNoOwner, kNoDeadline). */
const SENTINEL = 9e15;

function fmt(value) {
  if (typeof value === "boolean") return value ? "●" : "·";
  if (typeof value === "number") return value >= SENTINEL ? "—" : String(value);
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

export function createPanels({ tabs, host }) {
  const latest = new Map(); // topic -> {value, ts, src}
  let timerSlots = [];
  let active = "sched";
  let ctxSlot = 0;

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
          const armed = slots
            .map((slot, index) => ({ ...slot, index }))
            .filter((slot) => slot.armed);
          body.append(
            table(
              ["slot", "owner", "deadline"],
              armed.map((slot) => [slot.index, timerSlots[slot.index] ?? "?", slot.deadline]),
            ),
          );
          if (!armed.length) body.append(el("div", "pnote", "armed 슬롯 없음"));
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

  const interest = new Map(); // topic -> Set(panel ids); sched.valid feeds two panels
  for (const panel of PANELS) {
    for (const topic of panel.topics) {
      if (!interest.has(topic)) interest.set(topic, new Set());
      interest.get(topic).add(panel.id);
    }
  }

  const bodies = new Map();
  for (const panel of PANELS) {
    const tab = el("button", "tab");
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.append(el("span", "tt", panel.title));
    tab.addEventListener("click", () => activate(panel.id));
    tabs.append(tab);

    const body = el("div", "panel-body");
    body.hidden = true;
    host.append(body);
    bodies.set(panel.id, { panel, tab, body, fresh: null });
  }

  function activate(id) {
    active = id;
    for (const [panelId, entry] of bodies) {
      const on = panelId === active;
      entry.body.hidden = !on;
      entry.tab.setAttribute("aria-selected", String(on));
    }
    render(active);
  }

  function render(id) {
    const entry = bodies.get(id);
    if (!entry || entry.body.hidden) return;
    clear(entry.body);
    const newest = entry.panel.topics
      .map((topic) => latest.get(topic))
      .filter(Boolean)
      .reduce((a, b) => (a && a.ts > b.ts ? a : b), null);
    if (!newest) {
      entry.body.append(el("div", "pnote", "실측 대기 중 — 세션이 실행되면 채워집니다"));
      return;
    }
    entry.body.append(el("div", "pfresh", `src ${newest.src} · ${stamp(newest.ts, 1)}`));
    entry.panel.render(entry.body);
  }

  activate(active);

  return {
    accepts: (topic) => interest.has(topic),
    apply(frame) {
      latest.set(frame.topic, { value: frame.data.values, ts: frame.ts, src: frame.src });
      if (interest.get(frame.topic)?.has(active)) render(active);
    },
    setTopology(topo) {
      timerSlots = Array.isArray(topo.timer_slots) ? topo.timer_slots : [];
    },
    clearAll() {
      latest.clear();
      render(active);
    },
    activate,
  };
}

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

function fmt(shown) {
  if (typeof shown === "boolean") return shown ? "●" : "·";
  /* A nested value has no column of its own; its shape is still the
     truth, so it travels as JSON rather than as "[object Object]". */
  if (shown !== null && typeof shown === "object") return JSON.stringify(shown);
  return String(shown ?? "—");
}

/* One cell: what to show, and whether it moved since the previous stop.
   Both, always, because a cell drawn from a bare number has already
   thrown away the thing a stop is for.

   The kit — this, `Cursor`, `plain()` and `table()` — is exported so
   its rules can be exercised directly. The panels below are its only
   callers in the page. */
export class Cell {
  constructor(shown, moved) {
    this.shown = shown;
    this.moved = Boolean(moved);
  }
}

/* A reading and the mask of what moved in it, walked together.
 *
 * The alternative was for each renderer to rebuild the address of its
 * own cell — `sched.cpu[1].current` — and look it up in a list. That
 * puts the mask's grammar in the client a second time, and a cell whose
 * address is spelled wrong is silently never highlighted. Nine
 * renderers is nine chances, and every new panel is another.
 *
 * Here the mask is shaped like the value, so descending the value
 * descends the mask by the same key. The cursor arrives at the cell
 * carrying both; there is nothing to look up and nothing to forget. */
export class Cursor extends Cell {
  constructor(shown, mask) {
    super(shown, mask === true);
    this.mask = mask;
  }

  /* A child by key or index. `true` at a node means the node itself
     changed shape, and everything under it with it. */
  get(key) {
    const inner = this.mask === true ? true : this.mask?.[String(key)];
    return new Cursor(this.shown?.[key], inner);
  }

  /* An array's elements, as cursors. Not a plain map(), because the
     index has to reach the mask as the string key the bridge sent. */
  rows() {
    return Array.isArray(this.shown) ? this.shown.map((_, index) => this.get(index)) : [];
  }

  keys() {
    return this.shown && typeof this.shown === "object" ? Object.keys(this.shown) : [];
  }
}

/* A cell with no provenance, said so out loud: a row number, a label, a
   unit — something computed here rather than read from the machine.
   The point is that `plain()` is a claim a reader can grep for, where a
   bare value in a cell is indistinguishable from a forgotten cursor. */
export const plain = (shown) => new Cell(shown, false);

/* A cell handed to table() with no provenance: an authoring fault,
   distinct from the decode failures that arrive from guest RAM. Its own
   type so the drawer can name it without catching theirs. */
export class BareCell extends TypeError {}

export function table(headers, rows) {
  const node = el("table", "ptable");
  const head = el("tr");
  for (const header of headers) head.append(el("th", "", header));
  node.append(head);
  for (const cells of rows) {
    const row = el("tr");
    for (const cell of cells) {
      /* Refused rather than rendered. A bare value here would draw
         correctly and never highlight, which is the failure this whole
         arrangement exists to make impossible — so it must not be a
         thing that draws correctly. */
      if (!(cell instanceof Cell)) {
        throw new BareCell(`table cell is neither a cursor nor plain(): ${String(cell)}`);
      }
      row.append(el("td", cell.moved ? "moved" : "", fmt(cell.shown)));
    }
    node.append(row);
  }
  return node;
}

/* A section heading. `moved` because a reading is sometimes clearer in
   a heading than in a column, and a value that escapes the table must
   not escape the highlight with it — otherwise the tab's count points
   at a drawer where nothing appears to have changed. */
function section(title, moved = false) {
  return el("div", moved ? "psec-h moved" : "psec-h", title);
}

function note(text, moved = false) {
  return el("div", moved ? "pnote moved" : "pnote", text);
}

/* Any decoded value, without knowing what it is. The field names come
   from the firmware's own debug info, so a table of them is already
   readable — what a hand-written panel adds is ordering, units and
   which columns matter, not the ability to show the value at all. */
function generic(cursor) {
  const held = cursor.shown;
  if (Array.isArray(held)) {
    const rows = cursor.rows();
    const shaped = held.find((item) => item && typeof item === "object" && !Array.isArray(item));
    if (!shaped) return table(["#", "value"], rows.map((row, index) => [plain(index), row]));
    const columns = [...new Set(held.flatMap((item) => Object.keys(item || {})))];
    return table(
      ["#", ...columns],
      rows.map((row, index) => [plain(index), ...columns.map((key) => row.get(key))]),
    );
  }
  if (held && typeof held === "object") {
    return table(
      ["key", "value"],
      cursor.keys().map((key) => [plain(key), cursor.get(key)]),
    );
  }
  return note(fmt(held), cursor.moved);
}

/* Which panels were open, so a reload does not undo the choice. */
const OPEN_KEY = "nv-wb-panels";

export function createPanels({ tabs, host }) {
  const latest = new Map(); // topic -> {value, ts, at, src}
  /* Where a reading sits on the firmware's own clock, and what it is
     placed against.

     The publisher stamps every slot with the counter the trace records
     carry. Arrival time answers a different question — when this process
     got to the reading — and differs by the poll interval and the decode.

     With a mark selected the reference is that mark, so a panel says
     whether what it shows predates the moment the reader clicked. With
     none it is the newest reading held, so a lagging panel says so. */
  let counterHz = 0;
  let reference = null;
  /* Per topic, the mask of what moved between the last two stops.
     Cleared when the machine resumes: a delta is only true of the pair
     it came from. Beside `latest` because the two are read together and
     never apart — that pairing is the whole design here. */
  const moved = new Map();
  /* Topics the run had not read yet at the point the reader is looking
     at. Empty live, where the only point is now. */
  let unread = new Set();
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

  /* The only way into a reading. There is deliberately no accessor
     that hands back a bare value: one existed, every renderer used it,
     and the mask arrived at the panel with nowhere to be applied. */
  const at = (topic) =>
    unread.has(topic)
      ? /* The run had not read this yet at the point the reader is
           looking at. `latest` still holds the later value, so moving
           the cursor forward costs nothing — but that value must not be
           what the panel draws here. */
        new Cursor(undefined, undefined)
      : new Cursor(latest.get(topic)?.value, moved.get(topic));

  const PANELS = [
    {
      id: "sched",
      title: "Scheduler",
      topics: ["sched.cpu", "sched.slots", "sched.run", "sched.affinity", "sched.valid", "sched.slice"],
      render(body) {
        body.append(section("pCPU"));
        body.append(
          table(
            ["cpu", "current", "fp", "fp_trap", "idling"],
            at("sched.cpu")
              .rows()
              .map((cpu, index) => [
                plain(index),
                cpu.get("current"),
                cpu.get("fp"),
                cpu.get("fp_trap"),
                cpu.get("idling"),
              ]),
          ),
        );
        const power = at("sched.slots");
        const run = at("sched.run");
        const affinity = at("sched.affinity");
        const valid = at("sched.valid");
        body.append(section("vCPU 슬롯"));
        body.append(
          table(
            ["slot", "power", "run", "aff", "valid"],
            power
              .rows()
              .map((state, slot) => [
                plain(slot),
                state,
                run.get(slot).get("state"),
                affinity.get(slot),
                valid.get(slot),
              ]),
          ),
        );
        const slice = at("sched.slice");
        if (slice.shown !== undefined) {
          body.append(note(`slice ticks: ${fmt(slice.shown)}`, slice.moved));
        }
      },
    },
    {
      id: "timer",
      title: "Timer",
      topics: ["timer.queue", "timer.programmed", "timer.cntvoff", "vm.generation"],
      render(body) {
        const programmed = at("timer.programmed");
        at("timer.queue")
          .rows()
          .forEach((slots, cpu) => {
            const armed = programmed.get(cpu);
            body.append(section(`cpu${cpu} — programmed ${fmt(armed.shown)}`, armed.moved));
            body.append(
              table(
                ["slot", "owner", "deadline"],
                slots.rows().map((slot) => [
                  slot.get("slot"),
                  /* The manifest's label for that slot, not a reading. */
                  plain(timerSlots[slot.get("slot").shown] ?? "?"),
                  slot.get("deadline"),
                ]),
              ),
            );
            if (!slots.rows().length) body.append(el("div", "pnote", "armed 슬롯 없음"));
          });
        const generation = at("vm.generation");
        body.append(section("per-VM"));
        body.append(
          table(
            ["vm", "cntvoff", "generation"],
            at("timer.cntvoff")
              .rows()
              .map((offset, vm) => [plain(vm), offset, generation.get(vm)]),
          ),
        );
      },
    },
    {
      id: "ctx",
      title: "Context",
      topics: ["ctx.trap", "ctx.el1", "sched.valid"],
      render(body) {
        const valid = at("sched.valid");
        const traps = at("ctx.trap");
        const banks = at("ctx.el1");
        const picker = el("div", "pslots");
        const count = Math.max(traps.rows().length, banks.rows().length);
        for (let slot = 0; slot < count; slot += 1) {
          const pick = el("button", "pslot", `s${slot}`);
          pick.type = "button";
          if (slot === ctxSlot) pick.classList.add("on");
          if (valid.get(slot).shown === false) pick.classList.add("off");
          pick.addEventListener("click", () => {
            ctxSlot = slot;
            render("ctx");
          });
          picker.append(pick);
        }
        body.append(picker);
        const trap = traps.get(ctxSlot).get("ctx");
        if (trap.shown) {
          /* Two register pairs per row, so the dump reads in a column
             rather than forty rows deep. `named` is [label, cursor]. */
          const named = trap
            .get("x")
            .rows()
            .map((reg, index) => [plain(`x${index}`), reg]);
          for (const name of ["sp", "elr", "spsr", "esr", "far"]) {
            named.push([plain(name), trap.get(name)]);
          }
          const rows = [];
          for (let index = 0; index < named.length; index += 2) {
            rows.push(named.slice(index, index + 2).flat());
          }
          body.append(section(`s${ctxSlot} TrapContext — 마지막 EL2 진입 시점`));
          body.append(table(["reg", "value", "reg", "value"], rows));
        }
        const bank = banks.get(ctxSlot).get("el1");
        if (bank.shown) {
          body.append(section(`s${ctxSlot} EL1 뱅크 — 마지막 스위치 아웃 시점`));
          body.append(
            table(
              ["reg", "value"],
              bank.keys().map((name) => [plain(name), bank.get(name)]),
            ),
          );
        }
      },
    },
    {
      id: "ivc",
      title: "IVC",
      topics: ["ivc.page"],
      render(body) {
        const page = at("ivc.page");
        if (!page.shown) return;
        for (const name of page.keys()) {
          const ring = page.get(name);
          const slots = ring.get("slots");
          const width = slots.rows().length;
          const widx = ring.get("widx").shown;
          const ridx = ring.get("ridx").shown;
          const used = (parseInt(widx, 16) - parseInt(ridx, 16)) >>> 0;
          const tail = parseInt(ridx, 16) % width;
          body.append(section(`${name} — ${used}/${width} 사용 · widx ${widx} ridx ${ridx}`));
          /* Not a table: the point of the strip is occupancy at a
             glance, so a cell carries its own class rather than the
             shared `moved` one. Provenance still travels — a slot that
             moved gets the same mark the tables use. */
          const strip = el("div", "pcells");
          slots.rows().forEach((slot, index) => {
            const cell = el("div", "pcell");
            if ((index - tail + width) % width < Math.min(used, width)) cell.classList.add("on");
            if (slot.moved) cell.classList.add("moved");
            cell.title = `slot ${index}: ${slot.shown}`;
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
        const mode = at("smp.mode");
        const budget = at("smp.budget");
        const online = at("smp.online");
        const mail = at("smp.mail");
        const bits = Math.max(online.rows().length, 1);
        body.append(section("VM 라이프사이클"));
        body.append(
          table(
            ["vm", "mode", "epoch", "pending", "retries", "active", "budget"],
            at("smp.lifecycle")
              .rows()
              .map((vm, index) => {
                const pending = vm.get("pending_mask_");
                return [
                  plain(index),
                  mode.get(index),
                  vm.get("epoch_"),
                  /* Rendered as bits, so the cell is the reading in
                     another base rather than a computed one — it keeps
                     the cursor's provenance. */
                  new Cell(
                    `0b${(pending.shown ?? 0).toString(2).padStart(bits, "0")}`,
                    pending.moved,
                  ),
                  vm.get("retries_"),
                  vm.get("active_"),
                  budget.get(index),
                ];
              }),
          ),
        );
        body.append(section("코어"));
        body.append(
          table(
            ["cpu", "online", "mail"],
            online.rows().map((state, cpu) => [plain(cpu), state, mail.get(cpu).get("count")]),
          ),
        );
      },
    },
    {
      id: "dev",
      title: "Devices",
      topics: ["dev.uart", "dev.dma", "dev.watchdog"],
      render(body) {
        body.append(section("vUART FIFO"));
        body.append(
          table(
            ["vm", "count", "head", "imsc"],
            at("dev.uart")
              .rows()
              .map((uart, vm) => [
                plain(vm),
                uart.get("count"),
                uart.get("head"),
                uart.get("imsc"),
              ]),
          ),
        );
        const registry = at("dev.dma");
        if (registry.shown) {
          const entries = registry.get("entries_").rows();
          const count = registry.get("count_").shown;
          const known = Number.isInteger(count)
            ? entries.slice(0, count)
            : entries.filter((entry) => entry.get("state").shown !== "kUnavailable");
          body.append(section(`DMA 레지스트리 — ${known.length} 등록`));
          body.append(
            table(
              ["dev", "owner", "state", "gen", "deadline", "blocked"],
              known.map((entry) => [
                entry.get("device_id"),
                entry.get("owner_vm"),
                entry.get("state"),
                entry.get("generation"),
                entry.get("deadline"),
                entry.get("bus_master_blocked"),
              ]),
            ),
          );
        }
        body.append(section("워치독 갱신 시퀀스"));
        body.append(
          table(
            ["vm", "seq"],
            at("dev.watchdog")
              .rows()
              .map((seq, vm) => [plain(vm), seq]),
          ),
        );
      },
    },
    {
      id: "sysreg",
      title: "Sysreg",
      topics: ["sysreg"],
      render(body) {
        const data = at("sysreg");
        if (!data.shown) return;
        const registers = data.get("registers").rows();
        const cpus = data.get("cpus").rows();
        body.append(section("정지 시점 실측 (H)"));
        body.append(
          table(
            ["reg", ...cpus.map((_, index) => `cpu${index}`)],
            /* The register's name is a label, not a reading: it lights
               up only if the *list* changed, which is not a value the
               machine moved. The readings are the columns beside it. */
            registers.map((name) => [
              plain(name.shown),
              ...cpus.map((cpu) => cpu.get(name.shown)),
            ]),
          ),
        );
      },
    },
  ];

  /* Whatever no panel above claims, so a new row in the observation
     manifest is on screen without a panel being written for it. The
     default is visible rather than hidden; anything worth a shape of
     its own graduates to a panel above and leaves here by itself. */
  const FALLBACK = {
    id: "other",
    title: "기타",
    topics: [],
    render(body) {
      for (const topic of FALLBACK.topics) {
        const held = at(topic);
        if (held.shown === undefined) continue;
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

  const bodies = new Map();
  for (const panel of PANELS) {
    /* A toggle, not a tab: several panels may be open at once, so the
       control reports aria-pressed and the strip is a plain group. */
    const chip = el("button", "tab");
    chip.type = "button";
    chip.setAttribute("aria-pressed", "false");
    chip.title = `${panel.title} 표시 전환`;
    chip.append(el("span", "tt", panel.title));
    /* How many values in this panel's topics moved since the previous
       stop. A stop publishes the whole machine; between two consecutive
       binds three or four values actually changed, and this is what
       says which drawer to open for them.

       Counted over the reading, not over what is drawn — those differ
       where a panel shows a subset (the context dump is one slot at a
       time), and the count has to be right for a closed drawer, which
       has drawn nothing at all. */
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

  /* The newest firmware instant the drawer holds, across every topic. */
  function newestInstant() {
    let found = null;
    for (const reading of latest.values()) {
      if (reading.at !== undefined && (found === null || reading.at > found)) found = reading.at;
    }
    return found;
  }

  /* How far a panel's newest reading sits from the reference. Null when
     there is nothing to place it against — no counter rate yet, or a
     provider that stamps nothing — and the header falls back to
     arrival. */
  function placement(topics) {
    if (!counterHz) return null;
    let mine = null;
    for (const topic of topics) {
      const at = latest.get(topic)?.at;
      if (at !== undefined && (mine === null || at > mine)) mine = at;
    }
    const against = reference ?? newestInstant();
    if (mine === null || against === null) return null;
    const micros = ((mine - against) / counterHz) * 1e6;
    const sign = micros >= 0 ? "+" : "-";
    const size = Math.abs(micros);
    const shown = size >= 1000 ? `${(size / 1000).toFixed(1)}ms` : `${Math.round(size)}us`;
    return `${reference === null ? "최신" : "선택"} ${sign}${shown}`;
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
    if (newest) {
      /* The instant the machine took it, where there is one: only that
         places the reading against the events on the strip. */
      const placed = placement(entry.panel.topics);
      head.append(
        el("span", "pfresh", `src ${newest.src} · ${placed ?? stamp(newest.ts, 1)}`),
      );
    }
    entry.body.append(head);
    if (!newest) {
      entry.body.append(el("div", "pnote", "실측 대기 중 — 세션이 실행되면 채워집니다"));
    } else {
      try {
        entry.panel.render(entry.body);
      } catch (error) {
        /* Two different failures, said differently. A bare cell is a
           fault in this file and would otherwise draw correctly while
           never highlighting, so it is named rather than blamed on the
           machine. Everything else is a shape decoded straight out of
           live guest RAM.

           Neither escapes: the scroll restore below and the caller's
           dirty-set clear are what let the next batch draw at all, so a
           throw here would freeze this drawer — and the board view
           after it — for the rest of the session. */
        const note = error instanceof BareCell
          ? "이 표는 값의 출처를 잃었다 — 패널 코드의 결함이다"
          : "표시할 수 없는 값 — 다음 갱신에서 다시 그립니다";
        entry.body.append(el("div", "pnote", note));
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
      latest.set(frame.topic, {
        value: data.values,
        ts: frame.ts,
        /* The publisher's counter for this slot. Absent from a
           provider with no publisher behind it, and absent is not
           zero. */
        at: typeof data.ts === "number" ? data.ts : undefined,
        src: frame.src,
      });
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
    /* Topics with no reading at the cursor's moment. Held rather than
       cleared, so moving the cursor back and forth costs nothing — and
       drawn as "not yet read", because the alternative is leaving a
       later value on screen at a moment the machine had not produced
       it, which is the one thing a cursor exists to prevent. */
    /* The rate the stamps are in, from the trace summary. Without it a
       stamp is a number, not a moment. */
    setClock(hz) {
      if (!hz || hz === counterHz) return;
      counterHz = hz;
      renderAll();
    },
    /* The instant to place readings against. Null puts them back
       against the newest reading held. */
    setReference(at) {
      const next = typeof at === "number" ? at : null;
      if (next === reference) return;
      reference = next;
      renderAll();
    },
    setUnread(topics) {
      unread = new Set(Array.isArray(topics) ? topics : []);
      for (const id of visible) dirty.add(id);
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
      reference = null; /* it named a mark in a run that is over */
      ctxSlot = 0; /* the new run may not have the old slot */
      renderAll();
    },
  };
}

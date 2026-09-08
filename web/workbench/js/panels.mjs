/* Panel drawer: live tables fed by S-layer snapshot topics. Values render
   exactly as decoded (field names come from the firmware's own debug
   info); the only display rule is that a saturated 64-bit sentinel reads
   as "—". Panels re-render from the latest per-topic value, so frame
   order and rate never matter here.

   Which drawer a reading goes in is its own topic name: the manifest
   spells every topic `subsystem.thing`, so the prefix is the drawer and
   a topic the bridge adds arrives in a named drawer with nothing
   written here. A drawer may override the drawing — the seven below
   join several topics into one table — and whatever an override leaves
   follows it as the shape the bridge sent.

   Drawers are independent toggles rather than tabs: the sizes differ by
   an order of magnitude (Sysreg is ten rows and only moves on a pause,
   the context dump is forty), so which ones fit together is the
   reader's call, not a fixed cap. Only visible drawers render, and only
   those a changed topic actually feeds. */

import { clear, ecName, el, elapsed, micros, signed, stamp } from "./format.mjs";
import {
  BareCell,
  Cell,
  Cursor,
  generic,
  note,
  plain,
  read,
  section,
  table,
} from "./primitives/table.mjs";

/* Which drawers were open, so a reload does not undo the choice. */
const OPEN_KEY = "nv-wb-panels";

/* The subsystem a topic belongs to, and so the drawer it is drawn in. */
const drawerOf = (topic) => topic.split(".")[0];

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
  const visible = new Set(); // drawers switched on; screen order is drawer order
  const dirty = new Set(); // drawers whose topics changed since the last settle
  let timerSlots = [];
  let observations = {};
  /* The firmware's own vocabulary, for naming a code the bridge decoded
     out of a register rather than decoding one here. */
  let taxonomy = {};
  let ctxSlot = 0;

  /* The drawers a previous session left open, of those that exist. Read
     at every rebuild rather than once: the derived drawers only exist
     once a topology has arrived, and a choice filtered out before then
     would not survive a reload. Nothing stored at all is a reader who
     has never chosen, and gets the drawer that says whether the machine
     is running at all. */
  function restore() {
    try {
      const saved = JSON.parse(localStorage.getItem(OPEN_KEY) || "null");
      if (!Array.isArray(saved)) return ["sched"];
      return saved.filter((id) => bodies.has(id));
    } catch (error) {
      return ["sched"];
    }
  }

  function remember() {
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify([...visible]));
    } catch {
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
      : new Cursor(latest.get(topic)?.value, moved.get(topic), counterWords(topic));

  /* How a topic's counter values read, in the manifest's own words: a
     stamp is an instant, placed against the same reference the header
     is, and a duration is the length it counts. The raw ticks stay in
     the tooltip, and stay in the cell until the clock's rate is known —
     a division by no frequency is wrong by whatever it turns out to be. */
  function counterWords(topic) {
    const { stamps = [], durations = [] } = observations[topic] || {};
    if (!stamps.length && !durations.length) return null;
    const against = reference ?? newestInstant();
    return (key, shown) => {
      const instant = stamps.includes(key);
      if (typeof shown !== "number" || !(instant || durations.includes(key))) return null;
      /* Nothing has been at a stamp of zero, and no stamp can be placed
         at all until some reading carries an instant of its own. */
      if (instant && (!shown || against === null)) return null;
      const us = micros(instant ? shown - against : shown, counterHz);
      if (us === null) return null;
      return { text: instant ? signed(us) : elapsed(us), title: `${shown}틱` };
    };
  }

  /* Drawings a drawer does itself, keyed by the drawer they belong to.
     Each is a join — the Scheduler cross-joins four topics into one
     table — which is why the unit here is the drawer and not the topic.

     `draws` are the topics the body renders itself, so the rest of the
     drawer's bundle follows as generic tables; `reads` are topics from
     other drawers it consults, and they have to reach the interest
     index or the drawer sits still on the frame that moved them. */
  const OVERRIDES = [
    {
      id: "sched",
      title: "Scheduler",
      draws: ["sched.cpu", "sched.slots", "sched.run", "sched.affinity", "sched.valid", "sched.slice"],
      render(body) {
        body.append(section("pCPU"));
        body.append(
          table(
            /* `since` is when `current` became resident, so the pair
               reads as which vCPU is on this core and for how long. */
            ["cpu", "current", "since", "fp", "fp_trap", "idling"],
            at("sched.cpu")
              .rows()
              .map((cpu, index) => [
                plain(index),
                cpu.get("current"),
                cpu.get("since"),
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
          body.append(note(`slice: ${read(slice)}`, slice.moved, slice.title));
        }
      },
    },
    {
      id: "timer",
      title: "Timer",
      draws: ["timer.queue", "timer.programmed", "timer.cntvoff"],
      reads: ["vm.generation"],
      render(body) {
        const programmed = at("timer.programmed");
        at("timer.queue")
          .rows()
          .forEach((slots, cpu) => {
            const armed = programmed.get(cpu);
            body.append(
              section(`cpu${cpu} — programmed ${read(armed)}`, armed.moved, armed.title),
            );
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
      draws: ["ctx.trap", "ctx.el1", "ctx.syndrome"],
      reads: ["sched.valid"],
      render(body) {
        const valid = at("sched.valid");
        const traps = at("ctx.trap");
        const banks = at("ctx.el1");
        const hits = at("ctx.syndrome");
        /* Both shadow registers that live in hardware, so the heading
           carries when this slot's copy last became true rather than a
           sentence about when that usually happens. */
        const aged = (topic) => {
          const age = shadowAge(topic, ctxSlot);
          return age ? ` — ${age}` : "";
        };
        const picker = el("div", "pslots");
        /* Every reading of a slot offers it: the frame is polled at a
           tenth the rate of the syndrome, so a picker built from the
           frame alone would have no slots to pick early in a run. */
        const count = Math.max(traps.rows().length, banks.rows().length, hits.rows().length);
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
          body.append(section(`s${ctxSlot} TrapContext${aged("ctx.trap")}`));
          body.append(table(["reg", "value", "reg", "value"], rows));
        }
        /* Why the trap was taken, as one line: the bridge split the
           syndrome into its words and the topology names the class, so
           nothing here reads a bit of it. */
        const hit = hits.get(ctxSlot);
        if (hit.shown) {
          const ec = hit.get("ec").shown;
          const said = [
            `EC 0x${ec.toString(16)} ${ecName(taxonomy, ec)}`.trim(),
            `IL ${read(hit.get("il"))}`,
            `ISS ${read(hit.get("iss"))}`,
            `FAR ${read(hit.get("far"))}`,
            `ELR ${read(hit.get("elr"))}`,
          ].join(" · ");
          body.append(note(said, hit.moved));
        }
        const bank = banks.get(ctxSlot).get("el1");
        if (bank.shown) {
          body.append(section(`s${ctxSlot} EL1 뱅크${aged("ctx.el1")}`));
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
      draws: ["ivc.page"],
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
      draws: ["smp.lifecycle", "smp.mode", "smp.online", "smp.mail", "smp.budget"],
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
      draws: ["dev.uart", "dev.dma", "dev.watchdog"],
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
      draws: ["sysreg"],
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

  const drawers = new Map(); // id -> {title, override, watch, rest}
  const interest = new Map(); // topic -> Set(drawer ids); sched.valid feeds two
  const bodies = new Map(); // id -> {tab, body}, in screen order

  /* The drawers, from the manifest's own topic names. An override keeps
     its title and its place; every other prefix published gets a drawer
     named after itself. */
  function project() {
    const topics = Object.keys(observations);
    /* A topic that dates another one is the header's own ±Δ, so it is
       drawn as no row anywhere — it still feeds that age and the
       placement, which is why it stays in the interest index. */
    const dating = new Set(topics.map((topic) => observations[topic]?.as_of).filter(Boolean));
    const written = OVERRIDES.map((override) => override.id);
    const ids = [
      ...written,
      ...[...new Set(topics.map(drawerOf))].filter((id) => !written.includes(id)).sort(),
    ];
    drawers.clear();
    interest.clear();
    for (const id of ids) {
      const override = OVERRIDES.find((entry) => entry.id === id) ?? null;
      const draws = override?.draws ?? [];
      const bundle = topics.filter((topic) => drawerOf(topic) === id);
      /* Redrawn for its own bundle and for whatever an override reads
         elsewhere: Context reads sched.valid and Timer vm.generation,
         and a drawer watching only its prefix would sit still on the
         frame that moved them. The moved badge and the placement read
         this same union — a closed drawer has to count right too. */
      const watch = [...new Set([...bundle, ...draws, ...(override?.reads ?? [])])];
      drawers.set(id, {
        title: override?.title ?? id,
        override,
        watch,
        rest: bundle.filter((topic) => !draws.includes(topic) && !dating.has(topic)),
      });
      for (const topic of watch) {
        if (!interest.has(topic)) interest.set(topic, new Set());
        interest.get(topic).add(id);
      }
    }
    build(ids);
  }

  /* Tabs and bodies, rebuilt only when the set of drawers changes. The
     topology is republished whenever anything on it moves, and
     rebuilding on each would clear what a reader had open. */
  function build(ids) {
    if (ids.join(" ") === [...bodies.keys()].join(" ")) return;
    clear(tabs);
    clear(host);
    bodies.clear();
    for (const id of ids) {
      const { title } = drawers.get(id);
      /* A toggle, not a tab: several drawers may be open at once, so the
         control reports aria-pressed and the strip is a plain group. */
      const chip = el("button", "tab");
      chip.type = "button";
      chip.setAttribute("aria-pressed", "false");
      chip.title = `${title} 표시 전환`;
      chip.append(el("span", "tt", title));
      /* How many values in this drawer's topics moved since the previous
         stop. A stop publishes the whole machine; between two consecutive
         binds three or four values actually changed, and this is what
         says which drawer to open for them.

         Counted over the reading, not over what is drawn — those differ
         where a drawer shows a subset (the context dump is one slot at a
         time), and the count has to be right for a closed drawer, which
         has drawn nothing at all. */
      chip.append(el("b", "tmoved", ""));
      chip.addEventListener("click", () => toggle(id));
      tabs.append(chip);

      const body = el("div", "panel-body");
      body.hidden = true;
      host.append(body);
      bodies.set(id, { tab: chip, body });
    }
    /* Bodies sit in drawer order, so what is on screen always reads
       top-to-bottom in that order however the drawers were switched on. */
    host.append(placeholder);
    /* A drawer the manifest stopped publishing cannot stay open, and
       one it has just named opens if the reader had it open before. */
    for (const id of [...visible]) if (!bodies.has(id)) visible.delete(id);
    for (const id of restore()) visible.add(id);
  }

  const placeholder = el("div", "pnote", "표시할 패널을 위에서 선택하세요");

  function sync() {
    for (const [id, entry] of bodies) {
      const on = visible.has(id);
      entry.body.hidden = !on;
      entry.tab.setAttribute("aria-pressed", String(on));
      if (!on) clear(entry.body); /* a hidden drawer keeps no stale DOM */
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
    for (const [id, nodes] of bodies) {
      let count = 0;
      for (const topic of drawers.get(id).watch) count += movedCount(moved.get(topic));
      const badge = nodes.tab.querySelector(".tmoved");
      if (badge) badge.textContent = count ? String(count) : "";
      nodes.tab.classList.toggle("moved", count > 0);
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

  /* How old a shadow of hardware registers is, against the copy that
     carried it. Which topic dates which is the manifest's to say, so it
     arrives on the topology rather than being spelled here. Both stamps
     are the firmware's counter, so the difference is the machine's own;
     zero means the slot has never held a guest. */
  function shadowAge(topic, slot) {
    const dater = observations[topic]?.as_of;
    if (!dater) return null;
    const held = latest.get(topic);
    const stamps = latest.get(dater)?.value;
    const taken = Array.isArray(stamps) ? Number(stamps[slot]?.synced_at ?? 0) : 0;
    if (!taken) return "관측된 적 없음";
    if (held?.at === undefined) return null;
    const us = micros(held.at - taken, counterHz);
    if (us === null || us < 0) return null; /* the stamp is newer than the copy: mid-turn */
    return `${elapsed(us)} 전`;
  }

  /* How far a drawer's newest reading sits from the reference. Null when
     there is nothing to place it against — no counter rate yet, or a
     provider that stamps nothing — and the header falls back to
     arrival. */
  function placement(topics) {
    let mine = null;
    for (const topic of topics) {
      const at = latest.get(topic)?.at;
      if (at !== undefined && (mine === null || at > mine)) mine = at;
    }
    const against = reference ?? newestInstant();
    if (mine === null || against === null) return null;
    const us = micros(mine - against, counterHz);
    if (us === null) return null;
    return `${reference === null ? "최신" : "선택"} ${signed(us)}`;
  }

  function render(id) {
    const nodes = bodies.get(id);
    if (!nodes || nodes.body.hidden) return;
    const drawer = drawers.get(id);
    /* The drawer is the scroller and this rebuild empties it, which
       drops the reader's offset — a wide table could never be read to
       its right edge. Restore what they were looking at. */
    const left = host.scrollLeft;
    const top = host.scrollTop;
    clear(nodes.body);
    const newest = drawer.watch
      .map((topic) => latest.get(topic))
      .filter(Boolean)
      .reduce((a, b) => (a && a.ts > b.ts ? a : b), null);
    /* Stacked drawers need to name themselves; the freshness stamp rides
       the same line so a drawer costs one header row, not two. */
    const head = el("div", "phead");
    head.append(el("span", "pt", drawer.title));
    if (newest) {
      /* The instant the machine took it, where there is one: only that
         places the reading against the events on the strip. */
      const placed = placement(drawer.watch);
      head.append(
        el("span", "pfresh", `src ${newest.src} · ${placed ?? stamp(newest.ts, 1)}`),
      );
    }
    nodes.body.append(head);
    if (!newest) {
      nodes.body.append(el("div", "pnote", "실측 대기 중 — 세션이 실행되면 채워집니다"));
    } else {
      try {
        drawer.override?.render(nodes.body);
        /* Whatever the override left, as the shape the bridge sent it:
           a topic added to the manifest is drawn here, structured and
           under its subsystem's name, with nothing written for it. */
        for (const topic of drawer.rest) {
          const held = at(topic);
          if (held.shown === undefined) continue;
          nodes.body.append(section(topic));
          nodes.body.append(generic(held));
        }
      } catch (error) {
        /* Nothing escapes: a throw here would leave the dirty set
           uncleared and freeze this drawer for the session. What failed
           is printed rather than guessed at — a bare cell is a fault in
           this file, and anything else is as likely to be one as it is
           to be a shape decoded out of live guest RAM. */
        console.error("panel render failed", id, error);
        const said = error instanceof BareCell
          ? "이 표는 값의 출처를 잃었다 — 패널 코드의 결함이다"
          : `그리지 못했다 — ${error}`;
        nodes.body.append(el("div", "pnote", said));
      }
    }
    host.scrollLeft = left;
    host.scrollTop = top;
  }

  function renderAll() {
    for (const id of visible) render(id);
    dirty.clear();
  }

  project();
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
         drawers this topic actually feeds — its own bundle plus every
         drawer whose override reads it: six topics at 20 Hz would
         otherwise rebuild the same table over a hundred times a second,
         throwing away hover, text selection and the slot picker each
         time. Every drawer still draws the newest value. */
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
      observations = topo.observations || {};
      taxonomy = topo.taxonomy && typeof topo.taxonomy === "object" ? topo.taxonomy : {};
      /* The manifest states what is published, and its topic names say
         which drawer each reading is drawn in. */
      project();
      sync(); /* a rebuilt strip starts with every body hidden */
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

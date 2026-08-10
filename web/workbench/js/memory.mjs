/* Memory Map view: what an address means on this machine, read off the
   page tables the run built.

   Nothing here decodes a descriptor or knows where a level's index sits.
   Rows arrive carrying the span they cover and the permissions they
   grant, because that encoding has one source — the headers the
   hypervisor compiles.

   A row is a run of slots: mapping a region leaves hundreds of entries
   differing only in output address, and the bridge folds them before
   they travel. */

import { clear, el, elapsed, micros } from "./format.mjs";

/* S-layer topics this view reads, declared as the board declares its
   own so a topic the manifest dropped fails a test. The stream table is
   polled rather than read with the tables it points at, because a fault
   quarantines a stream and this is the entry that changes. The sync
   stamps are here because a live answer says how old the root it walked
   from was, and it is dated against the slot's own sync. */
const TOPICS = new Set(["smmu.stream", "ctx.synced"]);

/* A probe lands on exactly one row per level. */
const onPath = (row, steps) =>
  steps.some(
    (step) =>
      step.level === row.level && step.index >= row.index && step.index < row.index + row.count,
  );

const range = (row) => `${row.base} +${row.size}`;

/* Write and execute, the two that differ across this machine's maps and
   the two W^X is about. Read is left out: Stage 2 can withhold it and
   nothing here does, so the column would be one letter throughout. */
const rights = (row) => `${row.w ? "w" : "-"}${row.x ? "x" : "-"}`;

function slot(row) {
  const last = row.index + row.count - 1;
  return `L${row.level}[${row.count === 1 ? row.index : `${row.index}..${last}`}]`;
}

export function createMemory({ pick, form, input, note, body, request }) {
  let regimes = [];
  let chosen = null;
  let shown = null; /* the last answer, for the regime it was asked about */
  let streams = []; /* the stream table as last polled */
  let told = false; /* whether this run has said anything about its regimes */
  const readings = new Map(); /* topic -> values, for the topic a walk is dated by */
  let counterHz = 0;

  function ask(address) {
    if (!chosen) return;
    request({ regime: chosen, address: address ?? "" });
  }

  function choose(id) {
    if (chosen === id) return;
    chosen = id;
    shown = null;
    renderPick();
    clear(body);
    ask(null);
  }

  function renderPick() {
    clear(pick);
    for (const regime of regimes) {
      const button = el("button", "chip", regime.label);
      button.type = "button";
      button.dataset.role = regime.role;
      button.setAttribute("aria-pressed", String(regime.id === chosen));
      button.addEventListener("click", () => choose(regime.id));
      pick.append(button);
    }
  }

  /* One row and everything under it. Depth is the nesting the walk
     produced, not a level number: a walk starting lower still indents
     from where it started. */
  function renderRow(row, depth, steps, into, wxn) {
    const line = el("div", "mrow");
    line.style.setProperty("--depth", String(depth));
    if (onPath(row, steps)) line.classList.add("on");
    line.append(el("span", "mslot", slot(row)));
    line.append(el("span", "mrange", range(row)));
    const arrow = el("span", "marrow", row.kind === "table" ? "↳" : "→");
    line.append(arrow);
    line.append(el("span", "mout", row.output));
    if (row.kind === "table") {
      line.append(el("span", "mkind", "table"));
    } else {
      const perm = el("span", "mperm", rights(row));
      /* Writable and executable at once, where the regime's control
         register forbids that. Marked on the row because that row is
         what a reader needs to see, and left unmarked elsewhere since a
         guest's Stage 2 grants both on purpose. */
      if (wxn && row.w && row.x) perm.classList.add("wx");
      line.append(perm);
      line.append(el("span", "mkind", row.kind));
      line.append(el("span", "mattr", row.memory || ""));
      if (row.af === false) line.append(el("span", "mflag", "AF=0"));
    }
    into.append(line);
    for (const child of row.children || []) renderRow(child, depth + 1, steps, into, wxn);
  }

  /* The first address this regime maps, for the probe box to suggest.
     Typed into the page it would be a guess about a machine the page
     has not looked at. */
  function firstMapped(rows) {
    for (const row of rows) {
      if (row.kind !== "table") return row.base;
      const under = firstMapped(row.children || []);
      if (under) return under;
    }
    return "";
  }

  /* Which streams reach the chosen regime, and which are shut. Joined
     on the root each side already carries, so which VM owns which
     tables has one answer. */
  function renderStreams(regime, into) {
    const root = regime?.root;
    const mine = streams.filter((entry) => entry.root === root);
    const shut = streams.filter((entry) => entry.state === "abort");
    if (!mine.length && !shut.length) return;
    const strip = el("div", "mstreams");
    strip.append(el("span", "mstreams-h", "스트림"));
    for (const entry of mine) {
      strip.append(el("span", "mstream on", `S${entry.stream} · vmid ${entry.vmid}`));
    }
    /* An aborted stream refuses every transaction it makes — what a
       quarantine after a fault leaves behind. */
    for (const entry of shut) strip.append(el("span", "mstream off", `S${entry.stream} 차단`));
    into.append(strip);
  }

  /* A VM's two Stage 2 translations are separate table sets, so the
     reading is their difference: a window only the CPU reaches is memory
     no device can touch, one only DMA reaches is a device able to write
     where the guest cannot look. */
  function renderIsolation(isolation, into) {
    if (!isolation) return;
    const rows = [
      ["cpu_only", "CPU만", "mside"],
      ["dma_only", "DMA만", "mside bad"],
    ];
    for (const [key, caption, className] of rows) {
      const spans = isolation[key] || [];
      if (!spans.length) continue;
      const line = el("div", className);
      line.append(el("span", "mside-h", caption));
      for (const [base, size] of spans) line.append(el("span", "mspan", `${base} +${size}`));
      into.append(line);
    }
  }

  /* The same address in this VM's other translation. One number, two
     answers — which is the comparison. */
  function renderBeside(beside, into) {
    for (const other of beside || []) {
      const probe = other.probe || {};
      const line = el("div", probe.fault ? "mbeside bad" : "mbeside");
      line.append(el("span", "mbeside-h", other.label));
      line.append(
        el(
          "span",
          "",
          probe.fault
            ? `✕ L${probe.level} ${probe.fault}`
            : `→ ${probe.output} ${rights(probe)} ${probe.memory || ""}`,
        ),
      );
      into.append(line);
    }
  }

  /* How old the root a live walk started from was. The answer names
     both the topic that dates it and the slot, so nothing here decides
     either; both stamps are the firmware's counter, so the difference
     is the machine's own. A guest's root is a shadow of a register,
     refreshed when the guest traps, and an answer that showed only
     where it landed would be presenting a copy as the present. */
  function rootAge(rooted) {
    if (!rooted || !counterHz) return null;
    const stamps = readings.get(rooted.as_of);
    const taken = Array.isArray(stamps) ? Number(stamps[rooted.slot]?.synced_at ?? 0) : 0;
    if (!taken) return null;
    const us = micros(rooted.at - taken, counterHz);
    return us === null || us < 0 ? null : elapsed(us);
  }

  function renderRooted(rooted, into) {
    const age = rootAge(rooted);
    if (age === null) return;
    into.append(el("div", "mnote", `\uBFCC\uB9AC\uB294 ${age} \uC804 \uC0AC\uBCF8`));
  }

  /* The hop after this one. A guest's Stage 1 answers an IPA, which is
     the input to the translation beneath it, so the two together are the
     whole address and either alone reads like the whole address. */
  function renderThrough(through, probe, into) {
    if (!through) return;
    const next = through.probe || {};
    const line = el("div", next.fault ? "mbeside bad" : "mbeside");
    line.append(el("span", "mbeside-h", `↳ ${through.label}`));
    line.append(
      el(
        "span",
        "",
        next.fault
          ? `${probe.output} ✕ L${next.level} ${next.fault}`
          : `${probe.output} → ${next.output} ${rights(next)} ${next.memory || ""}`,
      ),
    );
    into.append(line);
  }

  /* A regime whose tables the guest rewrites as it runs. Walked twice:
     the same answer means the chain held still, and a different one means
     the address is moving — which is the reading, not a retry. */
  function renderMoving(moving, into) {
    if (moving === undefined) return;
    into.append(
      el(
        "div",
        moving ? "mwarn" : "mnote",
        moving ? "걷는 동안 테이블이 바뀌었다 — 이 답은 두 순간이 섞였다" : "두 번 걸어 같은 답",
      ),
    );
  }

  function renderAnswer() {
    clear(body);
    if (!shown) return;
    const tree = shown.tree || {};
    const steps = shown.probe?.steps || [];
    const rows = tree.nodes || [];
    const regime = regimes.find((entry) => entry.id === chosen);
    input.placeholder = firstMapped(rows);
    renderRooted(shown.rooted, body);
    renderBeside(shown.beside, body);
    renderThrough(shown.through, shown.probe || {}, body);
    renderMoving(shown.moving, body);
    renderIsolation(shown.isolation, body);
    renderStreams(regime, body);
    if (!rows.length) {
      /* "No mappings" is an answer about the tables; a regime that is
         walked as it is asked has no map to answer it with, and saying
         the first about the second is the kind of claim this whole path
         exists to prevent. */
      body.append(
        el(
          "div",
          "mempty",
          shown.ground === "live"
            ? "이 regime 은 물어본 주소만 답한다 — 지도는 없다"
            : "매핑된 구간이 없다",
        ),
      );
    }
    /* The whole map against the one question this regime's control
       register asks. A count above zero here is a map disagreeing with
       the register it runs under. */
    if (tree.wxn) {
      const verdict = el("div", tree.wx ? "mwarn" : "mverdict");
      verdict.textContent = tree.wx
        ? `SCTLR_EL2.WXN — 그런데 쓰기+실행 구간 ${tree.wx}개`
        : "SCTLR_EL2.WXN — 쓰기와 실행이 겹치는 구간 없음";
      body.append(verdict);
    }
    for (const row of rows) renderRow(row, 0, steps, body, tree.wxn);
    /* A map missing tables is a smaller map and must not read as one
       the machine had. */
    for (const at of tree.unreadable || []) {
      body.append(el("div", "mwarn", `읽지 못한 테이블 ${at}`));
    }
    if (tree.truncated) {
      body.append(el("div", "mwarn", `테이블 ${tree.read}개에서 중단 — 풀보다 깊다`));
    }
  }

  function noteProbe(probe) {
    if (!probe) {
      note.textContent = "";
      note.classList.remove("bad");
      return;
    }
    note.classList.toggle("bad", Boolean(probe.fault));
    if (probe.fault) {
      note.textContent = `${probe.address} ✕ L${probe.level} ${probe.fault}`;
      return;
    }
    const attrs = [rights(probe), probe.memory].filter(Boolean).join(" ");
    note.textContent = `${probe.address} → ${probe.output} · L${probe.level} ${attrs}`;
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    ask(input.value.trim());
  });

  return {
    /* The regimes a run has, from the topology. A run that published
       none leaves the view saying so rather than empty. */
    setWorld(memory) {
      const listed = Array.isArray(memory?.regimes) ? memory.regimes : [];
      /* The first word a run says about its regimes is drawn even when
         it is "none": a client joining before EL2 has built its tables
         would otherwise get an empty view with nothing saying why. */
      const same =
        told &&
        listed.length === regimes.length &&
        listed.every((regime, index) => regime.id === regimes[index].id);
      told = true;
      regimes = listed;
      if (same) return;
      chosen = null;
      shown = null;
      clear(body);
      renderPick();
      note.textContent = listed.length ? "" : "이 실행은 아직 페이지 테이블을 발행하지 않았다";
      if (listed.length) choose(listed[0].id);
    },

    answer(data) {
      if (!chosen || data.regime !== chosen) return;
      shown = data;
      renderAnswer();
      noteProbe(data.probe || null);
    },

    accepts: (topic) => TOPICS.has(topic),

    /* An S-layer reading, in the shape every panel takes one. The map
       does not change under a run; what a stream may do does. */
    apply(frame) {
      if (frame.kind !== "snapshot") return;
      const values = frame.data?.values;
      if (!Array.isArray(values)) return;
      readings.set(frame.topic, values);
      if (frame.topic === "smmu.stream") streams = values;
      if (shown) renderAnswer();
    },

    /* The counter's rate, learned with the first trace window. Until it
       arrives an age is a tick count, which is wrong by whatever the
       clock turns out to be. */
    setClock(hz) {
      counterHz = Number(hz) || 0;
    },

    /* Asked again when the view opens: a run that started since the
       last look has different tables. */
    refresh() {
      if (chosen) ask(input.value.trim() || null);
    },
  };
}

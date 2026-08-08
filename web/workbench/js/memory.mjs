/* Memory Map view: what an address means on this machine, read off the
   page tables the run actually built.

   Nothing here decodes a descriptor or knows where a level's index field
   sits. Rows arrive carrying the span they cover and the permissions
   they grant, because that encoding has one source — the headers the
   hypervisor compiles — and a second reading of it in this file would
   drift the first time a field moved.

   A row is a run of slots, not a slot: mapping a region leaves hundreds
   of entries differing only in output address, and the bridge folds them
   before they travel. What is on screen is what the builder wrote. */

import { clear, el } from "./format.mjs";

/* S-layer topics this view reads, declared as the board declares its
   own: a topic the manifest stopped publishing fails a test instead of
   quietly drawing nothing. The stream table is polled rather than read
   with the tables it points at, because a fault quarantines a stream
   and this is the entry that changes. */
const TOPICS = new Set(["smmu.stream"]);

/* A probe lands on exactly one row per level. */
const onPath = (row, steps) =>
  steps.some(
    (step) =>
      step.level === row.level && step.index >= row.index && step.index < row.index + row.count,
  );

const range = (row) => `${row.base} +${row.size}`;

/* Permissions as the three letters a reader already knows. Read is not
   among them: Stage 2 can withhold it, but nothing this hypervisor maps
   does, and a column that is always the same letter is not information.
   Write and execute are the two that differ, and the two W^X is about. */
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
     produced, not a level number: a walk that started lower would still
     indent from where it started. */
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
         register forbids exactly that. Marked on the row rather than
         only counted, because the row that breaks it is the one a
         reader needs to be looking at — and left unmarked elsewhere,
         since a guest's Stage 2 grants both on purpose. */
      if (wxn && row.w && row.x) perm.classList.add("wx");
      line.append(perm);
      line.append(el("span", "mkind", row.kind));
      line.append(el("span", "mattr", row.memory || ""));
      if (row.af === false) line.append(el("span", "mflag", "AF=0"));
    }
    into.append(line);
    for (const child of row.children || []) renderRow(child, depth + 1, steps, into, wxn);
  }

  /* The first address this regime actually maps, for the probe box to
     suggest. Typed into the page instead, it would be a guess about a
     machine the page has not looked at. */
  function firstMapped(rows) {
    for (const row of rows) {
      if (row.kind !== "table") return row.base;
      const under = firstMapped(row.children || []);
      if (under) return under;
    }
    return "";
  }

  /* Which streams a device master can drive into the chosen regime, and
     which are shut. The join is on the root each side already carries:
     resolving a stream to a VM in the bridge as well would be a second
     answer to one question. */
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
    /* An aborted stream refuses every transaction it makes, which is
       what a quarantine after a fault leaves behind — it belongs beside
       the windows rather than in a panel elsewhere. */
    for (const entry of shut) strip.append(el("span", "mstream off", `S${entry.stream} 차단`));
    into.append(strip);
  }

  /* One VM has two Stage 2 translations, and they are separate table
     sets rather than one with an overlay. So the reading is their
     difference: a window only the CPU reaches is memory no device can
     touch, and one only DMA reaches is a device able to write where the
     guest cannot look. */
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

  function renderAnswer() {
    clear(body);
    if (!shown) return;
    const tree = shown.tree || {};
    const steps = shown.probe?.steps || [];
    const rows = tree.nodes || [];
    const regime = regimes.find((entry) => entry.id === chosen);
    input.placeholder = firstMapped(rows);
    renderBeside(shown.beside, body);
    renderIsolation(shown.isolation, body);
    renderStreams(regime, body);
    if (!rows.length) {
      body.append(el("div", "mempty", "매핑된 구간이 없다"));
    }
    /* The whole map's answer to the one question this regime's control
       register asks. A count that is not zero where the register says it
       must be is a map disagreeing with the register it runs under. */
    if (tree.wxn) {
      const verdict = el("div", tree.wx ? "mwarn" : "mverdict");
      verdict.textContent = tree.wx
        ? `SCTLR_EL2.WXN — 그런데 쓰기+실행 구간 ${tree.wx}개`
        : "SCTLR_EL2.WXN — 쓰기와 실행이 겹치는 구간 없음";
      body.append(verdict);
    }
    for (const row of rows) renderRow(row, 0, steps, body, tree.wxn);
    /* A short walk is a fact about the answer, not a detail: a map
       missing tables is a smaller map, and it must not read as one the
       machine had. */
    for (const at of tree.unreadable || []) {
      body.append(el("div", "mwarn", `읽지 못한 테이블 ${at}`));
    }
    if (tree.truncated) {
      body.append(el("div", "mwarn", `테이블 ${tree.tables}개에서 중단 — 풀보다 깊다`));
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
    /* The regimes a run has, from the topology. A run that has published
       none leaves the view saying so rather than empty. */
    setWorld(memory) {
      const listed = Array.isArray(memory?.regimes) ? memory.regimes : [];
      const same =
        listed.length === regimes.length &&
        listed.every((regime, index) => regime.id === regimes[index].id);
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
       itself never changes under a run; what a stream is allowed to do
       does, and only that is polled. */
    apply(frame) {
      if (frame.kind !== "snapshot") return;
      const values = frame.data?.values;
      if (!Array.isArray(values)) return;
      streams = values;
      if (shown) renderAnswer();
    },

    /* Asked again when the view is opened: a run that started after the
       last look has different tables. */
    refresh() {
      if (chosen) ask(input.value.trim() || null);
    },
  };
}

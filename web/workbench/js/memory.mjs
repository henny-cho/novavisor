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
  function renderRow(row, depth, steps, into) {
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
      /* Writable and executable at once is what W^X forbids. Marked
         here rather than counted elsewhere: the row that breaks it is
         the row a reader needs to be looking at. */
      if (row.w && row.x) perm.classList.add("wx");
      line.append(perm);
      line.append(el("span", "mkind", row.kind));
      line.append(el("span", "mattr", row.memory || ""));
      if (row.af === false) line.append(el("span", "mflag", "AF=0"));
    }
    into.append(line);
    for (const child of row.children || []) renderRow(child, depth + 1, steps, into);
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

  function renderAnswer() {
    clear(body);
    if (!shown) return;
    const tree = shown.tree || {};
    const steps = shown.probe?.steps || [];
    const rows = tree.nodes || [];
    input.placeholder = firstMapped(rows);
    if (!rows.length) {
      body.append(el("div", "mempty", "매핑된 구간이 없다"));
    }
    for (const row of rows) renderRow(row, 0, steps, body);
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

    apply(data) {
      if (!chosen || data.regime !== chosen) return;
      shown = data;
      renderAnswer();
      noteProbe(data.probe || null);
    },

    /* Asked again when the view is opened: a run that started after the
       last look has different tables. */
    refresh() {
      if (chosen) ask(input.value.trim() || null);
    },
  };
}

import { accentOf, clear, el, stamp } from "./format.mjs";
import { StreamLog, stampOf } from "./primitives/stream_log.mjs";

const ROW_CAP = 2000;
/* UI-local chip for lifecycle rows: not part of the bridge taxonomy. */
const LIFE = "LIFE";

export function createEvents({ list, filters, resetButton, clearButton }) {
  /* Badges the user switched off. */
  const muted = new Set();
  /* Badges the board is asking to see, or null for "no narrowing". */
  let narrowed = null;
  const chips = new Map();
  const hidden = (name) => muted.has(name) || (narrowed !== null && !narrowed.has(name));
  const stream = new StreamLog({ container: list, lineCap: ROW_CAP });

  const accent = (name) => (name === LIFE ? "var(--ink3)" : accentOf(name));

  function placeholder() {
    if (list.childElementCount) return;
    list.append(el("div", "empty", "이벤트 없음 — 타깃을 실행하면 채워집니다."));
  }

  /* Whether a row is on screen. One rule, because two — a badge filter
     and a time cursor — would each decide half of it and the second to
     run would undo the first. An unstamped row sits on no run clock, so
     only the filter can hide it. */
  const shows = (row) => {
    const at = stampOf(row);
    return !hidden(row.dataset.badge) && (at === null || at <= (stream.cut ?? Infinity));
  };

  function refresh() {
    for (const row of list.children) {
      if (row.dataset.badge) row.hidden = !shows(row);
    }
    stream.dirty = true;
  }

  /* Show only these badges, or pass null to stop narrowing. What the
     user muted stays muted either way. */
  function narrow(names) {
    narrowed = names === null ? null : new Set(names);
    refresh();
    filters.classList.toggle("narrowed", narrowed !== null);
  }

  /* Everything after `ts` is the future of wherever the reader is
     looking. Hidden, not dropped: the cursor moves both ways.

     Only the rows between the old cut and the new one changed side, and
     the stream walks to them; the badge filter has not moved, so the
     rest already show what `shows` would say. */
  function cutAt(ts) {
    stream.cutTo(ts, (row) => {
      row.hidden = !shows(row);
    });
    stream.dirty = true;
  }

  function makeChip(name) {
    const chip = el("button", "fchip", name);
    chip.type = "button";
    chip.title = `${name} 표시 전환`;
    chip.style.setProperty("--chipc", accent(name));
    chip.setAttribute("aria-pressed", String(!muted.has(name)));
    chip.addEventListener("click", () => {
      if (muted.has(name)) muted.delete(name);
      else muted.add(name);
      chip.setAttribute("aria-pressed", String(!muted.has(name)));
      refresh();
    });
    chips.set(name, chip);
    return chip;
  }

  /* Rebuild the filter row from the snapshot's vocabulary. */
  function setBadges(badges) {
    const names = (Array.isArray(badges) ? badges : []).map(String);
    for (const name of [...muted]) {
      /* Its chip is gone; its rows must not stay hidden. */
      if (name !== LIFE && !names.includes(name)) muted.delete(name);
    }
    clear(filters);
    chips.clear();
    for (const name of names) filters.append(makeChip(name));
    filters.append(makeChip(LIFE));
    if (!names.length) filters.append(el("span", "empty", "배지 정보 대기 중"));
    refresh(); /* a chip that vanished must not leave its rows hidden */
  }

  function addRow({ ts, badge, severity, message, fields, dim, local }) {
    const name = badge ? String(badge) : "?";
    if (list.firstElementChild?.classList.contains("empty")) {
      list.removeChild(list.firstElementChild);
    }
    const row = el("div", dim ? "erow dim" : "erow");
    row.dataset.badge = name;
    if (severity) row.dataset.sev = String(severity);
    row.style.setProperty("--chipc", accent(name));
    row.append(el("span", "et", stamp(ts)));
    row.append(el("span", "eb", name));
    row.append(el("span", "em", message === undefined || message === null ? "" : message));
    if (fields && typeof fields === "object") {
      for (const [key, value] of Object.entries(fields)) {
        row.append(el("span", "ef", `${key}=${value}`));
      }
    }
    /* Stamped by the stream with the moment it describes, so a cursor
       moved into the past does not leave the log showing what the
       machine had not done yet. This session's own remarks go unstamped:
       they are not moments in the run. */
    stream.append(row, local ? undefined : (ts ?? 0));
    row.hidden = !shows(row);
  }

  /* One scroll write per batch; `stick` follows the scroll listener. */
  function settle() {
    stream.settle();
  }


  /* A classified console event straight off the wire. */
  function addEvent(ts, data) {
    addRow({
      ts,
      badge: data.badge,
      severity: data.severity,
      message: data.message,
      fields: data.fields,
    });
  }

  /* A lifecycle or bridge notice, rendered under the UI-local chip. */
  function addNotice(ts, message, options = {}) {
    addRow({
      ts,
      badge: LIFE,
      severity: options.severity || "INFO",
      message,
      fields: options.fields,
      dim: Boolean(options.dim),
      local: true,
    });
  }

  function clearAll() {
    stream.clear();
    placeholder();
  }

  resetButton.addEventListener("click", () => {
    muted.clear();
    for (const chip of chips.values()) chip.setAttribute("aria-pressed", "true");
    refresh();
  });
  clearButton.addEventListener("click", clearAll);

  placeholder();
  return { setBadges, addEvent, addNotice, settle, clearAll, narrow, cutAt };
}

/* Event log: rows the bridge classified, plus lifecycle notices from this
   UI. The badge vocabulary is never known ahead of time — it arrives in
   the topology snapshot and its chip colour is derived from the name, so
   adding a subsystem needs no change here. */

import { accentOf, atBottom, clear, el, stamp, toBottom, trim } from "./format.mjs";

const ROW_CAP = 2000;
/* UI-local chip for lifecycle rows: not part of the bridge taxonomy. */
const LIFE = "LIFE";

export function createEvents({ list, filters, resetButton, clearButton }) {
  /* Badges the user switched off. */
  const muted = new Set();
  const chips = new Map();
  let stick = true;
  let dirty = false;
  list.addEventListener("scroll", () => {
    stick = atBottom(list);
  });

  const accent = (name) => (name === LIFE ? "var(--ink3)" : accentOf(name));

  function placeholder() {
    if (list.childElementCount) return;
    list.append(el("div", "empty", "이벤트 없음 — 타깃을 실행하면 채워집니다."));
  }

  function apply(name) {
    const off = muted.has(name);
    for (const row of list.querySelectorAll(`[data-badge="${CSS.escape(name)}"]`)) row.hidden = off;
  }

  function makeChip(name) {
    const chip = el("button", name === LIFE ? "fchip life" : "fchip", name);
    chip.type = "button";
    chip.style.setProperty("--chipc", accent(name));
    chip.setAttribute("aria-pressed", String(!muted.has(name)));
    chip.title = `${name} 표시 전환`;
    chip.addEventListener("click", () => {
      if (muted.has(name)) muted.delete(name);
      else muted.add(name);
      chip.setAttribute("aria-pressed", String(!muted.has(name)));
      apply(name);
    });
    chips.set(name, chip);
    return chip;
  }

  /* Rebuild the filter row from the snapshot's vocabulary. */
  function setBadges(badges) {
    const names = (Array.isArray(badges) ? badges : []).map(String);
    for (const name of [...muted]) {
      if (name !== LIFE && !names.includes(name)) {
        muted.delete(name);
        apply(name); /* its chip is gone; its rows must not stay hidden */
      }
    }
    clear(filters);
    chips.clear();
    for (const name of names) filters.append(makeChip(name));
    filters.append(makeChip(LIFE));
    if (!names.length) filters.append(el("span", "empty", "배지 정보 대기 중"));
  }

  function addRow({ ts, badge, severity, message, fields, dim }) {
    const name = badge ? String(badge) : "?";
    if (list.firstElementChild && list.firstElementChild.classList.contains("empty")) clear(list);
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
    row.hidden = muted.has(name);
    list.append(row);
    trim(list, ROW_CAP);
    dirty = true;
  }

  /* One scroll write per batch; `stick` follows the scroll listener. */
  function settle() {
    if (!dirty) return;
    dirty = false;
    if (stick) toBottom(list);
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
    });
  }

  function clearAll() {
    clear(list);
    placeholder();
  }

  resetButton.addEventListener("click", () => {
    muted.clear();
    for (const [name, chip] of chips) {
      chip.setAttribute("aria-pressed", "true");
      apply(name);
    }
  });
  clearButton.addEventListener("click", clearAll);

  placeholder();
  return { setBadges, addEvent, addNotice, settle, clearAll };
}

/* Shared table engine: live tables with provenance tracking and mask-based
   moved cell highlighting. Values render exactly as decoded. */

import { el } from "../format.mjs";

function fmt(shown) {
  if (typeof shown === "boolean") return shown ? "●" : "·";
  if (shown !== null && typeof shown === "object") return JSON.stringify(shown);
  return String(shown ?? "—");
}

/* One cell: what to show, whether it moved, and optional explanation hint. */
export class Cell {
  constructor(shown, moved = false, hint = "") {
    this.shown = shown;
    this.moved = Boolean(moved);
    this.hint = hint;
  }
}

/* A reading and the mask of what moved in it, walked together. */
export class Cursor extends Cell {
  constructor(shown, mask, hint = "") {
    super(shown, mask === true, hint);
    this.mask = mask;
  }

  /* A child by key or index. true at a node means the node itself changed shape. */
  get(key) {
    const inner = this.mask === true ? true : this.mask?.[String(key)];
    return new Cursor(this.shown?.[key], inner);
  }

  /* An array's elements, as cursors. */
  rows() {
    return Array.isArray(this.shown) ? this.shown.map((_, index) => this.get(index)) : [];
  }

  keys() {
    return this.shown && typeof this.shown === "object" ? Object.keys(this.shown) : [];
  }
}

/* A cell with no provenance (a label, row index, unit, etc.). */
export const plain = (shown, hint = "") => new Cell(shown, false, hint);

/* A cell handed to table() with no provenance: an authoring fault. */
export class BareCell extends TypeError {}

export function table(headers, rows, options = {}) {
  const node = el("table", options.className || "ptable");
  const head = el("tr");
  for (const header of headers) head.append(el("th", "", header));
  node.append(head);
  for (const cells of rows) {
    const row = el("tr");
    for (const cell of cells) {
      if (!(cell instanceof Cell)) {
        throw new BareCell(`table cell is neither a cursor nor plain(): ${String(cell)}`);
      }
      const td = el("td", cell.moved ? "moved" : "", fmt(cell.shown));
      if (cell.hint) td.title = cell.hint;
      if (options.onCellClick) {
        td.addEventListener("click", (ev) => options.onCellClick(cell, ev));
      }
      row.append(td);
    }
    node.append(row);
  }
  return node;
}

export function section(title, moved = false) {
  return el("div", moved ? "psec-h moved" : "psec-h", title);
}

export function note(text, moved = false, hint = "") {
  const node = el("div", moved ? "pnote moved" : "pnote", text);
  if (hint) node.title = hint;
  return node;
}

/* Generic table renderer for unknown/fallback object structures. */
export function generic(cursor) {
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

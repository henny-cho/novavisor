/* Shared table engine: live tables with provenance tracking and mask-based
   moved cell highlighting. Values render exactly as decoded. */

import { el } from "../format.mjs";

/* How a decoded value reads: a flag as a mark, anything structured as
   its JSON, absent as an em dash. */
function fmt(shown) {
  if (typeof shown === "boolean") return shown ? "●" : "·";
  if (shown !== null && typeof shown === "object") return JSON.stringify(shown);
  return String(shown ?? "—");
}

/* One cell: what to show, and whether it moved. `text` is the words a
   converted value reads as, with `title` keeping the raw reading. */
export class Cell {
  constructor(shown, moved = false) {
    this.shown = shown;
    this.moved = Boolean(moved);
    this.text = null;
    this.title = "";
  }
}

/* What a cell reads as: its own words where it has them, else the value. */
export const read = (cell) => cell.text ?? fmt(cell.shown);

/* A reading and the mask of what moved in it, walked together.

   `unit` is how this topic's numbers read as time — the manifest's own
   words for it, since a counter value carries no unit — asked for by
   the key the walk arrived at. Handed down the walk, so every cell of
   the reading gets it wherever a renderer picks it up. */
export class Cursor extends Cell {
  constructor(shown, mask, unit = null, key = "") {
    super(shown, mask === true);
    this.mask = mask;
    this.unit = unit;
    const said = unit?.(key, shown);
    if (said) {
      this.text = said.text;
      this.title = said.title;
    }
  }

  /* A child by key or index. true at a node means the node itself changed shape. */
  get(key) {
    const inner = this.mask === true ? true : this.mask?.[String(key)];
    /* An element of an array is one of the reading's own values, which
       the manifest names ""; a member of a record is its named field. */
    const named = Array.isArray(this.shown) ? "" : String(key);
    return new Cursor(this.shown?.[key], inner, this.unit, named);
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
export const plain = (shown) => new Cell(shown, false);

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
      const shown = el("td", cell.moved ? "moved" : "", read(cell));
      if (cell.title) shown.title = cell.title;
      row.append(shown);
    }
    node.append(row);
  }
  return node;
}

/* A line of text carrying the provenance mark, and the raw reading
   behind it where the words on screen are a conversion of one. */
const line = (className, text, moved, hint) => {
  const node = el("div", moved ? `${className} moved` : className, text);
  if (hint) node.title = hint;
  return node;
};

export const section = (title, moved = false, hint = "") => line("psec-h", title, moved, hint);

export const note = (text, moved = false, hint = "") => line("pnote", text, moved, hint);

/* Every field name the records in a list carry, in first-seen order. */
const columnsOf = (items) => [
  ...new Set(
    items.flatMap((item) =>
      item && typeof item === "object" && !Array.isArray(item) ? Object.keys(item) : [],
    ),
  ),
];

/* Generic table renderer for a topic no drawer draws itself. */
export function generic(cursor) {
  const held = cursor.shown;
  if (Array.isArray(held)) {
    const rows = cursor.rows();
    /* A list per core or per VM of records — a list register shadow, a
       timer queue — flattened with the outer index as its first column,
       so the whole reading reads as one table instead of a JSON blob
       per core. Elements that are not records have no columns to
       spread, and fall through to the two-column form below. */
    const inner = held.some(Array.isArray) ? columnsOf(held.flat()) : [];
    if (inner.length) {
      const flat = [];
      rows.forEach((row, index) =>
        row.rows().forEach((item) => flat.push([plain(index), ...inner.map((key) => item.get(key))])),
      );
      return table(["#", ...inner], flat);
    }
    const columns = columnsOf(held);
    if (!columns.length) return table(["#", "value"], rows.map((row, index) => [plain(index), row]));
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
  return note(read(cursor), cursor.moved, cursor.title);
}

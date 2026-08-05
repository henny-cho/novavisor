/* Small shared helpers: time on the protocol's axis, safe DOM building.
   Nothing here touches the wire or the layout. */

const NS_PER_SECOND = 1e9;

/* Console lines carry a firmware-tagged VM slot; anything the board
   cannot host is guest text that merely looks like a tag, and must not
   mint tabs or cards. */
export const MAX_VM_SLOT = 8;

/* Protocol timestamps are session-monotonic nanoseconds. */
export function stamp(ns, digits = 3) {
  const seconds = Number(ns) / NS_PER_SECOND;
  if (!Number.isFinite(seconds)) return "—";
  return `${seconds.toFixed(digits)}s`;
}

/* Top-bar session clock: one decimal is enough to read at a glance. */
export function clockLabel(ns) {
  return stamp(ns, 1);
}

/* Element factory: text always lands in textContent, so firmware output
   can never be parsed as markup. */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/* Keep a scrolled-to-bottom log pinned without fighting a reading user. */
export function atBottom(node, slack = 12) {
  return node.scrollHeight - node.scrollTop - node.clientHeight <= slack;
}

export function toBottom(node) {
  node.scrollTop = node.scrollHeight;
}

/* Drop the oldest children once a log exceeds its cap. */
export function trim(node, cap) {
  while (node.childElementCount > cap) node.removeChild(node.firstElementChild);
}

/* A guest's VM id is the firmware slot its console lines are tagged with,
   which is not always the array position: a manifest may name a guest
   after the slot it loads into (a two-guest demo can hold vm0 and vm2).
   The name wins when it states a slot, position otherwise. */
const SLOT_NAME = /^vm(\d+)$/;

export function vmSlot(guest, index) {
  const match = SLOT_NAME.exec(String((guest && guest.name) || ""));
  return match ? Number(match[1]) : index;
}

/* Stable string → index into a small palette (FNV-1a). Chip colours are
   derived, never mapped by name, so a vocabulary change needs no UI edit. */
export function paletteIndex(name, size) {
  if (!size) return 0;
  let hash = 0x811c9dc5;
  const text = String(name);
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash % size;
}

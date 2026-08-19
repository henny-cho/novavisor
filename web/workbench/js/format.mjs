/* Small shared helpers: time on the protocol's axis, safe DOM building.
   Nothing here touches the wire or the layout. */

const NS_PER_SECOND = 1e9;

/* Console lines carry a firmware-tagged VM slot; anything the board
   cannot host is guest text that merely looks like a tag, and must not
   mint tabs or cards. */
export const MAX_VM_SLOT = 8;

/* One carried verification step as a line. A kind this build does not
   name still reads as itself rather than as a blank label, so the bridge
   may add one without the screen going quiet. */
const STEP_LABEL = { pattern: (subject) => `/${subject}/` };

export function describeStep({ kind, subject } = {}) {
  const text = subject ?? "";
  const label = STEP_LABEL[kind];
  return label ? label(text) : `${kind ?? "?"} ${text}`.trim();
}

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

/* Counter ticks as whole microseconds. The firmware stamps records and
   published copies with CNTPCT and states CNTFRQ beside them, so every
   duration on this screen is this one division — a shadow's age, a
   panel's place against the newest reading, the gap between two marks,
   how old the root a walk used was.

   Null when the rate is not known yet: ticks shown as a duration would
   be wrong by whatever the clock turns out to be. Whole microseconds
   because nothing here is drawn finer, and the alternative is each
   caller rounding its own way. */
export function micros(ticks, hz) {
  return hz ? Math.round((Number(ticks) * 1e6) / hz) : null;
}

/* A measured duration, at the precision it deserves: past a millisecond
   the microseconds are noise. Unsigned, because a caller that shows a
   direction says so in its own words. */
export function elapsed(us) {
  const size = Math.abs(us);
  return size >= 1000 ? `${(size / 1000).toFixed(1)}ms` : `${size}us`;
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

/* A guest's VM id is the firmware slot its console lines are tagged with,
   which is not always the array position: a manifest may name a guest
   after the slot it loads into (a two-guest demo can hold vm0 and vm2).
   The name wins when it states a slot, position otherwise. */
const SLOT_NAME = /^vm(\d+)$/;

export function vmSlot(guest, index) {
  const match = SLOT_NAME.exec(String((guest && guest.name) || ""));
  return match ? Number(match[1]) : index;
}

/* Accents a subsystem name can be given. Severity keeps its own colours,
   so the crit red is deliberately absent from this rotation. */
const ACCENTS = [
  "var(--hyp)",
  "var(--warn)",
  "var(--violet)",
  "var(--good)",
  "var(--vm0)",
  "var(--vm1)",
  "var(--vm2)",
  "var(--vm3)",
];

/* One accent per name, shared because the event log and the board must
   agree: an edge is drawn in the colour of the badge whose rows explain
   it, and a reader crossing between the two follows one colour, not two. */
export const accentOf = (name) => ACCENTS[paletteIndex(name, ACCENTS.length)];

/* Accent class for a VM slot. The classes cycle v0..v3, one per --vm
   token in the palette, so the count is stated here beside the palette
   and derived from it nowhere else. */
const VM_SLOTS = 4;
export const vmAccent = (slot) => `v${slot % VM_SLOTS}`;

/* Stable string → index into a small palette (FNV-1a). Chip colours are
   derived, never mapped by name, so a vocabulary change needs no UI edit. */
function paletteIndex(name, size) {
  if (!size) return 0;
  let hash = 0x811c9dc5;
  const text = String(name);
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash % size;
}

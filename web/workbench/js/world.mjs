/* What the topology said, for anything that needs to ask.

   Answers about the machine rather than about a view, installed once
   instead of threaded through every call: the slot rule is asked of
   every console line and the manifest of every topic a screen names.
   Everything else a topology carries is handed to the view that draws
   it, which is why only these two live here. */

/* Console lines carry a firmware-tagged VM slot; anything the board
   cannot host is guest text that merely looks like a tag, and must not
   mint tabs or cards. How many slots exist is the board's own answer, so
   nothing is hostable before a topology arrives. */
let guestSlots = 0;

export function setGuestSlots(board) {
  guestSlots = Number(board && board.max_guests) || 0;
}

export const hostsGuest = (vm) => Number.isInteger(vm) && vm >= 0 && vm < guestSlots;

/* The topics the manifest declares, and a name the screen asks for that
   is not among them — undeclared reads exactly like declared without a
   rate, so a renamed topic draws an ordinary surface that never fills.
   Said once per name per manifest, and the screen goes on drawing. */
let declared = null;
const unknown = new Set();

export function setObservations(observations) {
  declared = observations && typeof observations === "object" ? observations : null;
  unknown.clear();
}

export function observationOf(topic) {
  if (declared === null) return undefined;
  const said = declared[topic];
  if (said === undefined && !unknown.has(topic)) {
    unknown.add(topic);
    console.error(`the screen asks about ${topic}, which the manifest does not declare`);
  }
  return said;
}

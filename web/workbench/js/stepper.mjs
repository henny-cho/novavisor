/* The stepper: which event to stop the machine at, and the three ways to
   reach one. Every control here follows the shared control state — a
   machine that is there, and no request of this page's still in flight. */

import { clear, el } from "./format.mjs";

/* Instructions, not events: this is for looking *inside* one. At about
   700us per instruction over the debug socket, forty is a fraction of a
   second — the count a reader starts from, not the one they are held to. */
const STEP_COUNT = 40;
const STEP_KEY = "nv-wb-steps";
/* The period is in seconds but the unit of progress is an event — a
   fixed slice of time holds anywhere from zero of them to thousands. */
const AUTO = { repeat: 50, period: 1 };

export function createStepper({
  pick,
  countInput,
  advanceButton,
  stepButton,
  autoButton,
  abortButton,
  note,
  send,
  onPending,
  onNotice,
}) {
  let autoRunning = false;
  /* How far one request may reach is the bridge's to say, and it says so
     in the first frame of every connection. A number invented here to
     hold until then would be the copy this reads from the wire to avoid. */
  let ceiling = Infinity;

  /* One line beside the controls saying what the machine is doing right
     now. The event log keeps the history; this answers "is it stuck?",
     which a scrolling log answers badly. */
  function say(text) {
    note.textContent = text || "";
    note.hidden = !text;
  }

  function halt(data) {
    if (send("halt", data)) return true;
    onNotice?.("브리지에 연결되지 않아 요청을 보내지 못했습니다");
    return false;
  }

  function setAuto(next) {
    autoRunning = next;
    autoButton.setAttribute("aria-pressed", String(next));
    abortButton.hidden = !next;
  }

  /* Driving needs a machine and no request of ours still on the bridge.
     Cancelling is the answer to a request in flight, so 중지 and a
     pressed 자동 are the two that flight does not stand down. */
  function setState(state) {
    const live = String(state.phase) === "running" && !state.replaying;
    /* A machine that is gone is not being stepped through. */
    if (!live && autoRunning) setAuto(false);
    const busy = Boolean(state.pending);
    advanceButton.disabled = !live || busy;
    stepButton.disabled = !live || busy;
    autoButton.disabled = !live || (busy && !autoRunning);
  }

  /* The choices come from the bridge with the rest of the topology, the
     same way badges and board blocks do. Naming a firmware function here
     would put a second copy of the catalogue in the client. */
  function setStops(stops) {
    const previous = pick.value;
    const offered = new Set([""]);
    clear(pick);
    /* No stop is a choice, not merely the initial state: it is what
       launches a run that is meant to keep going. */
    const none = el("option", "", "정지 없음");
    none.value = "";
    pick.append(none);
    for (const stop of stops || []) {
      /* One catalogue, two uses. Everything in it names a lane; only the
         entries backed by a firmware function name a place to halt, and
         the bridge says which those are rather than the client guessing
         from an id. */
      if (stop.stop === false) continue;
      const option = el("option", "", stop.label ? `${stop.id} — ${stop.label}` : stop.id);
      option.value = stop.id;
      pick.append(option);
      offered.add(stop.id);
    }
    /* A republish keeps the reader's choice; one this catalogue no longer
       offers is dropped, or a launch arms a stop the run cannot reach. */
    pick.value = offered.has(previous) ? previous : "";
  }

  const chosen = () => (pick.value ? [pick.value] : []);

  /* The ceiling rides the topology beside the stop catalogue. Asking past
     it would be answered by a silent clamp on the far side, so the box
     stops offering it. */
  function setLimits(limits) {
    const steps = Math.trunc(Number(limits && limits.steps));
    if (!(steps >= 1)) return;
    ceiling = steps;
    countInput.max = String(steps);
    count();
  }

  /* The reader's count, held inside the band and written back, so an
     entry outside it is corrected where it was typed. An empty box is
     not zero steps; it is the count they have not chosen. */
  function count() {
    const typed = Math.trunc(Number(countInput.value));
    const held = Math.min(typed >= 1 ? typed : STEP_COUNT, ceiling);
    countInput.value = String(held);
    return held;
  }

  /* Advancing needs an event to advance to: with nothing armed the machine
     would run on until the wait budget expired and stop nowhere. */
  function runTo(extra) {
    const stops = chosen();
    if (!stops.length) {
      say("정지 지점을 선택하세요");
      return false;
    }
    return halt({ cmd: "run", stops, ...extra });
  }

  /* A timeline mark and the picker name the same catalogue entry, so
     stopping at a mark is the picker being set and advanced. */
  function stopAt(id) {
    pick.value = id;
    if (!runTo()) return false;
    onPending?.("advance");
    say(`대기 · ${id}`);
    return true;
  }

  /* A new machine, or one released from a stop: whatever the note and the
     자동 toggle described, it has moved past. */
  function reset() {
    setAuto(false);
    say("");
  }

  advanceButton.addEventListener("click", () => {
    if (runTo()) onPending?.("advance");
  });

  /* The bridge takes the batch one instruction at a time, each with its
     own timeout, so a large count holds the forward controls down for as
     long as it runs. */
  stepButton.addEventListener("click", () => {
    const steps = count();
    if (!halt({ cmd: "step", count: steps })) return;
    onPending?.("step");
    say(`${steps} 명령 진행 중`);
  });

  countInput.addEventListener("change", () => {
    try {
      localStorage.setItem(STEP_KEY, String(count()));
    } catch {
      /* private mode: the count simply does not persist */
    }
  });

  autoButton.addEventListener("click", () => {
    if (autoRunning) {
      halt({ cmd: "abort" });
      setAuto(false);
      return;
    }
    if (runTo(AUTO)) {
      setAuto(true);
      onPending?.("auto");
    }
  });

  /* 중지 expects no answer — the bridge only raises a flag — so it names
     no request in flight. */
  abortButton.addEventListener("click", () => {
    halt({ cmd: "abort" });
    setAuto(false);
  });

  /* Before the wire says anything: no machine to drive, no 자동 running,
     and nothing to say about either. The count a reader last chose is a
     reading habit, so it opens where they left it. */
  reset();
  setState({ phase: "", paused: false, replaying: false, pending: null });
  try {
    countInput.value = localStorage.getItem(STEP_KEY) || "";
  } catch {
    /* private mode: the count opens at its default */
  }
  count();

  return { setState, setStops, setLimits, chosen, say, stopAt, reset };
}

/* Bootstrap: build the views, route every envelope to one of them, and own
   the two pieces of UI state the wire does not carry — the theme and how
   much of the stream was lost. */

import {
  budgetWords,
  clockLabel,
  describeStep,
  sealedFields,
  setGuestSlots,
} from "./format.mjs";
import { connect } from "./net.mjs";
import { createBoard } from "./board.mjs";
import { createCards } from "./cards.mjs";
import { createConsole } from "./console.mjs";
import { createDrive } from "./drive.mjs";
import { createEvents } from "./events.mjs";
import { createMemory } from "./memory.mjs";
import { createPanels } from "./panels.mjs";
import { createStepper } from "./stepper.mjs";
import { createTimeline } from "./timeline.mjs";
import { createTopology } from "./topology.mjs";

const THEME_KEY = "nv-wb-theme";
const ref = (id) => document.getElementById(id);

const phaseBadge = ref("phase");
const phaseText = ref("phase-text");
const bootMark = ref("boot");
const connBadge = ref("conn");
const connText = ref("conn-text");
const lossBadge = ref("loss");
const lossNumber = ref("loss-n");
const clockNode = ref("clock");
const themeButton = ref("theme");

/* Session phases the bridge publishes, in this UI's words. Unknown phases
   fall through to a plain notice rather than a blank badge. */
const PHASES = {
  idle: { text: "대기", tone: "idle" },
  building: { text: "빌드 중", tone: "busy" },
  running: { text: "실행 중", tone: "live" },
  verifying: { text: "검증 중", tone: "busy" },
  exited: { text: "종료", tone: "idle" },
  failed: { text: "실패", tone: "crit" },
  /* A run read back from a file. Named rather than shown as idle: what
     is on screen is real and was real, and a reader has to know which
     of those two it is looking at. */
  replay: { text: "리플레이", tone: "idle" },
};

const WIRE_FAULTS = {
  envelope: "잘못된 프레임",
  protocol: "프로토콜 불일치",
  handler: "화면 처리 실패",
  payload: "메시지 해석 실패",
  batch: "잘못된 메시지 묶음",
  socket: "연결 전송 실패",
};
const CRITICAL_WIRE_FAULTS = new Set(["protocol", "handler"]);

let latestTs = 0;
let clockText = "";
let lostFrames = 0;
let currentRun = null;
let wireFault = null;
/* Snapshots may arrive out of order during connect replay; the highest
   sequence is the current world. */
let topoSeq = 0;
/* Runs whose seal has already been logged. The summary arrives as a
   life event and again on every connect topology, and it is one fact. */
const sealedRuns = new Set();
let wire = { send: () => false, ask: () => false };

/* The facts every control follows. `pending` is the request this page
   sent and has not heard back on; `halt` is the command the bridge holds
   the machine for, which can outlast a click by minutes and answer many
   times before it ends — so it is the bridge's to report, not ours. */
const state = { phase: "idle", paused: false, replaying: false, pending: null, halt: null };
const controls = [];

/* Anything the wire reports answers whatever request was in flight. */
function reported(patch) {
  Object.assign(state, patch, { pending: null });
  for (const control of controls) control.setState(state);
}

/* A control sent a request; it stands down until the wire answers. */
function pending(what) {
  state.pending = what;
  for (const control of controls) control.setState(state);
}

const events = createEvents({
  list: ref("elog"),
  filters: ref("filters"),
  resetButton: ref("ev-all"),
  clearButton: ref("ev-clear"),
});

const notify = (message) => events.addNotice(latestTs, message, { dim: true });

const cards = createCards(ref("cards"));

const boardView = createBoard({
  view: ref("view"),
  board: ref("board"),
  bands: ref("bands"),
  wires: ref("wires"),
  split: ref("split"),
  foldButton: ref("fold"),
  /* Focusing a block narrows the log to the subsystems that explain it.
     The board decides which those are, from the paths touching it; the
     log keeps that separate from what the reader muted by hand. */
  onFocus: (badges) => events.narrow(badges),
  /* Click a path, walk what actually went down it. The board names the
     path; the catalogue says which recorded moments light it; the strip
     already knows how to ask for a filtered window and walk the answer.
     Nothing here is new machinery — it is those three, composed. */
  onTour: (edge) => startTour(edge),
});

const panels = createPanels({ tabs: ref("panel-tabs"), host: ref("panels") });

const memory = createMemory({
  pick: ref("mmap-pick"),
  form: ref("mmap-probe"),
  input: ref("mmap-at"),
  note: ref("mmap-note"),
  body: ref("mmap-body"),
  /* Same topic out as in: the kind already says which direction a
     frame went. */
  request: (data) => wire.ask("probe", data),
});

const drive = createDrive({
  root: ref("drive"),
  note: ref("drive-note"),
  send: (data) => wire.send("cmd", data),
});

/* What the view slot can hold, named here so the tab buttons carry no
   knowledge of what they switch to. */
const VIEWS = {
  board: { node: ref("board"), name: "실행 보드" },
  memory: { node: ref("mmap"), name: "메모리 맵" },
};
const viewName = ref("view-name");

function showView(wanted) {
  for (const [id, view] of Object.entries(VIEWS)) view.node.hidden = id !== wanted;
  for (const tab of document.querySelectorAll(".vtab[data-view]")) {
    tab.setAttribute("aria-selected", String(tab.dataset.view === wanted));
  }
  viewName.textContent = VIEWS[wanted].name;
  /* The board measures itself when it becomes visible again; it cannot
     have done so while it had no size. */
  if (wanted === "board") boardView.reveal();
  else memory.refresh();
}

for (const tab of document.querySelectorAll(".vtab[data-view]")) {
  tab.addEventListener("click", () => showView(tab.dataset.view));
}

const timelineNote = ref("tl-note"); /* what the run holds */
/* The tip belongs to the line it explains: one without the other
   would outlive it across a run boundary. */
const setTimelineNote = (text, title = "") => {
  timelineNote.textContent = text;
  timelineNote.title = title;
};
const stopHereButton = ref("tl-stop");
let markedEvent = null; /* the event a picked mark names, for "stop here" */
const timelineSel = ref("tl-sel"); /* what the reader picked */
const timeline = createTimeline({
  strip: ref("tl"),
  canvas: ref("tl-canvas"),
  foldButton: ref("tl-fold"),
  followButton: ref("tl-follow"),
  /* The window request goes out on the same topic the summaries come
     back on: the kind already distinguishes an answer from something
     sent unasked. */
  request: (data) => wire.ask("trace", data),
  /* A mark is a moment on a path, so selecting one says both: the
     fields the firmware recorded, and the path lit on the board. The
     board already knows how to focus, and pointing it at the edge the
     catalogue names keeps one focus vocabulary rather than two. */
  onSelect: (choice) => {
    if (choice.kind === "none") {
      /* Nothing selected: readings go back to being placed against the
         newest of themselves. */
      panels.setReference(null);
      timelineSel.textContent = "";
      stopHereButton.hidden = true;
      markedEvent = null;
      return;
    }
    if (choice.kind === "delta") {
      const gap = choice.micros === null ? "—" : `${choice.micros}us`;
      timelineSel.textContent = `${choice.from.id} → ${choice.to.id} · Δt ${gap}`;
      events.addNotice(latestTs, `Δt ${choice.from.id} → ${choice.to.id} = ${gap}`);
      return;
    }
    const record = choice.record;
    /* The chain, not the mark. What a reader takes from the strip is
       `bind → +111us inject`, and the neighbours arrive with the
       selection precisely so this line can say it. The gap is always
       the real one, whatever rate the cursor is being moved at. */
    const gap = choice.dt === null || choice.dt === undefined ? "" : ` · Δt ${choice.dt}us`;
    const where = choice.total ? ` · ${choice.index + 1}/${choice.total}` : "";
    const ahead = choice.next ? ` → ${choice.next.id}` : "";
    timelineSel.textContent =
      `${record.id}${ahead} · cpu${record.cpu}${gap}${where} · ${traceFields(record)}`;
    if (record.edge) boardView.focusPath(record.edge);
    /* The drawer's readings against the moment just picked: both are
       counter values, so the comparison is the machine's own. */
    panels.setReference(record.ts);
    /* One catalogue, two consumers: the moment a reader picked out of
       the trace is a stop point too, so offering the next one is a
       lookup rather than a second table. Only where the catalogue says
       the firmware has a symbol to break on — arming a lane that has
       none clears every breakpoint and leaves the machine to be
       interrupted wherever it happens to be. */
    markedEvent = record.stop ? record.id : null;
    stopHereButton.hidden = !markedEvent;
    if (markedEvent) stopHereButton.title = `다음 ${markedEvent}에서 정지`;
    /* In a replay the selection is the whole view's cursor: the moment
       a reader picks on the strip is the moment the panels and the
       console are returned to. Live there is only now, and asking
       would be asking a machine to have been something it is not. */
    if (state.replaying) wire.ask("cursor", { ts: record.ts });
  },
});

/* The world the bridge published this run: the catalogue a path is
   turned into recorded moments with, and the vocabularies a run's
   summary is named with. One copy, so neither is a second table. */
let world = {};
const stopsOf = () => (Array.isArray(world.stops) ? world.stops : []);

function startTour(edge) {
  const ids = stopsOf()
    .filter((stop) => stop.edge === edge)
    .map((stop) => stop.id);
  if (!ids.length) {
    /* A path with no recorded moment is drawn from structure alone.
       Saying so beats a tour that starts and shows nothing. */
    timelineSel.textContent = `${edge} — 이 경로를 기록하는 훅이 없습니다`;
    return;
  }
  if (!timeline.tour(ids, edge)) {
    timelineSel.textContent = `${edge} — 아직 기록된 통과가 없습니다`;
    return;
  }
  setPlaying(true);
  timelineSel.textContent = `투어 · ${edge} — 녹화된 순서 재생 중`;
  events.addNotice(latestTs, `경로 투어 — ${edge}`);
}

/* The catalogue names the record's three words; an unnamed position
   holds nothing for that event. Naming happens there and not here, so
   the UI never learns a layout the bridge already knows. */
function traceFields(record) {
  const values = [record.a, record.b, record.c];
  return (record.fields || [])
    .map((name, index) => (name ? `${name}=${values[index] ?? 0}` : ""))
    .filter(Boolean)
    .join(" ");
}
/* The stop is taken through the same path the picker uses, because a
   mark and a stop point are one fact: the catalogue that named the
   record is the catalogue the halt layer breaks on. */
stopHereButton.addEventListener("click", () => {
  if (!markedEvent) return;
  if (stepper.stopAt(markedEvent)) {
    events.addNotice(latestTs, `타임라인 마크에서 정지 요청 — ${markedEvent}`);
  }
});

/* The strip owns the state — a drag turns following off from inside —
   so the button asks for a change and the strip decides what it says. */
ref("tl-follow").addEventListener("click", (event) => {
  timeline.setFollow(event.currentTarget.getAttribute("aria-pressed") !== "true");
});

/* The same three movers the keys use. Buttons because a reader who has
   not clicked the strip yet has nowhere to press a key, and stepping is
   the first thing they want. */
const playButton = ref("tl-play");
function setPlaying(on) {
  playButton.setAttribute("aria-pressed", String(on));
  playButton.textContent = on ? "정지" : "재생";
}
ref("tl-prev").addEventListener("click", () => {
  timeline.stop();
  setPlaying(false);
  timeline.setFollow(false);
  timeline.step(-1);
});
ref("tl-next").addEventListener("click", () => {
  timeline.stop();
  setPlaying(false);
  timeline.setFollow(false);
  timeline.step(+1);
});
playButton.addEventListener("click", () => {
  const on = !timeline.isPlaying();
  if (on) {
    /* Playing the tail while the tail keeps moving is two cursors
       chasing each other. */
    timeline.setFollow(false);
    timeline.play();
  } else {
    timeline.stop();
  }
  setPlaying(on);
});

const consoleView = createConsole({
  tabs: ref("tabs"),
  logs: ref("logs"),
  banner: ref("banner"),
  form: ref("cin"),
  input: ref("cin-text"),
  focusButton: ref("cin-focus"),
  send: (topic, data) => wire.send(topic, data),
  onNotice: notify,
});

const topology = createTopology({
  select: ref("target"),
  variantSelect: ref("variant"),
  verifyBox: ref("verify"),
  runButton: ref("run"),
  pauseButton: ref("pause"),
  pane: ref("topo"),
  send: (topic, data) => wire.send(topic, data),
  /* The stepper's own read of the stop picker, so a stop armed at launch
     and one advanced to are always the same chosen event. */
  stops: () => stepper.chosen(),
  onStart: (demo) => events.addNotice(latestTs, `실행 요청 — ${demo}`),
  onPending: pending,
  onNotice: notify,
});

const stepper = createStepper({
  pick: ref("stop-at"),
  countInput: ref("step-count"),
  advanceButton: ref("advance"),
  stepButton: ref("step"),
  autoButton: ref("auto"),
  abortButton: ref("abort"),
  note: ref("stop-note"),
  send: (topic, data) => wire.send(topic, data),
  onPending: pending,
  onNotice: notify,
});

controls.push(topology, stepper, consoleView, drive);

/* ---------------- top bar state ---------------- */

function setPhase(phase, override) {
  /* A recording replays its own lifecycle — started, ran, exited — and
     those are history to look at, not transitions to obey: acting on them
     would put "실행 중" on a screen with no machine behind it. */
  if (state.replaying && phase !== "replay") return;
  const info = PHASES[phase];
  phaseBadge.dataset.tone = info ? info.tone : "idle";
  phaseText.textContent = override || (info ? info.text : phase || "—");
  reported({ phase: String(phase) });
}

/* Connect replay hands the fresh snapshot over before the older backlog,
   so the clock tracks the highest timestamp rather than the last one. */
function updateClock(ts) {
  const value = Number(ts);
  if (!Number.isFinite(value) || value <= latestTs) return;
  latestTs = value;
  const next = clockLabel(value);
  if (next === clockText) return;
  clockText = next;
  clockNode.textContent = next;
}

/* The wire reports it, so it is a fact of the control state; the button
   that follows it belongs to the group it sits in. */
const setPaused = (next) => reported({ paused: next });

function noteLoss(count) {
  if (!(count > 0)) return;
  lostFrames += count;
  lossNumber.textContent = String(lostFrames);
  lossBadge.hidden = false;
}

lossBadge.addEventListener("click", () => {
  lostFrames = 0;
  lossBadge.hidden = true;
});

/* ---------------- frame routing ---------------- */

const exitSeverity = (code) => (Number(code) === 0 ? "INFO" : "WARN");

/* What a run added up to, once per run: how many of each event, whether
   that is all of them, and the quantile its span samples support. The
   catalogue and the vocabularies it is named with are the topology's. */
function noteSealed(ts, sealed) {
  const run = sealed.run_id;
  if (sealedRuns.has(run)) return;
  sealedRuns.add(run);
  events.addNotice(ts, `런 ${run} 봉인`, {
    severity: sealed.complete ? "INFO" : "WARN",
    fields: sealedFields(sealed, stopsOf(), world.taxonomy, world.timer_slots),
  });
}

function onTopo(ts, data) {
  const topo = data && typeof data === "object" ? data : {};
  world = topo;
  /* Before anything can mint a tab or a card: how many VM slots the
     machine has is the board's, not a number typed into the client. */
  setGuestSlots(topo.board);
  topology.render(topo);
  const guests = Array.isArray(topo.guests) ? topo.guests : [];
  consoleView.setGuests(guests);
  cards.setGuests(guests);
  const taxonomy = topo.taxonomy && typeof topo.taxonomy === "object" ? topo.taxonomy : {};
  events.setBadges(taxonomy.badges);
  panels.setTopology(topo);
  boardView.setTopology(topo);
  memory.setWorld(topo.memory);
  drive.setWorld(topo);
  stepper.setStops(topo.stops);
  stepper.setLimits(topo.limits);
  timeline.setCatalogue(topo.stops);
  timeline.setLimits(topo.limits);
  timeline.setBoard(topo.board);
  /* Connect-time session state: the life events that built this picture
     may already be evicted from the backlog, so the fresh topo is the
     only reliable carrier for a late joiner. */
  if (topo.phase !== undefined) {
    const phase = String(topo.phase);
    /* Set before the switch below, so the guard in setPhase is already
       true for the very first thing it is asked to show. */
    reported({ replaying: phase === "replay", halt: topo.halt || null });
    if (phase === "running" && topo.paused) {
      setPaused(true);
      setPhase("running", "일시정지 (H)");
    } else {
      setPaused(false);
      setPhase(phase);
    }
  }
  /* The life event is not replayed, so a browser that joined after the
     seal reads the summary here — the same shape, noticed once. */
  if (topo.sealed) noteSealed(ts, topo.sealed);
  if (topo.run_id !== undefined) {
    /* A run that started while this client was away: its panels and
       counters describe the previous machine. */
    if (currentRun !== null && topo.run_id !== currentRun) {
      panels.clearAll();
      boardView.clearAll();
      cards.reset();
    }
    currentRun = topo.run_id;
  }
}

/* The `early` count is a different fact from a drain loss: those events
   predate the rings, so no drainer however prompt could have had them. */
function traceStateText(data) {
  switch (String(data.state || "")) {
    case "active": {
      const shape = data.capacity ? ` — 링 ${data.rings}×${data.capacity}` : "";
      return data.early
        ? `트레이스 연결${shape} · 배치 전 유실 ${data.early}건`
        : `트레이스 연결${shape}`;
    }
    case "waiting":
      return "트레이스 영역 대기 중";
    case "none":
      return "트레이스 계층 없음 (이미지에 링 기록자 심볼이 없음)";
    case "mismatch":
      return `트레이스 레이아웃 불일치: ${data.reason || "?"}`;
    default:
      return `트레이스 상태: ${data.state || "?"}`;
  }
}

function lifeDetail(data) {
  if (data.error) return String(data.error);
  if (data.reason) return String(data.reason);
  if (Array.isArray(data.guests) && data.guests.length) return data.guests.map(String).join(", ");
  return "";
}

function onLife(ts, data) {
  const phase = String(data.phase || "");
  const demo = data.demo ? ` — ${data.demo}` : "";
  switch (phase) {
    case "idle":
      setPhase(phase);
      events.addNotice(ts, "세션 대기");
      break;
    case "building":
      setPhase(phase);
      bootMark.hidden = true;
      events.addNotice(ts, `빌드 중${demo}`);
      break;
    case "running":
      setPhase(phase);
      setPaused(false);
      stepper.reset();
      consoleView.setBanner(null); /* a panic banner lives until the next run */
      /* Run boundary: measurements and counters from the previous
         machine must not read as this one's. The timeline goes too —
         a new machine restarts CNTPCT, so merging the two would put
         them in one order. */
      panels.clearAll();
      timeline.reset();
      setTimelineNote("트레이스 대기");
      timelineSel.textContent = "";
      stopHereButton.hidden = true;
      boardView.clearAll();
      cards.reset();
      consoleView.mark(`── ${data.demo || "?"} ──`);
      events.addNotice(ts, `실행 중${demo}`);
      break;
    /* H layer: the machine (and its virtual clock) is stopped. */
    case "paused":
      setPaused(true);
      setPhase("running", "일시정지 (H)");
      events.addNotice(ts, "머신 정지 — sysreg 실측 갱신");
      break;
    case "resumed":
      setPaused(false);
      stepper.reset();
      setPhase("running");
      /* A delta is only true of the pair of stops it was measured
         across; a running machine has moved past both. */
      panels.clearMoved();
      events.addNotice(ts, "머신 재개");
      break;
    /* Stopped *at* something, rather than wherever the reader clicked.
       The board lights the path this is evidence for; the log keeps the
       values, because a pulse fades and a reader may look away. */
    case "stopped": {
      setPaused(true);
      setPhase("running", data.event ? `정지 · ${data.event}` : "정지");
      boardView.stopped(ts, data);
      const args = data.args && typeof data.args === "object" ? data.args : {};
      const named = Object.keys(args).map((key) => `${key}=${args[key]}`).join(" ");
      /* Which core it stopped on: on an SMP machine the other cores were
         somewhere else, and the stop is evidence about this one. */
      const where = data.thread ? ` · thread ${data.thread}` : "";
      events.addNotice(
        ts,
        `정지 ${data.event || data.pc || ""}${where}${named ? ` — ${named}` : ""}`,
      );
      stepper.say(data.event ? `정지 · ${data.event}` : "정지");
      break;
    }
    /* The machine is running toward the stops, at launch and again at
       every repeat of an 자동 run: the pause a stop reported is over. A
       repeat restates the hold's last arming, so the log says it dimly. */
    case "armed": {
      const stops = Array.isArray(data.stops) ? data.stops : [];
      const again = stepper.armed(stops);
      setPaused(false);
      stepper.say(`무장 · ${stops.join(", ")}`);
      events.addNotice(ts, `정지 지점 무장 — ${stops.join(", ")}`, { dim: again });
      break;
    }
    /* The bridge holds the machine for one command, from the click until
       it lets go — after the sweep, and after every repeat. What the
       drive controls may do follows this, not the request they sent. */
    case "halt-begin":
      reported({ halt: { cmd: String(data.cmd || "") } });
      break;
    case "halt-end":
      reported({ halt: null });
      break;
    /* The chosen event may be rare, or may not happen on this demo at
       all. Saying so is the difference between waiting and looking
       broken. */
    case "waiting":
      stepper.say(`대기 · ${(data.stops || []).join(", ")}`);
      break;
    case "stepped":
      setPaused(true);
      /* A step at `wfi` does not retire until an interrupt arrives, and
         the hypervisor idles there between events — so this is the
         ordinary answer, not a fault. */
      stepper.say(
        data.stalled ? "대기 중 — 명령이 끝나지 않음 (wfi)" : `${data.steps} 명령 진행`,
      );
      events.addNotice(
        ts,
        data.stalled
          ? "스텝 정지 — wfi에서 대기 중 (인터럽트 전까지 명령이 끝나지 않음)"
          : `${data.steps} 명령 진행 → ${(data.stop && data.stop.pc) || ""}`,
      );
      break;
    case "verifying":
      setPhase(phase);
      consoleView.mark("── verify ──");
      events.addNotice(ts, "검증 중");
      break;
    case "exited":
      setPhase(phase, `종료 (code=${data.code ?? "?"})`);
      events.addNotice(ts, `세션 종료 code=${data.code ?? "?"}`, {
        severity: exitSeverity(data.code),
      });
      break;
    case "failed":
      setPhase(phase);
      events.addNotice(ts, `실패: ${data.error || "원인 미상"}`, { severity: "CRIT" });
      break;
    case "booted":
      bootMark.hidden = false;
      events.addNotice(ts, "부팅 완료");
      break;
    /* Not terminal: a demo may report its exit code more than once. */
    case "demo-exit":
      events.addNotice(ts, `데모 종료 code=${data.code ?? "?"}`, {
        severity: exitSeverity(data.code),
      });
      break;
    case "panic": {
      const message = data.message ? String(data.message) : "EL2 패닉";
      consoleView.setBanner(message);
      events.addNotice(ts, message, { severity: "CRIT" });
      break;
    }
    /* Terminal in practice — QEMU never exits this wfi loop — but the
       session is still RUNNING to the bridge, so the badge has to say
       so itself rather than let "실행 중" stand for a parked machine. */
    case "halted":
      setPhase("running", "게스트 종료 — 머신 유지");
      events.addNotice(ts, "모든 vCPU 정지", { severity: "WARN" });
      break;
    case "verify-pass":
      events.addNotice(ts, `검증 통과 ${data.carried ?? "?"}/${data.total ?? "?"}`);
      break;
    /* How far it got before it failed, the pair the pass badge already
       carries: a failure at step 2 and one at step 20 read differently. */
    case "verify-fail":
      events.addNotice(
        ts,
        `검증 실패 ${data.carried ?? "?"}/${data.total ?? "?"} (${data.failure || "?"}${
          data.step ? ` — ${data.step}` : ""
        })`,
        { severity: "CRIT" },
      );
      break;
    /* Where the T layer stands, said once per transition rather than
       inferred from the absence of trace frames. */
    case "trace":
      events.addNotice(ts, traceStateText(data), {
        dim: data.state !== "mismatch",
        severity: data.state === "mismatch" ? "WARN" : undefined,
      });
      break;
    case "task-failed":
      events.addNotice(ts, `백그라운드 작업 실패: ${data.error || "원인 미상"}`, {
        severity: "CRIT",
      });
      break;
    case "snapshot-unavailable":
      events.addNotice(ts, `정지 스냅샷 실패: ${data.error || "원인 미상"}`, {
        severity: "CRIT",
      });
      break;
    case "stop-failed":
      events.addNotice(ts, `프로세스 종료 실패: ${data.target || "현재 머신"}`, {
        severity: "WARN",
      });
      break;
    /* Which guest, and which of its fields the machine placed otherwise. */
    case "guests-differ": {
      const guests = data.guests && typeof data.guests === "object" ? data.guests : {};
      const said = Object.entries(guests)
        .map(([name, fields]) => `${name}: ${Array.isArray(fields) ? fields.join(", ") : "?"}`)
        .join(" · ");
      events.addNotice(ts, `게스트 구성 불일치: ${said || "대상 미상"}`, { severity: "WARN" });
      break;
    }
    case "uplink-rejected":
      reported({}); /* a rejected request ends its attempt */
      events.addNotice(ts, `업링크 거부: ${data.reason || "?"}`, { severity: "WARN" });
      break;
    case "query-cancelled":
      events.addNotice(ts, `질문 취소: ${data.reason || "?"}`, { severity: "WARN" });
      break;
    case "run-sealed":
      noteSealed(ts, data);
      break;
    case "frames-dropped":
      /* The seq holes the eviction left already count these frames;
         adding the bridge's number would double them. */
      events.addNotice(ts, `브리지 프레임 유실 ${data.count ?? "?"}건`, { dim: true });
      break;
    default:
      {
        const detail = lifeDetail(data);
        events.addNotice(ts, `상태: ${phase || "?"}${detail ? ` — ${detail}` : ""}`, {
          severity: detail ? "CRIT" : "WARN",
        });
      }
  }
}

function onFrame(frame) {
  updateClock(frame.ts);
  const data = frame.data && typeof frame.data === "object" ? frame.data : {};
  switch (frame.topic) {
    case "topo":
      if (frame.seq >= topoSeq) {
        topoSeq = frame.seq;
        onTopo(frame.ts, data);
      }
      break;
    case "console":
      consoleView.append(data, frame.ts);
      cards.touch(data.vm, data.text);
      break;
    case "ev":
      /* One event, three readers: a log row, evidence a path was used,
         and — for a focus switch — the tab host input reached. */
      events.addEvent(frame.ts, data);
      boardView.note(frame.ts, data);
      consoleView.note(data);
      break;
    /* T layer: what the firmware recorded, drained from its rings. The
       summary lights the board; the window answers draw the order. */
    case "trace":
      if (frame.kind === "snapshot") {
        timeline.apply(data);
        break;
      }
      boardView.traced(frame.ts, data);
      timeline.note(data);
      /* The rate the drawer needs before a difference between two
         counter values is a duration. */
      if (data.span?.freq_hz) {
        panels.setClock(data.span.freq_hz);
        memory.setClock(data.span.freq_hz);
      }
      /* The one record that answers rather than reports: it goes back
         to the control that asked for it. */
      drive.answered(data.command);
      if (data.span) {
        const held = data.span.full
          ? `${data.span.n} 레코드 · 지평선 도달`
          : `${data.span.n} 레코드`;
        const words = data.budget ? budgetWords(data.budget) : { text: "", title: "" };
        setTimelineNote(words.text ? `${held} · ${words.text}` : held, words.title);
        /* The declared horizon and the observed stall side by side,
           with the crossing marked. */
        timelineNote.classList.toggle("over", Boolean(data.budget?.overrun));
      }
      if (data.dropped) noteLoss(data.dropped);
      break;
    /* One regime's tables, walked, in answer to this client's own
       request. */
    case "probe":
      memory.answer(data);
      break;
    case "life":
      onLife(frame.ts, data);
      break;
    /* Where in the run the reader is looking. The strip, the panels and
       the console were three views that could only agree about the
       present; this is the one number that makes them agree about a
       past. The panels arrive as ordinary snapshots just before it, so
       nothing here has to put them anywhere. */
    case "cursor":
      consoleView.cutAt(data.wire ?? null);
      events.cutAt(data.wire ?? null);
      /* Both, or the two views beside each other disagree about the
         same moment: the panel saying a topic was not read yet and the
         board still painting the value it later took. */
      panels.setUnread(data.unread);
      boardView.setUnread(data.unread);
      break;
    /* One carried step of a --verify run; index is 1-based. The kind
       travels with it, so a step this build does not know still reads as
       itself rather than as a blank label. */
    case "verify":
      events.addNotice(
        frame.ts,
        `검증 ${data.index ?? "?"}/${data.total ?? "?"} — ${describeStep(data)}`,
      );
      break;
    default:
      /* S-layer snapshot topics come from the observation manifest as
         plain strings; the panels declare which ones they consume. */
      if (panels.accepts(frame.topic)) panels.apply(frame);
      if (boardView.accepts(frame.topic)) boardView.apply(frame);
      if (memory.accepts(frame.topic)) memory.apply(frame);
      break;
  }
}

function onStatus(state) {
  const live = state === "connected";
  if (live) wireFault = null;
  if (!live && wireFault) return;
  connBadge.dataset.tone = live ? "live" : "busy";
  connText.textContent = live ? "연결됨" : "재연결 중";
}

function onFault(fault) {
  wireFault = fault;
  connBadge.dataset.tone = CRITICAL_WIRE_FAULTS.has(fault.kind) ? "crit" : "busy";
  connText.textContent = fault.count > 1 ? `수신 오류 ×${fault.count}` : "수신 오류";
  if (fault.count !== 1) return;
  const label = WIRE_FAULTS[fault.kind] || fault.kind;
  events.addNotice(latestTs, `수신 오류 — ${label}: ${fault.detail}`, {
    severity: CRITICAL_WIRE_FAULTS.has(fault.kind) ? "CRIT" : "WARN",
  });
}

function onHealthy() {
  if (!wireFault) return;
  wireFault = null;
  onStatus("connected");
}

/* The bridge restarted: nothing on screen describes the new session. */
function onReset() {
  consoleView.clearAll();
  cards.clearAll();
  events.clearAll();
  panels.clearAll();
  boardView.clearAll();
  lostFrames = 0;
  lossBadge.hidden = true;
  bootMark.hidden = true;
  currentRun = null;
  sealedRuns.clear();
  setPaused(false);
  topoSeq = 0;
  clockText = "";
  latestTs = 0;
  setPhase("idle");
  events.addNotice(0, "브리지 세션이 새로 시작되어 화면을 초기화했습니다", { dim: true });
}

/* Reconnected over a hole the backlog replay cannot fill. */
function onGap() {
  notify("재연결됨 — 끊긴 동안의 스트림 일부는 복원되지 않았을 수 있습니다");
}

/* End of one 50ms flush window: settle the work the views deferred —
   one layout and one card write per batch instead of one per line. */
function onBatch() {
  consoleView.settle();
  events.settle();
  panels.settle();
  boardView.settle();
  cards.settle();
}

/* ---------------- theme ---------------- */

function applyTheme(theme) {
  const dark = theme !== "light";
  document.documentElement.dataset.nvTheme = dark ? "dark" : "light";
  themeButton.textContent = dark ? "☀ Light" : "☾ Dark";
  themeButton.setAttribute("aria-pressed", String(dark));
}

function storedTheme() {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch {
    return null;
  }
}

themeButton.addEventListener("click", () => {
  const next = document.documentElement.dataset.nvTheme === "light" ? "dark" : "light";
  applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    /* private mode: the theme simply does not persist */
  }
});

applyTheme(storedTheme() || "dark");
wire = connect({
  onFrame,
  onStatus,
  onFault,
  onHealthy,
  onReset,
  onLoss: noteLoss,
  onGap,
  onBatch,
});

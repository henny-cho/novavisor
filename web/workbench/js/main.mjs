/* Bootstrap: build the views, route every envelope to one of them, and own
   the two pieces of UI state the wire does not carry — the theme and how
   much of the stream was lost. */

import { MAX_VM_SLOT, clear, clockLabel, el } from "./format.mjs";
import { connect, send } from "./net.mjs";
import { createBoard } from "./board.mjs";
import { createCards } from "./cards.mjs";
import { createConsole } from "./console.mjs";
import { createEvents } from "./events.mjs";
import { createPanels } from "./panels.mjs";
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
const runButton = ref("run");
const rerunButton = ref("rerun");
const pauseButton = ref("pause");
const stopPick = ref("stop-at");
const advanceButton = ref("advance");
const stepButton = ref("step");
const autoButton = ref("auto");
const abortButton = ref("abort");
const stopNote = ref("stop-note");

/* Session phases the bridge publishes, in this UI's words. Unknown phases
   fall through to a plain notice rather than a blank badge. */
const PHASES = {
  idle: { text: "대기", tone: "idle" },
  building: { text: "빌드 중", tone: "busy" },
  running: { text: "실행 중", tone: "live" },
  verifying: { text: "검증 중", tone: "busy" },
  exited: { text: "종료", tone: "idle" },
  failed: { text: "실패", tone: "crit" },
};

let latestTs = 0;
let clockText = "";
let lostFrames = 0;
let currentRun = null;
let paused = false;
let autoRunning = false;
/* Snapshots may arrive out of order during connect replay; the highest
   sequence is the current world. */
let topoSeq = 0;

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
});

const panels = createPanels({ tabs: ref("panel-tabs"), host: ref("panels") });

const consoleView = createConsole({
  tabs: ref("tabs"),
  logs: ref("logs"),
  banner: ref("banner"),
  form: ref("cin"),
  input: ref("cin-text"),
  focusButton: ref("cin-focus"),
  onNotice: notify,
});

const topology = createTopology({
  select: ref("target"),
  runButton: ref("run"),
  rerunButton,
  pane: ref("topo"),
  onStart: (demo) => {
    rerunButton.hidden = true;
    /* One start per click storm: the next terminal phase (or a
       rejection) re-arms the button. */
    runButton.disabled = true;
    events.addNotice(latestTs, `실행 요청 — ${demo}`);
  },
  onNotice: notify,
});

/* ---------------- top bar state ---------------- */

function setPhase(phase, override) {
  const info = PHASES[phase];
  phaseBadge.dataset.tone = info ? info.tone : "idle";
  phaseText.textContent = override || (info ? info.text : phase || "—");
  /* Only a running machine can be paused; every other phase offering
     the button would send stop to a machine that no longer exists. */
  pauseButton.hidden = phase !== "running";
  /* Same reason as the pause button: advancing a machine that is not
     there sends commands the bridge can only reject. */
  for (const control of [advanceButton, stepButton, autoButton]) {
    control.disabled = phase !== "running";
  }
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

function setPaused(next) {
  paused = next;
  pauseButton.textContent = paused ? "재개" : "일시정지";
}

pauseButton.addEventListener("click", () => {
  if (!send("halt", { cmd: paused ? "cont" : "stop" })) {
    notify("브리지에 연결되지 않아 요청을 보내지 못했습니다");
  }
});

/* The choices come from the bridge with the rest of the topology, the
   same way badges and board blocks do. Naming a firmware function here
   would put a second copy of the catalogue in the client. */
function setStops(stops) {
  const previous = stopPick.value;
  clear(stopPick);
  for (const stop of stops || []) {
    const option = el("option", "", stop.label ? `${stop.id} — ${stop.label}` : stop.id);
    option.value = stop.id;
    stopPick.append(option);
  }
  if (previous) stopPick.value = previous;
}

const chosen = () => (stopPick.value ? [stopPick.value] : []);

function setAuto(next) {
  autoRunning = next;
  autoButton.setAttribute("aria-pressed", String(next));
  abortButton.hidden = !next;
}

/* One line beside the controls saying what the machine is doing right
   now. The event log keeps the history; this answers "is it stuck?",
   which a scrolling log answers badly. */
function say(text) {
  stopNote.textContent = text || "";
  stopNote.hidden = !text;
}

function halt(data) {
  if (!send("halt", data)) {
    notify("브리지에 연결되지 않아 요청을 보내지 못했습니다");
    return false;
  }
  return true;
}

advanceButton.addEventListener("click", () => {
  halt({ cmd: "run", stops: chosen() });
});

/* Instructions, not events: this is for looking *inside* one. At about
   700us per instruction over the debug socket, forty is a fraction of a
   second and forty thousand would be a minute. */
stepButton.addEventListener("click", () => {
  halt({ cmd: "step", count: 40 });
});

/* The period is in seconds but the unit of progress is an event — a
   fixed slice of time holds anywhere from zero of them to thousands. */
autoButton.addEventListener("click", () => {
  if (autoRunning) {
    halt({ cmd: "abort" });
    setAuto(false);
    return;
  }
  if (halt({ cmd: "run", stops: chosen(), repeat: 50, period: 1 })) setAuto(true);
});

abortButton.addEventListener("click", () => {
  halt({ cmd: "abort" });
  setAuto(false);
});

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

function onTopo(data) {
  const topo = data && typeof data === "object" ? data : {};
  topology.render(topo);
  const guests = Array.isArray(topo.guests) ? topo.guests : [];
  consoleView.setGuests(guests);
  cards.setGuests(guests);
  const taxonomy = topo.taxonomy && typeof topo.taxonomy === "object" ? topo.taxonomy : {};
  events.setBadges(taxonomy.badges);
  panels.setTopology(topo);
  boardView.setTopology(topo);
  setStops(topo.stops);
  /* Connect-time session state: the life events that built this picture
     may already be evicted from the backlog, so the fresh topo is the
     only reliable carrier for a late joiner. */
  if (topo.phase !== undefined) {
    const phase = String(topo.phase);
    if (phase === "running" && topo.paused) {
      setPaused(true);
      setPhase("running", "일시정지 (H)");
    } else {
      setPaused(false);
      setPhase(phase);
    }
    rerunButton.hidden = phase !== "exited" && phase !== "failed";
  }
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
    case "active":
      return data.early
        ? `트레이스 연결 — 배치 전 유실 ${data.early}건`
        : "트레이스 연결";
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

function onLife(ts, data) {
  const phase = String(data.phase || "");
  const demo = data.demo ? ` — ${data.demo}` : "";
  switch (phase) {
    case "idle":
      setPhase(phase);
      runButton.disabled = false;
      events.addNotice(ts, "세션 대기");
      break;
    case "building":
      setPhase(phase);
      bootMark.hidden = true;
      rerunButton.hidden = true;
      events.addNotice(ts, `빌드 중${demo}`);
      break;
    case "running":
      setPhase(phase);
      runButton.disabled = false;
      rerunButton.hidden = true;
      setPaused(false);
      setAuto(false);
      say("");
      consoleView.setBanner(null); /* a panic banner lives until the next run */
      /* Run boundary: measurements and counters from the previous
         machine must not read as this one's. */
      panels.clearAll();
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
      setAuto(false);
      setPhase("running");
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
      events.addNotice(ts, `정지 ${data.event || data.pc || ""}${named ? ` — ${named}` : ""}`);
      say(data.event ? `정지 · ${data.event}` : "정지");
      break;
    }
    case "armed":
      say(`무장 · ${(data.stops || []).join(", ")}`);
      events.addNotice(ts, `정지 지점 무장 — ${(data.stops || []).join(", ")}`);
      break;
    /* The chosen event may be rare, or may not happen on this demo at
       all. Saying so is the difference between waiting and looking
       broken. */
    case "waiting":
      say(`대기 · ${(data.stops || []).join(", ")}`);
      break;
    case "stepped":
      setPaused(true);
      /* A step at `wfi` does not retire until an interrupt arrives, and
         the hypervisor idles there between events — so this is the
         ordinary answer, not a fault. */
      say(data.stalled ? "대기 중 — 명령이 끝나지 않음 (wfi)" : `${data.steps} 명령 진행`);
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
      runButton.disabled = false;
      rerunButton.hidden = false;
      events.addNotice(ts, `세션 종료 code=${data.code ?? "?"}`, {
        severity: exitSeverity(data.code),
      });
      break;
    case "failed":
      setPhase(phase);
      runButton.disabled = false;
      rerunButton.hidden = false;
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
    case "halted":
      events.addNotice(ts, "모든 vCPU 정지", { severity: "WARN" });
      break;
    case "verify-pass":
      events.addNotice(ts, `검증 통과 ${data.matched ?? "?"}/${data.total ?? "?"}`);
      break;
    case "verify-fail":
      events.addNotice(
        ts,
        `검증 실패 (${data.failure || "?"}${data.pattern ? ` — ${data.pattern}` : ""})`,
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
    case "unsupported":
      events.addNotice(ts, `미지원 업링크 토픽: ${data.topic || "?"}`, { dim: true });
      break;
    case "uplink-rejected":
      runButton.disabled = false; /* a rejected select ends its attempt */
      events.addNotice(ts, `업링크 거부: ${data.reason || "?"}`, { dim: true });
      break;
    case "frames-dropped":
      /* The seq holes the eviction left already count these frames;
         adding the bridge's number would double them. */
      events.addNotice(ts, `브리지 프레임 유실 ${data.count ?? "?"}건`, { dim: true });
      break;
    default:
      events.addNotice(ts, `상태: ${phase || "?"}`, { dim: true });
  }
}

function onFrame(frame) {
  updateClock(frame.ts);
  const data = frame.data && typeof frame.data === "object" ? frame.data : {};
  switch (frame.topic) {
    case "topo":
      if (frame.seq >= topoSeq) {
        topoSeq = frame.seq;
        onTopo(data);
      }
      break;
    case "console":
      consoleView.append(data);
      if (Number.isInteger(data.vm) && data.vm >= 0 && data.vm < MAX_VM_SLOT) {
        cards.touch(data.vm, data.text);
      }
      break;
    case "ev":
      /* The same event, read two ways: the log takes it as a row, the
         board as evidence that a path was used. */
      events.addEvent(frame.ts, data);
      boardView.note(frame.ts, data);
      break;
    /* T layer: what the firmware recorded, drained from its rings. */
    case "trace":
      boardView.traced(frame.ts, data);
      if (data.dropped) noteLoss(data.dropped);
      break;
    case "life":
      onLife(frame.ts, data);
      break;
    /* One matched expectation of a --verify run; index is 1-based. */
    case "verify":
      events.addNotice(
        frame.ts,
        `검증 진행 ${data.index ?? "?"}/${data.total ?? "?"} — ${data.pattern ?? ""}`,
      );
      break;
    default:
      /* S-layer snapshot topics come from the observation manifest as
         plain strings; the panels declare which ones they consume. */
      if (panels.accepts(frame.topic)) panels.apply(frame);
      if (boardView.accepts(frame.topic)) boardView.apply(frame);
      break;
  }
}

function onStatus(state) {
  const live = state === "connected";
  connBadge.dataset.tone = live ? "live" : "busy";
  connText.textContent = live ? "연결됨" : "재연결 중";
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
  runButton.disabled = false;
  rerunButton.hidden = true;
  currentRun = null;
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

/* End of one 50ms flush window: settle the scroll work the views
   deferred, one layout per batch instead of one per line. */
function onBatch() {
  consoleView.settle();
  events.settle();
  panels.settle();
  boardView.settle();
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
  } catch (error) {
    return null;
  }
}

themeButton.addEventListener("click", () => {
  const next = document.documentElement.dataset.nvTheme === "light" ? "dark" : "light";
  applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch (error) {
    /* private mode: the theme simply does not persist */
  }
});

applyTheme(storedTheme() || "dark");
connect({ onFrame, onStatus, onReset, onLoss: noteLoss, onGap, onBatch });

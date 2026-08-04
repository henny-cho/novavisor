/* Bootstrap: build the views, route every envelope to one of them, and own
   the two pieces of UI state the wire does not carry — the theme and how
   much of the stream was lost. */

import { MAX_VM_SLOT, clockLabel } from "./format.mjs";
import { connect, send } from "./net.mjs";
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
  if (!send("qmp", { cmd: paused ? "cont" : "stop" })) {
    notify("브리지에 연결되지 않아 요청을 보내지 못했습니다");
  }
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
      cards.reset();
    }
    currentRun = topo.run_id;
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
      consoleView.setBanner(null); /* a panic banner lives until the next run */
      /* Run boundary: measurements and counters from the previous
         machine must not read as this one's. */
      panels.clearAll();
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
      setPhase("running");
      events.addNotice(ts, "머신 재개");
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
      events.addEvent(frame.ts, data);
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

/* WebSocket client: one socket, one sequence tracker, one reconnect timer.
   Downlink messages are always JSON arrays of envelopes; uplink messages
   are always single JSON objects. */

const WS_PATH = "/ws";
const BACKOFF_MIN_MS = 500;
const BACKOFF_MAX_MS = 5000;
/* Duplicate memory. The bridge replays at most a few hundred frames on
   connect, so this window is generous while staying bounded. */
const SEEN_LIMIT = 8192;
const DEBUG = new URLSearchParams(location.search).has("debug");

let socket = null;
let backoffMs = BACKOFF_MIN_MS;
let retryTimer = 0;
let hooks = {};

/* Sequence state: highest accepted seq, the floor under which everything
   counts as already delivered, and the recent set in between. */
let lastSeq = 0;
let floorSeq = 0;
let seen = new Set();
/* The next frame is the first one of a freshly opened connection. */
let firstOfConnection = true;

function endpoint() {
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  return `${scheme}${location.host}${WS_PATH}`;
}

function debug(...args) {
  if (DEBUG) console.info("[wb]", ...args);
}

function resetTracking() {
  lastSeq = 0;
  floorSeq = 0;
  seen = new Set();
}

/* True the first time a seq is delivered; false for replayed duplicates. */
function accept(seq) {
  if (seq <= floorSeq || seen.has(seq)) return false;
  seen.add(seq);
  if (seq > lastSeq) lastSeq = seq;
  if (seen.size > SEEN_LIMIT) {
    floorSeq = lastSeq - SEEN_LIMIT / 2;
    for (const value of seen) if (value <= floorSeq) seen.delete(value);
  }
  return true;
}

function dispatch(frame) {
  if (!frame || typeof frame.seq !== "number") return;
  const seq = frame.seq;
  if (firstOfConnection) {
    firstOfConnection = false;
    /* A restarted bridge restarts its counter. Within one bridge the
       replayed snapshot always carries a fresh (higher) seq, so a first
       frame at or below what we already saw can only be a new session. */
    if (seq <= lastSeq) {
      debug("new session", { seq, lastSeq });
      resetTracking();
      hooks.onReset?.();
    }
  } else if (seq > lastSeq + 1) {
    /* Replay fills in below lastSeq and is never a loss; only a forward
       jump during live broadcast means frames never arrived. */
    hooks.onLoss?.(seq - lastSeq - 1);
  }
  if (!accept(seq)) return;
  hooks.onFrame?.(frame);
}

function ingest(payload) {
  let frames;
  try {
    frames = JSON.parse(payload);
  } catch (error) {
    debug("undecodable message", error);
    return;
  }
  /* Every downlink message is a batch array; a bare object is a bug. */
  if (!Array.isArray(frames)) {
    debug("non-batch message ignored");
    return;
  }
  for (const frame of frames) dispatch(frame);
}

function scheduleRetry() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(open, backoffMs);
  backoffMs = Math.min(BACKOFF_MAX_MS, backoffMs * 2);
}

function open() {
  socket = new WebSocket(endpoint());
  socket.addEventListener("open", () => {
    backoffMs = BACKOFF_MIN_MS;
    firstOfConnection = true;
    hooks.onStatus?.("connected");
    debug("connected");
  });
  socket.addEventListener("message", (event) => ingest(event.data));
  socket.addEventListener("close", () => {
    socket = null;
    hooks.onStatus?.("reconnecting");
    scheduleRetry();
  });
  /* An error is always followed by close; that path owns the retry. */
  socket.addEventListener("error", () => debug("socket error"));
}

/* callbacks: { onFrame, onStatus, onReset, onLoss } */
export function connect(callbacks) {
  hooks = callbacks || {};
  open();
  return { send };
}

/* Uplink: a single JSON object, never an array. Returns false when the
   socket is down so the caller can surface the loss. */
export function send(topic, data) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify({ topic, data }));
  return true;
}

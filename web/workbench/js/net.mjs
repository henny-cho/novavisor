/* WebSocket client: one socket, one receiver, and bounded query state.
   Downlink messages are arrays of envelopes; uplinks are single objects. */

const WS_PATH = "/ws";
const PROTOCOL_VERSION = 3;
const BACKOFF_MIN_MS = 500;
const BACKOFF_MAX_MS = 5000;
const SEEN_LIMIT = 8192;
const PAGE = typeof location === "undefined"
  ? { protocol: "http:", host: "localhost", search: "" }
  : location;
const DEBUG = new URLSearchParams(PAGE.search).has("debug");
const TERMINAL_PHASES = new Set(["uplink-rejected", "query-cancelled"]);

function endpoint() {
  const scheme = PAGE.protocol === "https:" ? "wss://" : "ws://";
  return `${scheme}${PAGE.host}${WS_PATH}`;
}

function debug(...args) {
  if (DEBUG) console.info("[wb]", ...args);
}

function randomPrefix() {
  if (globalThis.crypto?.randomUUID) {
    return `wb:${globalThis.crypto.randomUUID().replaceAll("-", "")}`;
  }
  if (globalThis.crypto?.getRandomValues) {
    const words = new Uint32Array(4);
    globalThis.crypto.getRandomValues(words);
    const value = [...words].map((word) => word.toString(16).padStart(8, "0")).join("");
    return `wb:${value}`;
  }
  return `wb:${Date.now().toString(36)}${Math.random().toString(16).slice(2)}`;
}

function requestIds(prefix = randomPrefix()) {
  let next = 0;
  return {
    make: () => `${prefix}:${++next}`,
    owns: (requestId) =>
      typeof requestId === "string" && requestId.startsWith(`${prefix}:`),
  };
}

function createQueryBroker(transmit) {
  const slots = new Map();

  function issue(topic, slot, data) {
    const requestId = transmit(topic, data);
    if (requestId === null) {
      slot.replacement = data;
      return false;
    }
    slot.active = { requestId, data };
    slot.replacement = null;
    return true;
  }

  function ask(topic, data) {
    let slot = slots.get(topic);
    if (!slot) {
      slot = { active: null, replacement: null };
      slots.set(topic, slot);
    }
    if (slot.active) {
      slot.replacement = data;
      return true;
    }
    return issue(topic, slot, data);
  }

  function terminal(frame) {
    const replyTo = frame?.reply_to;
    if (typeof replyTo !== "string") return;
    let entry = null;
    if (frame.topic !== "life") {
      const slot = slots.get(frame.topic);
      if (slot?.active?.requestId === replyTo) entry = [frame.topic, slot];
    } else if (TERMINAL_PHASES.has(frame.data?.phase)) {
      entry = [...slots].find(([, slot]) => slot.active?.requestId === replyTo) || null;
    }
    if (!entry) return;
    const [topic, slot] = entry;
    slot.active = null;
    const replacement = slot.replacement;
    slot.replacement = null;
    if (replacement !== null) issue(topic, slot, replacement);
  }

  function disconnected() {
    for (const slot of slots.values()) {
      if (!slot.active) continue;
      if (slot.replacement === null) slot.replacement = slot.active.data;
      slot.active = null;
    }
  }

  function reconnected() {
    for (const [topic, slot] of slots) {
      if (!slot.active && slot.replacement !== null) {
        const data = slot.replacement;
        issue(topic, slot, data);
      }
    }
  }

  return { ask, terminal, disconnected, reconnected };
}

export function createReceiver(callbacks = {}, options = {}) {
  let lastSeq = 0;
  let floorSeq = 0;
  let seen = new Set();
  let sessionToken = null;
  let firstOfConnection = true;
  let faultKind = null;
  let faultCount = 0;
  let batchFaulted = false;
  const ownsReply = options.ownsReply || (() => true);

  function fault(kind, detail) {
    batchFaulted = true;
    faultCount = kind === faultKind ? faultCount + 1 : 1;
    faultKind = kind;
    try {
      callbacks.onFault?.({ kind, detail: String(detail), count: faultCount });
    } catch (error) {
      debug("fault handler failed", error);
    }
  }

  function healthy() {
    if (faultKind === null) return;
    faultKind = null;
    faultCount = 0;
    try {
      callbacks.onHealthy?.();
    } catch (error) {
      debug("healthy handler failed", error);
    }
  }

  function reset() {
    lastSeq = 0;
    floorSeq = 0;
    seen = new Set();
    sessionToken = null;
  }

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
    if (!frame || !Number.isSafeInteger(frame.seq) || frame.seq < 1) {
      fault("envelope", "missing or invalid frame sequence");
      return;
    }
    const seq = frame.seq;
    const compatible = frame.v === undefined || frame.v === PROTOCOL_VERSION;
    const token = compatible && frame.topic === "topo" && frame.data
      ? frame.data.session
      : undefined;
    if (token !== undefined) {
      if (sessionToken !== null && token !== sessionToken) {
        debug("new bridge", { token });
        reset();
        callbacks.onReset?.();
      }
      sessionToken = token;
    }
    if (firstOfConnection) {
      firstOfConnection = false;
      if (seq <= lastSeq) {
        debug("new session", { seq, lastSeq });
        reset();
        callbacks.onReset?.();
      } else if (lastSeq && seq > lastSeq + 1) {
        callbacks.onGap?.();
      }
    } else if (seq > lastSeq + 1) {
      callbacks.onLoss?.(seq - lastSeq - 1);
    }
    if (!accept(seq)) return;
    if (!compatible) {
      fault("protocol", `received version ${frame.v}; expected ${PROTOCOL_VERSION}`);
      return;
    }
    if (frame.reply_to !== undefined && !ownsReply(frame.reply_to)) return;
    try {
      callbacks.onFrame?.(frame);
    } catch (error) {
      debug("frame handler failed", frame.topic, error);
      fault("handler", `${frame.topic || "?"}: ${error}`);
    } finally {
      options.onTerminal?.(frame);
    }
  }

  function ingest(payload) {
    batchFaulted = false;
    let frames;
    try {
      frames = JSON.parse(payload);
    } catch (error) {
      debug("undecodable message", error);
      fault("payload", error);
      return;
    }
    if (!Array.isArray(frames)) {
      debug("non-batch message ignored");
      fault("batch", "message is not an array");
      return;
    }
    for (const frame of frames) dispatch(frame);
    try {
      callbacks.onBatch?.();
    } catch (error) {
      debug("batch handler failed", error);
      fault("handler", `batch: ${error}`);
    }
    if (!batchFaulted) healthy();
  }

  return {
    fault,
    ingest,
    opened() {
      firstOfConnection = true;
      faultKind = null;
      faultCount = 0;
    },
    reset,
  };
}

export function connect(callbacks = {}, options = {}) {
  const ids = requestIds(options.requestPrefix);
  let socket = null;
  let backoffMs = BACKOFF_MIN_MS;
  let retryTimer = 0;

  function transmit(topic, data) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return null;
    const requestId = ids.make();
    try {
      socket.send(JSON.stringify({ topic, data, request_id: requestId }));
    } catch (error) {
      receiver.fault("socket", error);
      return null;
    }
    return requestId;
  }

  const broker = createQueryBroker(transmit);
  const receiver = createReceiver(callbacks, {
    ownsReply: ids.owns,
    onTerminal: broker.terminal,
  });

  function scheduleRetry() {
    clearTimeout(retryTimer);
    retryTimer = setTimeout(open, backoffMs);
    backoffMs = Math.min(BACKOFF_MAX_MS, backoffMs * 2);
  }

  function open() {
    const ws = new WebSocket(endpoint());
    socket = ws;
    let healthyTimer = 0;
    ws.addEventListener("open", () => {
      receiver.opened();
      healthyTimer = setTimeout(() => {
        backoffMs = BACKOFF_MIN_MS;
      }, BACKOFF_MAX_MS);
      callbacks.onStatus?.("connected");
      broker.reconnected();
      debug("connected");
    });
    ws.addEventListener("message", (event) => receiver.ingest(event.data));
    ws.addEventListener("close", () => {
      clearTimeout(healthyTimer);
      if (socket !== ws) return;
      socket = null;
      broker.disconnected();
      callbacks.onStatus?.("reconnecting");
      scheduleRetry();
    });
    ws.addEventListener("error", (event) => {
      debug("socket error", event);
      receiver.fault("socket", event?.message || "WebSocket transport error");
    });
  }

  open();
  return {
    ask: broker.ask,
    send: (topic, data) => transmit(topic, data) !== null,
  };
}

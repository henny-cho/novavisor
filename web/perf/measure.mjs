/* What the workbench UI costs, measured in a browser against the page
   itself.

   The page is served and loaded unmodified; only `WebSocket` is stubbed,
   so every scenario below is the real client handling a real frame batch
   rather than a rehearsal of one. Node's test DOM is a tree with no
   style engine and cannot answer this: much of what a batch costs is the
   layout it leaves behind, and that is where the surprises have been.

   Each scenario reports the two halves apart — the script the batch runs,
   and the style and layout it leaves for the next frame — because a fix
   for one is not a fix for the other. `share` is the number that says
   whether the UI keeps up: cost times the rate the bridge really
   publishes at, as a fraction of one core.

   Prints JSON. What counts as too slow is the caller's to say. */

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

import * as wire from "./session.mjs";

const UI = fileURLToPath(new URL("../workbench/", import.meta.url));
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
};
/* One frame at 60Hz. A batch costing more than this cannot be absorbed
   between two paints, whatever else the page is doing. */
const FRAME_MS = 1000 / 60;

async function serve() {
  const server = createServer(async (request, response) => {
    const path = normalize(decodeURI(request.url.split("?")[0]));
    const file = join(UI, path === "/" ? "index.html" : path);
    try {
      const body = await readFile(file);
      response.writeHead(200, { "content-type": TYPES[extname(file)] ?? "text/plain" });
      response.end(body);
    } catch {
      response.writeHead(404).end("not found");
    }
  });
  await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
  return { server, port: server.address().port };
}

/* The socket the page opens, answered from here. Installed before any
   module runs, so `net.mjs` connects to it as it would to a bridge. */
function harness() {
  class Socket {
    constructor() {
      this.readyState = 1;
      this.listeners = { open: [], message: [], close: [], error: [] };
      window.__socket = this;
      setTimeout(() => this.listeners.open.forEach((fn) => fn({})), 0);
    }

    addEventListener(type, fn) {
      (this.listeners[type] ??= []).push(fn);
    }

    send() {}

    close() {}
  }
  Socket.OPEN = 1;
  window.WebSocket = Socket;
  window.WebSocket.OPEN = 1;

  /* The receiver drops a sequence it has already seen, so a batch fed
     twice is a batch handled once. Each feed is renumbered and restamped
     the way a bridge numbers what it sends, which is also what keeps the
     clock, the log stamps and the cut moving. */
  window.__wire = { seq: 0, ts: 0 };
  window.__feed = (frames) => {
    for (const frame of frames) {
      frame.seq = window.__wire.seq += 1;
      frame.ts = window.__wire.ts += 1_000_000;
    }
    const text = JSON.stringify(frames);
    for (const fn of window.__socket.listeners.message) fn({ data: text });
  };

  /* The browser's own view of main-thread blocking, so a scenario that
     stalls is reported as a stall and not only as a median. */
  window.__long = [];
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) window.__long.push(Math.round(entry.duration));
  }).observe({ entryTypes: ["longtask"] });

  /* Timed in the page: a round trip per sample would measure the driver.
     The two halves are separated by forcing the layout the batch left
     pending — that work is the batch's, and letting whatever paints next
     absorb it is how it stays hidden. */
  window.__measure = (batches, warm, samples) => {
    const pick = (index) => batches[index % batches.length];

    for (let index = 0; index < warm; index += 1) window.__feed(pick(index));
    const script = [];
    const render = [];
    for (let index = 0; index < samples; index += 1) {
      const started = performance.now();
      window.__feed(pick(warm + index));
      const fed = performance.now();
      void document.body.offsetHeight;
      render.push(performance.now() - fed);
      script.push(fed - started);
    }
    const median = (values) => values.sort((a, b) => a - b)[values.length >> 1];
    return { script: median(script), render: median(render) };
  };
}

/* Wire events worth timing, each named for what causes it. `rate` is how
   often the bridge publishes it — from the observation manifest, or the
   protocol's flush window for what the machine emits. */
function scenarios() {
  const values = wire.readings();
  const at = (topics) => topics.map((topic) => wire.snapshot(topic, values[topic]));
  const rated = (hz) => Object.keys(wire.RATES).filter((topic) => wire.RATES[topic] === hz);
  const drain = wire.traceDrain(2048);
  return [
    { name: "console-burst", rate: 20,
      what: "a boot burst: 200 console lines in one flush window",
      batches: [wire.consoleLines(200)] },
    { name: "event-burst", rate: 20,
      what: "50 classified events — log rows and the paths they light",
      batches: [wire.events(50)] },
    { name: "snapshot-20hz", rate: 20,
      what: "the S-layer topics that publish twenty times a second",
      batches: [at(rated(20))] },
    { name: "snapshot-all", rate: 2,
      what: "every observed topic in one tick, as a stop publishes them",
      batches: [at(Object.keys(wire.RATES))] },
    { name: "trace-drain", rate: 4,
      what: "a drain summary and the 2048-record window the strip asks for",
      batches: [[drain.summary, drain.window]] },
    { name: "busy-window", rate: 20,
      what: "one flush of a running machine: output, events, readings and a drain together",
      batches: [[
        ...wire.consoleLines(40),
        ...wire.events(10),
        ...at([...rated(20), ...rated(10)]),
        drain.summary,
      ]] },
    { name: "cursor-step", rate: 20,
      what: "the replay cursor moving one mark: both logs re-cut",
      /* Eight distinct moments, so the cut really moves rather than being
         handed the threshold it already holds. */
      batches: Array.from({ length: 8 }, (_, i) => [wire.cursor(3_000_000 + i * 40_000)]) },
  ];
}

/* A session that has been running a while: the board built, the strip
   holding records, and both logs filled past their caps.

   Filled deliberately rather than left to whatever a scenario before it
   appended. The logs cost what they cost by the row, so a page whose
   panes are half full answers a different question depending on which
   scenario ran first — and the answer worth having is the steady one a
   long session reaches and stays at. */
const OVER_CAP = 8000;

function session() {
  const drain = wire.traceDrain(2048);
  return [
    wire.topology(),
    wire.life("running", { demo: "07-shm" }),
    wire.life("booted"),
    ...wire.consoleLines(OVER_CAP),
    ...wire.events(OVER_CAP),
    ...Object.entries(wire.readings()).map(([topic, value]) => wire.snapshot(topic, value)),
    drain.summary,
    drain.window,
  ];
}

function report({ name, what, rate = null, once = false, samples, result, long = [] }) {
  const total = result.script + result.render;
  return {
    name,
    what,
    rate_hz: rate,
    once,
    samples,
    script_ms: Number(result.script.toFixed(2)),
    render_ms: Number(result.render.toFixed(2)),
    total_ms: Number(total.toFixed(2)),
    budget_ms: Number(FRAME_MS.toFixed(2)),
    /* Of one core, at the rate this batch really arrives. */
    share: rate ? Number(((total * rate) / 1000).toFixed(3)) : null,
    longtasks: long.length,
    worst_longtask_ms: long.length ? Math.max(...long) : 0,
  };
}

async function main() {
  const samples = Number(process.argv[2] ?? 21);
  const warm = Math.max(3, Math.round(samples / 3));
  const { server, port } = await serve();
  const browser = await chromium.launch({ args: ["--no-sandbox", "--disable-gpu"] });
  const faults = [];
  const open = async () => {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    page.on("pageerror", (error) => faults.push(`pageerror: ${error.message}`));
    page.on("console", (message) => {
      if (message.type() === "error") faults.push(`console: ${message.text()}`);
    });
    await page.addInitScript(harness);
    await page.goto(`http://127.0.0.1:${port}/index.html`);
    await page.waitForFunction(() => window.__socket?.listeners.message.length > 0);
    return page;
  };

  /* The first topology a reader ever gets, on a page that has drawn
     nothing. Measured on fresh pages rather than by re-feeding one: the
     views recognise a topology they already hold and rebuild nothing,
     which is correct of them and useless here. */
  const opening = [];
  const first = [wire.topology(), wire.life("running", { demo: "07-shm" })];
  for (let round = 0; round < 5; round += 1) {
    const page = await open();
    opening.push(await page.evaluate((frames) => {
      const started = performance.now();
      window.__feed(frames);
      const fed = performance.now();
      void document.body.offsetHeight;
      return { script: fed - started, render: performance.now() - fed };
    }, first));
    await page.close();
  }
  opening.sort((a, b) => a.script + a.render - (b.script + b.render));
  const measured = [
    report({
      name: "connect",
      what: "the first topology: board, cards, rail, panels and controls built",
      once: true,
      samples: opening.length,
      result: opening[opening.length >> 1],
    }),
  ];

  const page = await open();
  await page.evaluate((frames) => window.__feed(frames), session());
  await page.waitForTimeout(300);
  for (const scenario of scenarios()) {
    const before = await page.evaluate(() => window.__long.length);
    const result = await page.evaluate(
      ({ batches, warm: w, samples: n }) => window.__measure(batches, w, n),
      { batches: scenario.batches, warm, samples },
    );
    const long = await page.evaluate((n) => window.__long.slice(n), before);
    measured.push(report({ ...scenario, samples, result, long }));
  }

  const version = browser.version();
  await browser.close();
  server.close();
  process.stdout.write(
    `${JSON.stringify({ browser: `chromium ${version}`, samples, faults, scenarios: measured }, null, 1)}\n`,
  );
  return faults.length ? 1 : 0;
}

process.exitCode = await main();

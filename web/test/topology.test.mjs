/* Launch group test: the payload a launch sends — variant, verification
   and a stop armed at launch all travel with the demo — and the choice
   the page comes back up on. The toggle is the other half: a machine
   that exists is one to stop, not one to launch again. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createTopology } from "../workbench/js/topology.mjs";
import { element, fire, installDom } from "./dom.mjs";

/* Every control the launch group owns, for the states that answer for
   all of them at once. */
const OWNED = ["select", "variantSelect", "verifyBox", "runButton", "pauseButton"];

const CATALOG = [
  { id: "02", name: "02_timer", variants: [] },
  { id: "12", name: "12_zephyr", variants: ["heartbeat", "dma"] },
];

function harness({ stops = () => [], storage = null } = {}) {
  installDom();
  /* A fresh DOM hands out a fresh storage; a page that has to remember
     across a reload is given the one the previous page wrote. */
  if (storage) globalThis.localStorage = storage;
  const select = element("select");
  const variantSelect = element("select");
  const verifyBox = element("input");
  const runButton = element("button");
  const pauseButton = element("button");
  const pane = element("div");
  const sent = [];
  const notices = [];
  /* The control state main.mjs owns, driven here the way the wire drives
     it: a request stands until the next thing the bridge reports. */
  const state = { phase: "idle", paused: false, replaying: false, pending: null, halt: null };

  const view = createTopology({
    select,
    variantSelect,
    verifyBox,
    runButton,
    pauseButton,
    pane,
    send: (topic, data) => {
      sent.push([topic, data]);
      return true;
    },
    stops,
    onStart: () => {},
    onPending: (what) => {
      state.pending = what;
      view.setState(state);
    },
    onNotice: (message) => notices.push(message),
  });

  const at = (patch) => {
    Object.assign(state, patch, { pending: null });
    view.setState(state);
  };

  return {
    view,
    at,
    select,
    variantSelect,
    verifyBox,
    runButton,
    pauseButton,
    pane,
    sent,
    notices,
  };
}

const topo = (extra = {}) => ({ demo: null, guests: [], catalog: CATALOG, ...extra });
const options = (node) => node.children.map((option) => option.value);

describe("target picker", () => {
  it("offers the chosen demo's variants and hides when it has none", () => {
    const { view, select, variantSelect } = harness();
    view.render(topo());

    /* The first catalogue entry has none, so there is nothing to pick. */
    assert.equal(select.value, "02_timer");
    assert.deepEqual(options(variantSelect), []);
    assert.equal(variantSelect.hidden, true);

    select.value = "12_zephyr";
    fire(select, "change");
    assert.deepEqual(options(variantSelect), ["heartbeat", "dma"]);
    assert.equal(variantSelect.hidden, false);
    /* The demo's first variant, which is also what the bridge builds
       when a run names none. */
    assert.equal(variantSelect.value, "heartbeat");
  });

  it("sends demo, variant and verify as they are chosen", () => {
    const { view, select, variantSelect, verifyBox, runButton, sent } = harness();
    view.render(topo());

    select.value = "12_zephyr";
    fire(select, "change");
    variantSelect.value = "dma";
    verifyBox.checked = true;
    fire(runButton, "click");

    assert.deepEqual(sent, [["target", { demo: "12_zephyr", variant: "dma", verify: true }]]);
  });

  it("sends no variant for a demo that has none, and no verify unasked", () => {
    const { view, runButton, sent } = harness();
    view.render(topo());
    fire(runButton, "click");

    assert.deepEqual(sent, [["target", { demo: "02_timer", variant: null, verify: false }]]);
  });

  it("carries the chosen stop so it is armed at launch", () => {
    const held = [];
    const { view, at, runButton, sent } = harness({ stops: () => held });
    view.render(topo());

    /* Nothing chosen: a run that is meant to keep going says nothing
       about stops rather than arming an empty list. */
    fire(runButton, "click");
    assert.equal("stops" in sent[0][1], false);

    /* That run ended: the button is armed again, and launches again. */
    held.push("trap");
    at({ phase: "idle" });
    fire(runButton, "click");
    assert.deepEqual(sent[1][1].stops, ["trap"]);
  });

  it("comes back up on the choice it was left with", () => {
    const first = harness();
    first.view.render(topo());
    first.select.value = "12_zephyr";
    fire(first.select, "change");
    first.variantSelect.value = "dma";
    first.verifyBox.checked = true;
    fire(first.runButton, "click");

    /* A new page against the same storage: the run button alone has to
       repeat the last launch, so what it sends must survive the reload. */
    const again = harness({ storage: globalThis.localStorage });
    again.view.render(topo());
    assert.equal(again.select.value, "12_zephyr");
    assert.equal(again.variantSelect.value, "dma");
    assert.equal(again.verifyBox.checked, true);
    fire(again.runButton, "click");
    assert.deepEqual(again.sent, [
      ["target", { demo: "12_zephyr", variant: "dma", verify: true }],
    ]);
  });

  it("takes the machine's launch as what 실행 sends next, once per run", () => {
    const { view, select, variantSelect, verifyBox } = harness();
    view.render(topo({ demo: "12_zephyr", variant: "dma", run_id: 1 }));
    assert.equal(select.value, "12_zephyr");
    assert.equal(variantSelect.value, "dma");
    assert.equal(verifyBox.checked, false);

    /* The reader looks ahead to another demo. A republish within the run
       — page tables landed, an edge regraded — is not a launch. */
    select.value = "02_timer";
    fire(select, "change");
    view.render(topo({ demo: "12_zephyr", variant: "dma", run_id: 1, description: "Zephyr" }));
    assert.equal(select.value, "02_timer");
  });

  it("relaunches the machine it saw stopped, whoever launched it", () => {
    const { view, at, runButton, sent } = harness();
    view.render(topo({ demo: "12_zephyr", variant: "heartbeat", run_id: 3 }));
    at({ phase: "running" });
    /* The stop: no machine, the same run number. */
    view.render(topo({ demo: null, run_id: 3 }));
    at({ phase: "idle" });

    fire(runButton, "click");
    assert.deepEqual(sent, [["target", { demo: "12_zephyr", variant: "heartbeat", verify: false }]]);
  });

  it("takes a relaunch of the same demo as a boundary too", () => {
    const { view, select } = harness();
    view.render(topo({ demo: "12_zephyr", variant: "dma", run_id: 1 }));
    view.render(topo({ demo: null, run_id: 1 }));
    select.value = "02_timer";
    fire(select, "change");

    view.render(topo({ demo: "12_zephyr", variant: "dma", run_id: 2 }));
    assert.equal(select.value, "12_zephyr");
  });

  it("leaves the pick alone for a machine the catalogue does not offer", () => {
    const { view, select } = harness();
    view.render(topo());
    select.value = "02_timer";
    fire(select, "change");

    /* A measurement-only demo runs from the CLI: 실행 could not send it. */
    view.render(topo({ demo: "19_irq_latency", run_id: 1 }));
    assert.equal(select.value, "02_timer");
  });

  it("outranks the launch a previous page remembered, and is remembered in turn", () => {
    const first = harness();
    first.view.render(topo());
    first.select.value = "02_timer";
    fire(first.select, "change");
    first.verifyBox.checked = true;
    fire(first.runButton, "click");

    /* A new page onto a machine somebody else launched: what runs wins
       over what this browser launched yesterday, and the run that
       happened was not a verify. */
    const again = harness({ storage: globalThis.localStorage });
    again.view.render(topo({ demo: "12_zephyr", variant: "dma", run_id: 7 }));
    assert.equal(again.select.value, "12_zephyr");
    assert.equal(again.variantSelect.value, "dma");
    assert.equal(again.verifyBox.checked, false);

    const third = harness({ storage: globalThis.localStorage });
    third.view.render(topo());
    assert.equal(third.select.value, "12_zephyr");
    assert.equal(third.variantSelect.value, "dma");
  });

  it("reads 정지 while a machine is building, running or verifying and 실행 otherwise", () => {
    const { view, at, runButton } = harness();
    view.render(topo());

    for (const phase of ["building", "running", "verifying"]) {
      at({ phase });
      assert.equal(runButton.textContent, "정지", phase);
    }
    for (const phase of ["idle", "exited", "failed", "replay"]) {
      at({ phase });
      assert.equal(runButton.textContent, "실행", phase);
    }
  });

  it("sends stop while active and target while idle", () => {
    const { view, at, runButton, sent, notices } = harness();
    view.render(topo());

    at({ phase: "running" });
    fire(runButton, "click");
    assert.deepEqual(sent, [["stop", {}]]);
    assert.deepEqual(notices, ["정지 요청 — 머신을 정지합니다"]);

    /* The stop landed: nothing is running, so the same button launches. */
    at({ phase: "idle" });
    fire(runButton, "click");
    assert.deepEqual(sent[1], ["target", { demo: "02_timer", variant: null, verify: false }]);
  });

  it("is disabled after a click until the wire answers it", () => {
    const { view, at, runButton, sent } = harness();
    view.render(topo());

    fire(runButton, "click");
    assert.equal(runButton.disabled, true);
    /* A click storm is one request: the bridge is building, and the
       clicks that land meanwhile must not queue a second launch. */
    fire(runButton, "click");
    assert.equal(sent.length, 1);

    at({ phase: "running" });
    assert.equal(runButton.disabled, false);
  });

  it("stays disabled while a stop it sent is still in flight", () => {
    const { view, at, runButton, sent } = harness();
    view.render(topo());

    at({ phase: "building" });
    fire(runButton, "click");
    assert.deepEqual(sent, [["stop", {}]]);
    /* The machine came up anyway — the stop lands once it has. A phase
       that re-armed the button here would let a second stop go out. */
    view.setState({ phase: "running", paused: false, replaying: false, pending: "stop" });
    assert.equal(runButton.disabled, true);
    fire(runButton, "click");
    assert.equal(sent.length, 1);
  });

  it("refuses every control it owns in a replay", () => {
    const kit = harness();
    kit.view.render(topo());

    kit.at({ phase: "replay", replaying: true });
    /* A knob left live in a replay reads as a choice the reader can
       make, and there is no machine for any of them to reach. */
    for (const name of OWNED) assert.equal(kit[name].disabled, true, name);
    /* And nothing re-arms them: a file stays a file. */
    kit.at({});
    for (const name of OWNED) assert.equal(kit[name].disabled, true, name);
    fire(kit.runButton, "click");
    fire(kit.pauseButton, "click");
    assert.deepEqual(kit.sent, []);
  });

  it("offers 일시정지 only for a running machine, and 재개 once it is", () => {
    const { view, at, pauseButton } = harness();
    view.render(topo());

    at({ phase: "running" });
    assert.equal(pauseButton.hidden, false);
    assert.equal(pauseButton.textContent, "일시정지");
    /* The wire reports the pause; the label follows that, not the click
       that asked for it. */
    at({ phase: "running", paused: true });
    assert.equal(pauseButton.textContent, "재개");

    for (const phase of ["idle", "building", "verifying", "exited", "failed"]) {
      at({ phase });
      assert.equal(pauseButton.hidden, true, phase);
    }
  });

  it("stands 일시정지 down while the bridge holds the machine for a command", () => {
    const { view, at, runButton, pauseButton } = harness();
    view.render(topo());

    at({ phase: "running" });
    assert.equal(pauseButton.disabled, false);
    /* A run toward a stop is an inspection in flight; a pause asked for
       meanwhile could only be refused. Stopping the session is not: the
       machine can be taken away from under a hold. */
    at({ halt: { cmd: "run" } });
    assert.equal(pauseButton.disabled, true);
    assert.equal(runButton.disabled, false);
    at({ halt: null });
    assert.equal(pauseButton.disabled, false);
  });

  it("halts and releases the machine with the one button", () => {
    const { view, at, pauseButton, sent } = harness();
    view.render(topo());

    at({ phase: "running" });
    fire(pauseButton, "click");
    assert.deepEqual(sent, [["halt", { cmd: "stop" }]]);

    at({ phase: "running", paused: true });
    fire(pauseButton, "click");
    assert.deepEqual(sent[1], ["halt", { cmd: "cont" }]);
  });

  it("says a stop during a build applies once the build has finished", () => {
    const { view, at, runButton, notices } = harness();
    view.render(topo());

    /* The session lock holds the stop until the launch it is queued
       behind lands, and the button alone implies none of that. */
    at({ phase: "building" });
    fire(runButton, "click");
    assert.match(notices.at(-1), /빌드/u);

    at({ phase: "running" });
    fire(runButton, "click");
    assert.doesNotMatch(notices.at(-1), /빌드/u);
  });

  it("shows the running variant beside the demo it belongs to", () => {
    const { view, pane } = harness();
    view.render(topo({ demo: "12_zephyr", variant: "dma", description: "Zephyr" }));

    assert.match(pane.textContent, /ID 12 · variant dma/u);
  });
});

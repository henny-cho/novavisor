/* Launch control test: what a launch actually asks the bridge for, and
   which of its two meanings the one button carries.

   Every knob here is one the uplink has always accepted, so the thing
   under test is the payload — that a variant, a verification run and a
   stop armed at launch travel with the demo, and that the page comes
   back up on the choice it was left with. The toggle is the other half:
   a machine that exists is one to stop, not one to launch again. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createTopology } from "../workbench/js/topology.mjs";
import { element, fire, installDom } from "./dom.mjs";

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
  const pane = element("div");
  const sent = [];
  const notices = [];

  const view = createTopology({
    select,
    variantSelect,
    verifyBox,
    runButton,
    pane,
    send: (topic, data) => {
      sent.push([topic, data]);
      return true;
    },
    stops,
    onStart: () => {},
    onNotice: (message) => notices.push(message),
  });

  return { view, select, variantSelect, verifyBox, runButton, pane, sent, notices };
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
    const { view, runButton, sent } = harness({ stops: () => held });
    view.render(topo());

    /* Nothing chosen: a run that is meant to keep going says nothing
       about stops rather than arming an empty list. */
    fire(runButton, "click");
    assert.equal("stops" in sent[0][1], false);

    /* That run ended: the button is armed again, and launches again. */
    held.push("trap");
    view.setPhase("idle");
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

  it("reads 정지 while a machine is building, running or verifying and 실행 otherwise", () => {
    const { view, runButton } = harness();
    view.render(topo());

    for (const phase of ["building", "running", "verifying"]) {
      view.setPhase(phase);
      assert.equal(runButton.textContent, "정지", phase);
    }
    for (const phase of ["idle", "exited", "failed", "replay"]) {
      view.setPhase(phase);
      assert.equal(runButton.textContent, "실행", phase);
    }
  });

  it("sends stop while active and target while idle", () => {
    const { view, runButton, sent, notices } = harness();
    view.render(topo());

    view.setPhase("running");
    fire(runButton, "click");
    assert.deepEqual(sent, [["stop", {}]]);
    assert.deepEqual(notices, ["정지 요청"]);

    /* The stop landed: nothing is running, so the same button launches. */
    view.setPhase("idle");
    fire(runButton, "click");
    assert.deepEqual(sent[1], ["target", { demo: "02_timer", variant: null, verify: false }]);
  });

  it("is disabled after a click until the phase re-arms it", () => {
    const { view, runButton, sent } = harness();
    view.render(topo());

    fire(runButton, "click");
    assert.equal(runButton.disabled, true);
    /* A click storm is one request: the bridge is building, and the
       clicks that land meanwhile must not queue a second launch. */
    fire(runButton, "click");
    assert.equal(sent.length, 1);

    view.setPhase("running");
    assert.equal(runButton.disabled, false);
  });

  it("replay keeps it disabled", () => {
    const { view, runButton, sent } = harness();
    view.render(topo());

    view.setPhase("replay");
    assert.equal(runButton.disabled, true);
    /* And nothing re-arms it: a file has no machine to launch or stop. */
    view.arm(true);
    assert.equal(runButton.disabled, true);
    fire(runButton, "click");
    assert.deepEqual(sent, []);
  });

  it("shows the running variant beside the demo it belongs to", () => {
    const { view, pane } = harness();
    view.render(topo({ demo: "12_zephyr", variant: "dma", description: "Zephyr" }));

    assert.match(pane.textContent, /ID 12 · variant dma/u);
  });
});

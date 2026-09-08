/* Target picker test: what a launch actually asks the bridge for.

   Every knob here is one the uplink has always accepted, so the thing
   under test is the payload — that a variant, a verification run and a
   stop armed at launch travel with the demo, and that the page comes
   back up on the choice it was left with. */

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
  const rerunButton = element("button");
  const pane = element("div");
  const sent = [];

  const view = createTopology({
    select,
    variantSelect,
    verifyBox,
    runButton,
    rerunButton,
    pane,
    send: (topic, data) => {
      sent.push([topic, data]);
      return true;
    },
    stops,
    onStart: () => {},
    onNotice: () => {},
  });

  return { view, select, variantSelect, verifyBox, runButton, rerunButton, pane, sent };
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

    held.push("trap");
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

  it("shows the running variant beside the demo it belongs to", () => {
    const { view, pane } = harness();
    view.render(topo({ demo: "12_zephyr", variant: "dma", description: "Zephyr" }));

    assert.match(pane.textContent, /ID 12 · variant dma/u);
  });
});

/* The board against the manifest it draws from: a topic it names that
   the manifest does not declare reads exactly like one declared without
   a rate, so the badge looks ordinary over a surface that will never
   fill. The board says so and keeps drawing. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createBoard } from "../workbench/js/board.mjs";
import { setObservations } from "../workbench/js/format.mjs";
import { element, installDom } from "./dom.mjs";

const BOARD = {
  name: "qemu-virt", cpus: 2, cpu: "cortex-a57", vcpu_stride: 4, max_guests: 4,
  blocks: [
    { id: "gicd", label: "GICD", layer: "ic", base: 0x8000000, size: 0x10000 },
    { id: "uart0", label: "UART0", layer: "dev", base: 0x9000000, size: 0x1000, intid: 33 },
  ],
  edges: [],
  regions: { pa: [], ipa: [] },
};

/* The board reads its own height from the element it hangs in, so the
   view needs a parent the way it has one on the page. */
function harness(observations) {
  installDom();
  setObservations(observations);
  const host = element("div");
  const view = element("div");
  host.append(view);
  const said = [];
  const spoke = console.error;
  console.error = (...parts) => said.push(parts.join(" "));
  try {
    const board = createBoard({
      view,
      board: element("div"),
      bands: element("div"),
      wires: element("div"),
      split: element("div"),
      foldButton: element("button"),
      onFocus: () => {},
      onTour: () => {},
    });
    board.setTopology({ board: BOARD, observations, guests: [{ name: "linux", vcpus: 1 }] });
    board.settle();
  } finally {
    console.error = spoke;
  }
  return said;
}

describe("board vocabulary", () => {
  const nameOf = (line) => line.match(/asks about (\S+),/u)[1];

  it("names every topic it holds, not only the ones a first paint reads", () => {
    const said = harness({});
    for (const line of said) {
      assert.match(line, /^the screen asks about \S+, which the manifest does not declare$/u);
    }
    /* The sections these belong to draw nothing until their own reading
       arrives, so a check that waited for a paint would never reach
       them — and a topic renamed out from under one of them would show
       as a surface that merely has no evidence yet. */
    const named = said.map(nameOf);
    for (const topic of ["ivc.page", "dev.dma", "vgic.lr", "timer.queue"]) {
      assert.ok(named.includes(topic), `${topic} went unnamed`);
    }
  });

  it("says it once for a name, however often it is asked", () => {
    const named = harness({}).map(nameOf);
    assert.deepEqual([...new Set(named)], named);
  });

  it("says nothing about the topics the manifest declares", () => {
    /* Built from what the board itself named: writing the list out here
       would be the painters' table kept in two places. */
    const declared = Object.fromEntries(
      harness({}).map(nameOf).map((topic) => [topic, { rate: 20, asserted: false }]),
    );
    assert.deepEqual(harness(declared), []);
  });
});

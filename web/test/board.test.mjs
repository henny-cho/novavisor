/* The board against the manifest it draws from: a topic it names that
   the manifest does not declare reads exactly like one declared without
   a rate, so the badge looks ordinary over a surface that will never
   fill. The board says so and keeps drawing. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createBoard } from "../workbench/js/board.mjs";
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
  it("names a topic the manifest does not declare", () => {
    const said = harness({});
    /* What an initial paint asks for; the rest are named as their own
       sections paint. Whichever they are, none may go unsaid. */
    assert.ok(said.length > 0);
    for (const line of said) assert.match(line, /which the manifest does not declare$/u);
    assert.ok(said.some((line) => line.includes("sched.cpu")));
  });

  it("says it once for a name, however often it is asked", () => {
    const said = harness({});
    const names = said.map((line) => line.split(" ")[3]);
    assert.deepEqual([...new Set(names)], names);
  });

  it("says nothing about the topics the manifest declares", () => {
    const declared = Object.fromEntries(
      ["vm.generation", "sched.cpu"].map((topic) => [topic, { rate: 20, asserted: false }]),
    );
    assert.deepEqual(harness(declared), []);
  });
});

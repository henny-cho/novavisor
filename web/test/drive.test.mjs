/* The one panel that acts on the machine.

   It holds no list of opcodes: the run publishes a row per op it
   carries out, saying how many arguments it reads and what each one
   means and accepts, and the controls follow. So the questions worth
   asking are what it builds from a given answer, what it sends when
   pressed, and when it declines to build again. That last one is not
   cosmetic: the topology is republished whenever anything on it moves,
   and a rebuild on each would clear the INTID a reader had just typed. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createDrive } from "../workbench/js/drive.mjs";
import { element, find, findAll, fire, installDom } from "./dom.mjs";

const band = (kind, lo, hi, dflt = 0) => ({ kind, lo, hi, default: dflt, free: lo > hi });
const free = (kind = "plain") => band(kind, 1, 0);

const RUN = {
  command: {
    ops: [
      { name: "mark", code: 1, args: [free()] },
      { name: "spi", code: 2, args: [band("vm", 0, 1), band("plain", 32, 63, 32)] },
      { name: "slice", code: 3, args: [band("micros", 500, 1000, 500)] },
    ],
    period_us: 2000,
  },
  guests: [{ name: "vm0" }, { name: "vm1" }],
};

/* Where the SPI control's INTID input lands among the panel's numbers:
   mark's tag comes first, so this names the field rather than assuming
   the first one found is it. */
const intidField = (root) => findAll(root, "dnum")[1];

/* A fresh answer with the same content, which is what a republished
   topology looks like from here. */
const again = (over = {}) => ({
  ...structuredClone(RUN),
  command: { ...structuredClone(RUN.command), ...over },
});

function harness() {
  installDom();
  const root = element("div");
  const note = element("div");
  const sent = [];
  const drive = createDrive({ root, note, send: (data) => sent.push(data) });
  return { drive, root, note, sent };
}

const press = (root, label) =>
  fire(
    findAll(root, "btn").find((control) => control.textContent === label),
    "click",
  );

describe("drive panel", () => {
  it("builds a control for each op the run declares and no other", () => {
    const { drive, root } = harness();
    drive.setWorld(again({ ops: [RUN.command.ops[0]] }));

    assert.deepEqual(
      findAll(root, "dlabel").map((label) => label.textContent),
      ["표식"],
    );
    assert.equal(find(root, "pick"), null, "offered a control the run refuses");
  });

  it("builds a control for an op it was never taught", () => {
    /* The point of the rows: a firmware that added an opcode gets a
       control here without this file moving. Unlabelled, so it shows
       the machine's own word rather than a stale translation. */
    const { drive, root, sent } = harness();
    drive.setWorld(
      again({ ops: [{ name: "stop", code: 4, args: [band("vm", 0, 1)] }] }),
    );

    assert.deepEqual(
      findAll(root, "dlabel").map((label) => label.textContent),
      ["stop"],
    );
    find(root, "pick").value = "1";
    press(root, "stop");
    assert.deepEqual(sent.at(-1), { op: "stop", a: 1, b: 0 });
  });

  it("states the wait the run promised", () => {
    const { drive, note } = harness();
    drive.setWorld(RUN);
    assert.equal(note.textContent, "≤2 ms");
  });

  it("bounds the SPI control by the range the run declares", () => {
    const { drive, root, sent } = harness();
    drive.setWorld(RUN);

    const intid = intidField(root);
    assert.equal(intid.min, "32");
    assert.equal(intid.max, "63");
    assert.equal(intid.value, "32");

    press(root, "주입");
    assert.deepEqual(sent.at(-1), { op: "spi", a: 0, b: 32 });

    find(root, "pick").value = "1";
    intid.value = "40";
    press(root, "주입");
    assert.deepEqual(sent.at(-1), { op: "spi", a: 1, b: 40 });
  });

  it("offers the quantum's own band and starts at the value it booted with", () => {
    const { drive, root, sent } = harness();
    drive.setWorld(RUN);

    const quantum = findAll(root, "dnum")[2];
    assert.equal(quantum.min, "500");
    assert.equal(quantum.max, "1000");
    assert.equal(quantum.value, "500");

    quantum.value = "1000";
    press(root, "적용");
    assert.deepEqual(sent.at(-1), { op: "slice", a: 1000, b: 0 });
  });

  it("numbers each mark, so two of them are distinguishable", () => {
    /* The count follows the free tag, not the opcode: an argument with
       no band is a tag, and a tag nobody varies brackets nothing. */
    const { drive, root, sent } = harness();
    drive.setWorld(RUN);

    press(root, "남기기");
    press(root, "남기기");
    assert.deepEqual(sent, [
      { op: "mark", a: 1, b: 0 },
      { op: "mark", a: 2, b: 0 },
    ]);
  });

  it("keeps what a reader typed when the run repeats what it accepts", () => {
    const { drive, root } = harness();
    drive.setWorld(RUN);
    const intid = intidField(root);
    intid.value = "40";

    drive.setWorld(again());

    assert.equal(intidField(root), intid, "rebuilt on an unchanged answer");
    assert.equal(intidField(root).value, "40");
  });

  it("rebuilds when the run changes what it accepts", () => {
    const { drive, root } = harness();
    drive.setWorld(RUN);
    intidField(root).value = "40";

    const widened = structuredClone(RUN.command.ops);
    widened[1].args[1] = band("plain", 32, 95, 32);
    drive.setWorld(again({ ops: widened }));

    const intid = intidField(root);
    assert.equal(intid.max, "95");
    assert.equal(intid.value, "32", "kept a bound the run had moved");
  });

  it("says so when a run takes no commands at all", () => {
    const { drive, root, note } = harness();
    drive.setWorld(RUN);

    drive.setWorld({ guests: RUN.guests });

    assert.equal(root.hidden, true);
    assert.equal(findAll(root, "drow").length, 0);
    assert.equal(note.textContent, "이 실행은 명령을 받지 않는다");
  });
});

describe("drive verdict", () => {
  it("reports nothing until the machine answers", () => {
    const { drive, root, note } = harness();
    drive.setWorld(RUN);
    press(root, "남기기");
    assert.equal(note.textContent, "≤2 ms");
  });

  it("keeps the wait beside what the machine did with it", () => {
    const { drive, note } = harness();
    drive.setWorld(RUN);

    drive.answered({ op: "spi", a: 0, b: 33, result: "ok" });
    /* Trailing zeros dropped, interior ones kept: `spi 0 33` names VM 0,
       where `mark 1 0` says nothing the tag did not. */
    assert.equal(note.textContent, "≤2 ms · spi 0 33 → ok");
    assert.equal(note.classes.has("bad"), false);

    drive.answered({ op: "mark", a: 1, b: 0, result: "refused" });
    assert.equal(note.textContent, "≤2 ms · mark 1 → refused");
    assert.equal(note.classes.has("bad"), true);
  });

  it("ignores an answer from a run it is not driving", () => {
    const { drive, note } = harness();
    drive.answered({ op: "mark", a: 1, b: 0, result: "ok" });
    assert.equal(note.textContent, "");
  });
});

/* The one panel that acts on the machine.

   Everything it offers comes from the run — the opcodes, the quantum's
   band, the INTID range, the wait — so the questions worth asking of it
   are what it builds from a given answer, what it sends when pressed,
   and when it declines to build again. That last one is not cosmetic:
   the topology is republished whenever anything on it moves, and a
   rebuild on each would clear the INTID a reader had just typed. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createDrive } from "../workbench/js/drive.mjs";
import { element, find, findAll, fire, installDom } from "./dom.mjs";

const RUN = {
  command: {
    ops: ["mark", "spi", "slice"],
    slice_us: [500, 1000],
    spi_intids: [32, 63],
    period_us: 2000,
  },
  guests: [{ name: "vm0" }, { name: "vm1" }],
};

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
    drive.setWorld(again({ ops: ["mark"] }));

    assert.deepEqual(
      findAll(root, "dlabel").map((label) => label.textContent),
      ["표식"],
    );
    assert.equal(find(root, "dnum"), null, "offered a control the run refuses");
  });

  it("states the wait the run promised", () => {
    const { drive, note } = harness();
    drive.setWorld(RUN);
    assert.equal(note.textContent, "≤2 ms");
  });

  it("bounds the SPI control by the range the run declares", () => {
    const { drive, root, sent } = harness();
    drive.setWorld(RUN);

    const intid = find(root, "dnum");
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

  it("offers the quantum's own band and sends one of it", () => {
    const { drive, root, sent } = harness();
    drive.setWorld(RUN);

    press(root, "500 us");
    assert.deepEqual(sent.at(-1), { op: "slice", a: 500, b: 0 });
    press(root, "1 ms");
    assert.deepEqual(sent.at(-1), { op: "slice", a: 1000, b: 0 });
  });

  it("numbers each mark, so two of them are distinguishable", () => {
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
    const intid = find(root, "dnum");
    intid.value = "40";

    drive.setWorld(again());

    assert.equal(find(root, "dnum"), intid, "rebuilt on an unchanged answer");
    assert.equal(find(root, "dnum").value, "40");
  });

  it("rebuilds when the run changes what it accepts", () => {
    const { drive, root } = harness();
    drive.setWorld(RUN);
    find(root, "dnum").value = "40";

    drive.setWorld(again({ spi_intids: [32, 95] }));

    const intid = find(root, "dnum");
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

/* A verification step names its own kind, so a build that does not know
   a kind still prints something a reader can act on. And the one place
   counter ticks become a duration: four callers draw one, and four
   divisions would be four chances to use the wrong clock. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { describeStep, ecName, elapsed, micros, sealedFields } from "../workbench/js/format.mjs";

describe("describeStep", () => {
  it("reads a console step as its pattern", () => {
    assert.equal(
      describeStep({ kind: "pattern", subject: "demo_exit code=0" }),
      "/demo_exit code=0/",
    );
  });

  it("names a kind it has no phrasing for", () => {
    assert.equal(
      describeStep({ kind: "observe", subject: "smmu.stream" }),
      "observe smmu.stream",
    );
  });

  it("never renders blank", () => {
    assert.equal(describeStep({}), "?");
    assert.equal(describeStep(), "?");
  });
});

describe("micros", () => {
  it("turns counter ticks into microseconds at the firmware's rate", () => {
    assert.equal(micros(62_500, 62_500_000), 1000);
    assert.equal(micros(1, 1_000_000), 1);
  });

  it("refuses to guess before the rate is known", () => {
    /* Ticks drawn as a duration would be wrong by whatever the clock
       turns out to be, which is a factor of 25 on this board. */
    assert.equal(micros(62_500, 0), null);
    assert.equal(micros(62_500, undefined), null);
  });

  it("keeps a direction, so a caller can say which way", () => {
    assert.equal(micros(-62_500, 62_500_000), -1000);
  });
});

describe("elapsed", () => {
  it("reads microseconds below a millisecond and milliseconds above", () => {
    assert.equal(elapsed(812), "812us");
    assert.equal(elapsed(1400), "1.4ms");
  });

  it("is unsigned: the direction belongs to whoever shows it", () => {
    assert.equal(elapsed(-1400), "1.4ms");
  });
});

describe("ecName", () => {
  it("reads a firmware identifier without its k", () => {
    assert.equal(ecName({ esr_ec: { 22: "kHvcAa64" } }, 22), "HvcAa64");
  });

  it("names nothing for a class the build did not describe", () => {
    assert.equal(ecName({}, 22), "");
    assert.equal(ecName(undefined, 22), "");
  });
});

/* One sealed run, as the bridge states it: a trap row broken down by its
   EC, a late timer row broken down by its slot, and a lifecycle span
   whose quantile is the only thing on screen that says how long a reset
   took. The catalogue and both vocabularies are the topology's. */
const STOPS = [
  { code: 1, id: "trap" },
  { code: 18, id: "timer.late" },
  { code: 20, id: "vm.lifecycle" },
];
const TAXONOMY = { esr_ec: { 22: "kHvcAa64" } };
const SLOTS = ["watchdog vm0", "lifecycle vm0"];
const SEALED = {
  phase: "run-sealed",
  run_id: 2,
  freq_hz: 62_500_000,
  complete: true,
  lost: 0,
  tail_drained: true,
  producer_dead: true,
  absent: false,
  events: { "1:22": 314, "18:1": 2, "20:0": 1 },
  latency: { "18:1": 62_500, "20:0": 1_250_000 },
};

describe("sealedFields", () => {
  it("names each counted key by the catalogue and its own vocabulary", () => {
    assert.deepEqual(sealedFields(SEALED, STOPS, TAXONOMY, SLOTS), {
      트레이스: "완전",
      "trap HvcAa64": 314,
      "timer.late lifecycle vm0": 2,
      "vm.lifecycle vm0": 1,
      "timer.late lifecycle vm0 p99.9": "1.0ms",
      "vm.lifecycle vm0 p99.9": "20.0ms",
    });
  });

  it("says what an incomplete run lost, and why it is incomplete", () => {
    const fields = sealedFields(
      { ...SEALED, complete: false, lost: 7, tail_drained: false, events: {}, latency: {} },
      STOPS,
      TAXONOMY,
      SLOTS,
    );
    assert.deepEqual(fields, { 트레이스: "불완전", 유실: "7건", 꼬리: "미회수" });
  });

  it("keeps a quantile in ticks until the clock is known", () => {
    const fields = sealedFields({ ...SEALED, freq_hz: 0, events: {} }, STOPS, TAXONOMY, SLOTS);
    assert.equal(fields["vm.lifecycle vm0 p99.9"], "1250000틱");
  });

  it("keeps a key it has no catalogue or vocabulary for", () => {
    const fields = sealedFields(
      { ...SEALED, events: { "9:27": 4, "99:0": 1 }, latency: {} },
      STOPS,
      TAXONOMY,
      SLOTS,
    );
    assert.deepEqual(fields, { 트레이스: "완전", "code 9 27": 4, "code 99": 1 });
  });
});

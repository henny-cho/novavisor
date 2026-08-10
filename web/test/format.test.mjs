/* A verification step names its own kind, so a build that does not know
   a kind still prints something a reader can act on. And the one place
   counter ticks become a duration: four callers draw one, and four
   divisions would be four chances to use the wrong clock. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { describeStep, elapsed, micros } from "../workbench/js/format.mjs";

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

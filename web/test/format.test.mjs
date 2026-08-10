/* A verification step names its own kind, so a build that does not know
   a kind still prints something a reader can act on. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { describeStep } from "../workbench/js/format.mjs";

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

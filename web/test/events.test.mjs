/* Two reasons to hide a row, kept apart.

   The board narrows the log to the badges a path is evidence for; the
   reader switches badges off for their own reasons. Folded into one
   set, dropping the board's narrowing would restore chips the reader
   had muted themselves — state lost silently, and it reads as the log
   misbehaving rather than the board. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createEvents } from "../workbench/js/events.mjs";
import { element, findAll, fire, installDom } from "./dom.mjs";

function harness() {
  installDom();
  const list = element("div");
  const filters = element("div");
  const resetButton = element("button");
  const clearButton = element("button");
  const events = createEvents({ list, filters, resetButton, clearButton });
  events.setBadges(["GIC", "SMP"]);
  events.addEvent(1e9, { badge: "GIC", message: "spi 33" });
  events.addEvent(2e9, { badge: "SMP", message: "cross" });
  events.addNotice(3e9, "연결됨");
  return { events, list, filters, resetButton, clearButton };
}

const chip = (filters, name) =>
  findAll(filters, "fchip").find((found) => found.textContent === name);

const showing = (list) =>
  list.children.filter((row) => !row.hidden).map((row) => row.dataset.badge);

describe("event log filters", () => {
  it("keeps the board's narrowing out of the reader's muting", () => {
    const { events, list, filters, resetButton } = harness();
    assert.deepEqual(showing(list), ["GIC", "SMP", "LIFE"]);

    fire(chip(filters, "SMP"), "click");
    assert.deepEqual(showing(list), ["GIC", "LIFE"]);
    assert.equal(chip(filters, "SMP").getAttribute("aria-pressed"), "false");

    events.narrow(["SMP"]);
    assert.deepEqual(showing(list), [], "narrowing consulted only one layer");
    assert.equal(filters.classes.has("narrowed"), true);

    /* Dropping the narrowing restores what the board hid, and nothing
       the reader hid. */
    events.narrow(null);
    assert.deepEqual(showing(list), ["GIC", "LIFE"], "narrowing wrote the reader's filter");
    assert.equal(filters.classes.has("narrowed"), false);

    fire(resetButton, "click");
    assert.deepEqual(showing(list), ["GIC", "SMP", "LIFE"]);
  });

  it("does not leave a vanished badge's rows hidden", () => {
    const { events, list, filters } = harness();
    fire(chip(filters, "SMP"), "click");
    assert.deepEqual(showing(list), ["GIC", "LIFE"]);

    /* A new run publishes a vocabulary without it; its chip is gone, so
       there is nothing left to switch it back on with. */
    events.setBadges(["GIC"]);
    assert.equal(chip(filters, "SMP"), undefined);
    assert.deepEqual(showing(list), ["GIC", "SMP", "LIFE"]);
  });

  it("cuts at the cursor without cutting this session's own remarks", () => {
    const { events, list } = harness();
    events.cutAt(1.5e9);
    /* The notice has no run time: it is not a moment in the run. */
    assert.deepEqual(showing(list), ["GIC", "LIFE"]);

    events.cutAt(null);
    assert.deepEqual(showing(list), ["GIC", "SMP", "LIFE"]);
  });
});

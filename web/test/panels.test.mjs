/* Provenance in the measurement drawer.

   A stop's whole product is what moved, so a cell drawn from a bare
   number has already thrown it away — it would render perfectly and
   silently never light up. The arrangement that prevents it is a mask
   shaped like the value, walked beside it, and a table that refuses
   anything else. Both halves are exercised here, and so is the third:
   that the refusal reaches a reader rather than being filed away as a
   value the drawer could not decode. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createPanels } from "../workbench/js/panels.mjs";
import { BareCell, Cursor, plain, table } from "../workbench/js/primitives/table.mjs";
import { element, find, findAll, fire, installDom, movedIn, rowsOf } from "./dom.mjs";

function harness() {
  const document = installDom();
  const tabs = element("div");
  const host = element("div");
  return { panels: createPanels({ tabs, host }), tabs, host, document };
}

/* Two cores, and one field that moved between the last two stops. */
const SCHED = {
  kind: "snapshot",
  topic: "sched.cpu",
  ts: 1e9,
  src: "S",
  data: {
    values: [
      { current: 3, fp: true, fp_trap: false, idling: false },
      { current: 7, fp: false, fp_trap: false, idling: true },
    ],
    changed: { 1: { current: true } },
  },
};

const chipNamed = (tabs, title) =>
  tabs.children.find((chip) => chip.textContent.startsWith(title));

describe("provenance kit", () => {
  it("refuses a cell that arrived without its mask", () => {
    installDom();
    assert.throws(() => table(["value"], [[3]]), TypeError);
    assert.throws(() => table(["value"], [["0x40000000"]]), TypeError);
    assert.throws(() => table(["value"], [[{ shown: 3, moved: true }]]), TypeError);
  });

  it("takes a reading or a cell that says out loud it was computed", () => {
    installDom();
    const node = table(["#", "value"], [[plain(0), new Cursor(41, true)]]);
    assert.deepEqual(rowsOf(node), [["0", "41"]]);
    assert.deepEqual(movedIn(node), ["41"], "a computed cell claimed provenance");
  });

  it("descends the mask by the same key as the value", () => {
    const cursor = new Cursor(
      { cpu: [{ current: 3 }, { current: 7 }] },
      { cpu: { 1: { current: true } } },
    );
    const cores = cursor.get("cpu");
    assert.equal(cores.get(1).get("current").shown, 7);
    assert.equal(cores.get(1).get("current").moved, true);
    assert.equal(cores.get(0).get("current").moved, false);
    /* An index has to reach the mask as the string key the bridge sent,
       which is why rows() is not a plain map(). */
    assert.deepEqual(
      cores.rows().map((core) => core.get("current").moved),
      [false, true],
    );
    assert.deepEqual(cursor.keys(), ["cpu"]);
  });

  it("marks everything under a node that changed shape", () => {
    const cursor = new Cursor({ ring: { widx: 2, slots: [1] } }, { ring: true });
    assert.equal(cursor.get("ring").moved, true);
    assert.equal(cursor.get("ring").get("widx").moved, true);
    assert.equal(cursor.get("ring").get("slots").get(0).moved, true);
  });
});

describe("panel drawer", () => {
  it("lights the cells the mask named and no others", () => {
    const { panels, host } = harness();
    panels.apply(SCHED);
    panels.settle();

    const cores = findAll(host, "ptable")[0];
    assert.deepEqual(rowsOf(cores), [
      ["0", "3", "●", "·", "·"],
      ["1", "7", "·", "·", "●"],
    ]);
    /* The row number is computed here, so it never lights however much
       the reading beside it moved. */
    assert.deepEqual(movedIn(cores), ["7"]);
  });

  it("counts the moved leaves on the tab, so a shut drawer says to open", () => {
    const { panels, tabs } = harness();
    panels.apply(SCHED);
    panels.settle();

    const chip = chipNamed(tabs, "Scheduler");
    assert.equal(find(chip, "tmoved").textContent, "1");
    assert.equal(chip.classes.has("moved"), true);

    /* A delta belongs to the pair of stops it was measured across. */
    panels.clearMoved();
    assert.equal(find(chip, "tmoved").textContent, "");
    assert.equal(chip.classes.has("moved"), false);
  });

  it("draws nothing for a topic the run had not read yet", () => {
    const { panels, host } = harness();
    panels.apply(SCHED);
    panels.settle();

    panels.setUnread(["sched.cpu"]);
    panels.settle();
    assert.deepEqual(rowsOf(findAll(host, "ptable")[0]), []);

    /* Held rather than dropped: moving the cursor back costs nothing. */
    panels.setUnread([]);
    panels.settle();
    assert.equal(rowsOf(findAll(host, "ptable")[0]).length, 2);
  });

  it("hands an unclaimed topic to a panel fed by the manifest", () => {
    const { panels, tabs, host } = harness();
    const fallback = chipNamed(tabs, "기타");
    assert.equal(fallback.hidden, true, "unclaimed before a manifest said so");

    panels.setTopology({
      observations: { "sched.cpu": {}, "novel.thing": {} },
      timer_slots: [],
    });
    assert.equal(fallback.hidden, false);
    assert.equal(panels.accepts("novel.thing"), true);

    panels.apply({
      kind: "snapshot",
      topic: "novel.thing",
      ts: 2e9,
      src: "S",
      data: { values: { alpha: 1, beta: 2 }, changed: { beta: true } },
    });
    fire(fallback, "click");

    assert.equal(fallback.getAttribute("aria-pressed"), "true");
    const shown = findAll(host, "ptable").at(-1);
    assert.deepEqual(rowsOf(shown), [
      ["alpha", "1"],
      ["beta", "2"],
    ]);
    assert.deepEqual(movedIn(shown), ["2"]);
  });

  it("only accepts a snapshot", () => {
    const { panels, host } = harness();
    panels.apply({ ...SCHED, kind: "delta" });
    panels.settle();
    assert.equal(findAll(host, "ptable").length, 0);
  });
});

describe("panel faults", () => {
  it("does not file a renderer's own fault as an unreadable value", () => {
    const { panels, host, document } = harness();
    panels.apply(SCHED);
    /* A cell with no provenance is this file's bug, not the machine's,
       and it says so — while still leaving the drawer able to draw the
       next batch. */
    document.failOn("table", new BareCell("table cell is neither a cursor nor plain(): 3"));

    panels.settle();
    assert.match(host.textContent, /출처를 잃었다/);
    assert.doesNotMatch(host.textContent, /그리지 못했다/);
  });

  it("keeps drawing after a panel faulted", () => {
    const { panels, host, document } = harness();
    panels.apply(SCHED);
    document.failOn("table", new BareCell("bare"));
    panels.settle();

    /* The fault must not latch: a throw escaping settle() would leave
       the dirty set uncleared and refault on every batch from here on. */
    panels.apply(SCHED);
    panels.settle();
    assert.ok(findAll(host, "ptable").length > 0);
  });

  it("names what failed instead of blaming the machine for it", () => {
    const { panels, host, document } = harness();
    panels.apply(SCHED);
    document.failOn("table", new RangeError("decoded out of live guest RAM"));

    panels.settle();
    /* The drawer cannot know whether a throw came from the reading or
       from its own code, so it prints the throw. A fixed sentence about
       an unreadable value would file this file's own bugs — a missing
       import among them — as the machine's. */
    assert.match(host.textContent, /decoded out of live guest RAM/);
  });
});

/* Where a reading sits on the firmware's own clock.

   The publisher stamps every slot with the counter the trace records
   carry, precisely so a reading can be placed against the events around
   it. The arrival time answers a different question — when this process
   got to it — and a drawer that shows only that leaves the reader
   comparing two different quantities. */
describe("readings on the machine's clock", () => {
  const stamped = (topic, at) => ({
    kind: "snapshot",
    topic,
    ts: 1e9,
    src: "S",
    data: { values: [{ current: 1, fp: false, fp_trap: false, idling: false }], ts: at },
  });

  const header = (host) => find(host, "pfresh").textContent;

  it("places a reading against the newest one held", () => {
    const { panels, host } = harness();
    panels.setClock(1e6); // a microsecond a tick, so the arithmetic is readable
    panels.apply(stamped("sched.cpu", 5_000_000));
    panels.settle();
    // The only reading held is the newest, so it sits on the reference.
    assert.match(header(host), /최신 \+0us/);
  });

  it("places it against the mark a reader selected", () => {
    const { panels, host } = harness();
    panels.setClock(1e6);
    panels.apply(stamped("sched.cpu", 5_000_000));
    panels.settle();
    panels.setReference(4_998_000); // the mark is 2ms earlier
    assert.match(header(host), /선택 \+2\.0ms/);
    panels.setReference(5_003_500); // and now 3.5ms later
    assert.match(header(host), /선택 -3\.5ms/);
  });

  it("falls back to arrival where nothing stamped the reading", () => {
    const { panels, host } = harness();
    panels.setClock(1e6);
    panels.apply(SCHED); // a provider with no publisher behind it
    panels.settle();
    assert.doesNotMatch(header(host), /최신|선택/);
  });

  it("says nothing about a clock it has not been told", () => {
    const { panels, host } = harness();
    panels.apply(stamped("sched.cpu", 5_000_000));
    panels.settle();
    // A difference between two counter values is not a duration until
    // the rate arrives with the trace summary.
    assert.doesNotMatch(header(host), /최신|선택/);
  });
})

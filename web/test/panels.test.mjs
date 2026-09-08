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
import { element, find, findAll, fire, installDom, movedIn, rowsOf, walk } from "./dom.mjs";

/* `open` is what a previous session left in storage, which is the only
   way into the restore path. */
function harness(open) {
  const document = installDom();
  if (open) localStorage.setItem("nv-wb-panels", JSON.stringify(open));
  const tabs = element("div");
  const host = element("div");
  return { panels: createPanels({ tabs, host }), tabs, host, document };
}

const snapshot = (topic, values, changed) => ({
  kind: "snapshot",
  topic,
  ts: 2e9,
  src: "S",
  data: { values, ...(changed ? { changed } : {}) },
});

const titles = (host, className) =>
  findAll(host, className).map((node) => node.textContent);

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

  it("only accepts a snapshot", () => {
    const { panels, host } = harness();
    panels.apply({ ...SCHED, kind: "delta" });
    panels.settle();
    assert.equal(findAll(host, "ptable").length, 0);
  });
});

/* Where a reading is drawn.

   The drawer a topic belongs to is its own name: the manifest spells
   every topic `subsystem.thing`. What used to be here instead was a
   hand-written topic-to-panel table, and nine topics the bridge had
   added since sat in a fallback drawer as JSON — the list was the gap.
   So what is tested is the derivation, and the two things it must not
   lose: a drawer's own drawing, and the frames that drawing reads from
   elsewhere. */
describe("drawers from the manifest", () => {
  const TOPO = (observations) => ({ observations, timer_slots: [] });

  it("names a drawer after every prefix the manifest publishes", () => {
    const { panels, tabs } = harness();
    panels.setTopology(TOPO({ "sched.cpu": {}, "vgic.lr": {}, "novel.thing": {} }));

    /* The hand-written drawers keep their titles and their order;
       everything else the bridge publishes follows, named after itself.
       Sysreg is the one declared drawer — it is a protocol topic, not
       an observation, so no prefix would ever derive it. */
    assert.deepEqual(
      tabs.children.map((chip) => chip.textContent),
      ["Scheduler", "Timer", "Context", "IVC", "PSCI·SMP", "Devices", "Sysreg", "novel", "vgic"],
    );
    assert.equal(panels.accepts("novel.thing"), true);
  });

  it("draws the topic that dates another one as no row at all", () => {
    const { panels, tabs, host } = harness();
    panels.setTopology(TOPO({ "novel.thing": { as_of: "novel.stamp" }, "novel.stamp": {} }));
    fire(chipNamed(tabs, "novel"), "click");
    panels.apply(snapshot("novel.thing", { alpha: 1 }));
    panels.apply(snapshot("novel.stamp", [{ synced_at: 5 }]));
    panels.settle();

    /* The header already says how old the reading is; the stamp behind
       that is not a second row. It still reaches the drawer, which is
       what the age and the placement are computed from. */
    assert.deepEqual(titles(host, "psec-h"), ["novel.thing"]);
    assert.equal(panels.accepts("novel.stamp"), true);
  });

  it("draws an override's own picture and then whatever it left", () => {
    const { panels, host } = harness(["ctx"]);
    panels.setTopology(
      TOPO({
        "ctx.trap": { as_of: "ctx.synced" },
        "ctx.el1": { as_of: "ctx.synced" },
        "ctx.synced": {},
        "ctx.syndrome": { as_of: "ctx.synced" },
      }),
    );
    panels.apply(snapshot("ctx.el1", [{ el1: { sctlr: "0x1" } }]));
    panels.apply(snapshot("ctx.syndrome", [{ ec: 36, far: "0x9000000" }]));
    panels.settle();

    /* The slot picker is the override speaking; the syndrome is a topic
       no renderer claims, and it lands under `ctx` structured rather
       than in a fallback drawer. */
    const order = walk(host).filter(
      (node) => node.classes.has("pslots") || node.classes.has("psec-h"),
    );
    assert.equal(order[0].classes.has("pslots"), true);
    assert.equal(order.at(-1).textContent, "ctx.syndrome");
    assert.deepEqual(rowsOf(findAll(host, "ptable").at(-1)), [["0", "36", "0x9000000"]]);
  });

  it("redraws a drawer for the topic its override reads elsewhere", () => {
    const { panels, host } = harness(["ctx"]);
    panels.setTopology(TOPO({ "ctx.el1": {}, "sched.valid": {} }));
    panels.apply(snapshot("ctx.el1", [{ el1: { sctlr: "0x1" } }, { el1: { sctlr: "0x2" } }]));
    panels.settle();
    assert.equal(findAll(host, "pslot").length, 2);
    assert.deepEqual(findAll(host, "pslot").map((pick) => pick.classes.has("off")), [false, false]);

    /* `sched.valid` belongs to another drawer, and Context reads it to
       mark a slot that holds no vCPU. A drawer watching only its own
       prefix would sit still on this frame — the regression this
       union exists to prevent. */
    panels.apply(snapshot("sched.valid", [false, false]));
    panels.settle();
    assert.deepEqual(findAll(host, "pslot").map((pick) => pick.classes.has("off")), [true, true]);
  });

  it("flattens a list per core into one table", () => {
    const { panels, tabs, host } = harness();
    panels.setTopology(TOPO({ "novel.lr": {} }));
    fire(chipNamed(tabs, "novel"), "click");
    panels.apply(
      snapshot("novel.lr", [[{ slot: 0, vintid: 27 }], [{ slot: 2, vintid: 33 }]], {
        1: { 0: { vintid: true } },
      }),
    );
    panels.settle();

    /* The outer index is the core, and it is computed here, so it never
       lights however much the reading beside it moved. */
    const shown = findAll(host, "ptable").at(-1);
    assert.deepEqual(rowsOf(shown), [
      ["0", "0", "27"],
      ["1", "2", "33"],
    ]);
    assert.deepEqual(movedIn(shown), ["33"]);
  });

  it("keeps a list of plain values as itself", () => {
    const { panels, tabs, host } = harness();
    panels.setTopology(TOPO({ "novel.programmed": {} }));
    fire(chipNamed(tabs, "novel"), "click");
    panels.apply(snapshot("novel.programmed", ["0x5000", "0x6000"]));
    panels.settle();

    // A per-core u64 has no fields to spread into columns.
    assert.deepEqual(rowsOf(findAll(host, "ptable").at(-1)), [
      ["0", "0x5000"],
      ["1", "0x6000"],
    ]);
  });

  it("rebuilds the strip only when the set of drawers changes", () => {
    const { panels, tabs, host } = harness(["ctx"]);
    panels.setTopology(TOPO({ "ctx.el1": {} }));
    const before = chipNamed(tabs, "Context");
    assert.equal(before.getAttribute("aria-pressed"), "true");

    /* A topology is republished whenever anything on it moves. Same
       drawers, same strip — and the same chip, so nothing a reader had
       open or hovered is thrown away. */
    panels.setTopology(TOPO({ "ctx.el1": {}, "ctx.trap": {} }));
    assert.equal(chipNamed(tabs, "Context"), before);

    panels.setTopology(TOPO({ "ctx.el1": {}, "novel.thing": {} }));
    assert.notEqual(chipNamed(tabs, "Context"), before, "a new drawer rebuilt the strip");
    assert.equal(chipNamed(tabs, "Context").getAttribute("aria-pressed"), "true");
    /* The placeholder sits after the bodies, so a rebuilt host still
       reads top-to-bottom in drawer order. */
    assert.equal(host.children.at(-1).classes.has("pnote"), true);
  });

  it("drops a persisted drawer that no longer exists", () => {
    const { panels, tabs } = harness(["other", "ctx"]);
    assert.equal(chipNamed(tabs, "기타"), undefined);
    assert.equal(chipNamed(tabs, "Context").getAttribute("aria-pressed"), "true");

    /* One that does not exist *yet* is a different answer: the derived
       drawers arrive with the topology, and the choice waits for them. */
    panels.setTopology(TOPO({ "novel.thing": {} }));
    assert.equal(chipNamed(tabs, "Context").getAttribute("aria-pressed"), "true");
    assert.equal(chipNamed(tabs, "novel").getAttribute("aria-pressed"), "false");
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

/* The address view: what an answer draws.

   Three of the things drawn here had never been drawn — the hop through
   the translation beneath, the recheck that says a chain moved, and the
   age of the root a live walk started from. The lane exists; this view
   was simply not in it, so a renderer could be written and never run.

   Nothing here decodes a descriptor or knows where a level's index
   sits: the answers below are shaped the way the bridge shapes them,
   and what is checked is what a reader ends up looking at. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createMemory } from "../workbench/js/memory.mjs";
import { element, find, findAll, installDom } from "./dom.mjs";

/* A machine's counter, the same rate the board declares. */
const HZ = 62_500_000;

function harness() {
  installDom();
  const parts = {
    pick: element("div"),
    form: element("form"),
    input: element("input"),
    note: element("div"),
    body: element("div"),
  };
  const asked = [];
  const memory = createMemory({ ...parts, request: (data) => asked.push(data) });
  return { memory, asked, ...parts };
}

const GUEST = {
  id: "vm0.v0.el1.low",
  label: "VM 0 · vCPU 0 · EL1 low",
  role: "el1.low",
  ground: "live",
};
const CPU = { id: "vm0.cpu", label: "VM 0 · CPU", role: "cpu", ground: "captured" };

/* One address walked in a guest's own tables, and where the translation
   beneath puts the IPA that came out. */
const CLOSED = {
  regime: GUEST.id,
  ground: "live",
  root: "0x100000",
  probe: {
    address: "0xffff8000807c12a4",
    level: 3,
    fault: "",
    output: "0x507c12a4",
    steps: [{ level: 0, index: 511, table: "0x100000", kind: "table" }],
    w: true,
    x: false,
    memory: "normal",
  },
  through: {
    regime: "vm0.cpu",
    label: "VM 0 · CPU",
    probe: {
      address: "0x507c12a4",
      level: 2,
      fault: "",
      output: "0x50fc12a4",
      steps: [],
      w: true,
      x: false,
      memory: "normal",
    },
  },
  moving: false,
  beside: [],
};

const world = (memory, regimes) => memory.setWorld({ regimes });

describe("address view: the chain", () => {
  it("shows the hop through the translation beneath", () => {
    /* Half an answer is where the guest's own tables ended. The IPA is
       an input to the translation under it, and a reader holding the two
       apart is a reader doing the second walk. */
    const { memory, body } = harness();
    world(memory, [GUEST]);
    memory.answer(CLOSED);

    const hop = find(body, "mbeside");
    assert.ok(hop, "the hop beneath was not drawn");
    assert.match(hop.textContent, /VM 0 · CPU/);
    assert.match(hop.textContent, /0x507c12a4 → 0x50fc12a4/);
  });

  it("marks the hop as bad when the translation beneath faults", () => {
    const { memory, body } = harness();
    world(memory, [GUEST]);
    memory.answer({
      ...CLOSED,
      through: {
        ...CLOSED.through,
        probe: { ...CLOSED.through.probe, fault: "unmapped", output: null, level: 1 },
      },
    });

    const hop = find(body, "mbeside");
    assert.ok(hop.className.includes("bad"));
    assert.match(hop.textContent, /unmapped/);
  });
});

describe("address view: the recheck", () => {
  it("says a chain held still", () => {
    const { memory, body } = harness();
    world(memory, [GUEST]);
    memory.answer(CLOSED);
    assert.ok(findAll(body, "mnote").some((node) => node.textContent.includes("두 번 걸어")));
  });

  it("warns when the tables moved under the walk", () => {
    /* Not a retry: how many tries would settle it is not a number
       anyone knows, so the reading is that it moved. */
    const { memory, body } = harness();
    world(memory, [GUEST]);
    memory.answer({ ...CLOSED, moving: true });

    const warned = findAll(body, "mwarn");
    assert.ok(warned.some((node) => node.textContent.includes("두 순간이 섞였다")));
  });

  it("says nothing about movement for a regime that does not move", () => {
    const { memory, body } = harness();
    world(memory, [CPU]);
    memory.answer({ regime: CPU.id, ground: "captured", root: "0x40000000", tree: { nodes: [] } });
    assert.equal(findAll(body, "mwarn").length, 0);
  });
});

describe("address view: the age of the root", () => {
  const ROOTED = { ...CLOSED, rooted: { at: 1_000_000, as_of: "ctx.synced", slot: 0 } };
  const synced = (values) => ({ topic: "ctx.synced", kind: "snapshot", data: { values } });

  it("dates the root against the slot the answer names", () => {
    /* A guest's root is a shadow of a register, refreshed when the
       guest traps. An answer that showed only where it landed would be
       presenting a copy as the present. */
    const { memory, body } = harness();
    memory.setClock(HZ);
    world(memory, [GUEST]);
    memory.apply(synced([{ synced_at: 1_000_000 - 62_500 }]));
    memory.answer(ROOTED);

    assert.ok(
      findAll(body, "mnote").some((node) => node.textContent === "뿌리는 1.0ms 전 사본"),
      findAll(body, "mnote").map((node) => node.textContent),
    );
  });

  it("says nothing before the counter's rate is known", () => {
    /* Ticks drawn as a duration would be wrong by whatever the clock
       turns out to be. */
    const { memory, body } = harness();
    world(memory, [GUEST]);
    memory.apply(synced([{ synced_at: 1_000_000 - 62_500 }]));
    memory.answer(ROOTED);
    assert.ok(!findAll(body, "mnote").some((node) => node.textContent.includes("뿌리는")));
  });

  it("says nothing for a slot that has never been synced", () => {
    const { memory, body } = harness();
    memory.setClock(HZ);
    world(memory, [GUEST]);
    memory.apply(synced([{ synced_at: 0 }]));
    memory.answer(ROOTED);
    assert.ok(!findAll(body, "mnote").some((node) => node.textContent.includes("뿌리는")));
  });

  it("says nothing for an answer that carries no age", () => {
    const { memory, body } = harness();
    memory.setClock(HZ);
    world(memory, [GUEST]);
    memory.apply(synced([{ synced_at: 1 }]));
    memory.answer(CLOSED);
    assert.ok(!findAll(body, "mnote").some((node) => node.textContent.includes("뿌리는")));
  });
});

describe("address view: an empty tree", () => {
  it("says a live regime has no map rather than no mappings", () => {
    /* Two different statements. "No mappings" is about the tables; a
       regime walked as it is asked has no map to make it with, and
       saying the first about the second is the claim this whole path
       exists to prevent. */
    const { memory, body } = harness();
    world(memory, [GUEST]);
    memory.answer(CLOSED);
    assert.match(find(body, "mempty").textContent, /지도는 없다/);
  });

  it("says a captured regime with no rows has none", () => {
    const { memory, body } = harness();
    world(memory, [CPU]);
    memory.answer({ regime: CPU.id, ground: "captured", root: "0x40000000", tree: { nodes: [] } });
    assert.match(find(body, "mempty").textContent, /매핑된 구간이 없다/);
  });

  it("says a run that published no tables published none", () => {
    const { memory, note, asked } = harness();
    world(memory, []);
    assert.match(note.textContent, /페이지 테이블/);
    assert.equal(asked.length, 0, "nothing to ask about");
  });
});

describe("address view: what it asks for", () => {
  it("asks about the first regime as soon as a run publishes one", () => {
    const { memory, asked } = harness();
    world(memory, [GUEST, CPU]);
    assert.deepEqual(asked, [{ regime: GUEST.id, address: "" }]);
  });

  it("ignores an answer about a regime the reader has moved off", () => {
    const { memory, body } = harness();
    world(memory, [GUEST, CPU]);
    memory.answer({ ...CLOSED, regime: CPU.id });
    assert.equal(find(body, "mbeside"), null);
  });
});

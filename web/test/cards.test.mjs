/* VM card test: the cards a topology mints, and what one console line
   does to the card it belongs to. The slot rule is the console's — a
   line claiming a slot the board cannot host is guest text that looks
   tagged — so it is stated here against the other thing it mints. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createCards } from "../workbench/js/cards.mjs";
import { setGuestSlots } from "../workbench/js/format.mjs";
import { element, find, findAll, installDom } from "./dom.mjs";

const GUESTS = [
  { name: "linux", vcpus: 2 },
  { name: "zephyr", vcpus: 1 },
];

function harness({ slots = 4 } = {}) {
  installDom();
  setGuestSlots({ max_guests: slots });
  const root = element("div");
  return { view: createCards(root), root };
}

const cards = (root) => findAll(root, "card");
const partOf = (card, className) => find(card, className).textContent;

describe("vm cards", () => {
  it("mints one card per guest the topology names", () => {
    const { view, root } = harness();
    view.setGuests(GUESTS);

    const minted = cards(root);
    assert.equal(minted.length, 2);
    assert.deepEqual(minted.map((card) => partOf(card, "cvm")), ["vm0", "vm1"]);
    assert.deepEqual(minted.map((card) => partOf(card, "cnm")), ["linux", "zephyr"]);
    assert.deepEqual(minted.map((card) => partOf(card, "cvc")), ["vCPU 2", "vCPU 1"]);
    /* Nothing is inferred: a guest that has not printed says so. */
    assert.deepEqual(minted.map((card) => partOf(card, "cc")), ["0줄", "0줄"]);
  });

  it("marks the card a console line belongs to", () => {
    const { view, root } = harness();
    view.setGuests(GUESTS);

    view.touch(1, "zephyr boot banner");
    const [first, second] = cards(root);
    assert.equal(second.classes.has("act"), true);
    assert.equal(partOf(second, "cl"), "zephyr boot banner");
    assert.equal(partOf(second, "cc"), "1줄");
    /* The line belongs to one guest, and only that one moved. */
    assert.equal(first.classes.has("act"), false);
    assert.equal(partOf(first, "cc"), "0줄");
  });

  it("mints nothing for a slot the board cannot host", () => {
    const { view, root } = harness({ slots: 2 });
    view.setGuests(GUESTS);

    view.touch(5, "[vm5] a line that only looks tagged");
    assert.equal(cards(root).length, 2);
  });

  it("keeps the cards and starts the counters over at a run boundary", () => {
    const { view, root } = harness();
    view.setGuests(GUESTS);
    view.touch(0, "linux kernel started");
    view.touch(0, "and a second line");

    view.reset();
    const minted = cards(root);
    assert.equal(minted.length, 2);
    assert.deepEqual(minted.map((card) => partOf(card, "cc")), ["0줄", "0줄"]);
    assert.equal(partOf(minted[0], "cl"), "출력 없음");
    assert.equal(minted[0].classes.has("act"), false);
  });
});

/* The cursor over the trace strip: one selection, moved four ways.

   A click, an arrow key, a tour and playback all push the same cursor,
   and everything downstream of a selection — the caption, the board
   focus, the grade badge — hangs off that one announcement. These
   exercise the module rather than read it: the cursor holds a record
   and derives its position, so the test that matters is what "the next
   one" answers after the list underneath it has changed. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createTimeline } from "../workbench/js/timeline.mjs";
import { element, fire, gesture, installDom } from "./dom.mjs";

/* One tick is one microsecond, so a gap and its printed delta are the
   same number and a wrong one cannot hide in the conversion. */
const FREQ = 1_000_000;
const CATALOGUE = [
  { id: "bind", code: 1, edge: "inject", fields: ["lr"] },
  { id: "eoi", code: 2, edge: "inject", fields: [] },
];
/* Wide enough that a lane is its own band of pixels: two lanes at 20px
   in a 200px strip, and 704px of plot after the caption gutter. */
const BOX = { left: 0, top: 0, width: 800, height: 200 };

function harness() {
  installDom();
  const strip = element("div");
  const canvas = element("canvas");
  canvas.rect = BOX;
  const followButton = element("button");
  const asked = [];
  const chosen = [];
  const timeline = createTimeline({
    strip,
    canvas,
    foldButton: element("button"),
    followButton,
    request: (data) => asked.push(data),
    onSelect: (choice) => chosen.push(choice),
  });
  timeline.setCatalogue(CATALOGUE);
  return { timeline, asked, chosen, canvas, strip, followButton };
}

const columns = (records) => ({
  ts: records.map((record) => record.ts),
  code: records.map((record) => record.code),
  cpu: records.map((record) => record.cpu ?? 0),
  a: records.map(() => 0),
  b: records.map(() => 0),
  c: records.map(() => 0),
});

/* A window answer of the kind the bridge sends unasked while following:
   records, relative to the window's own start. */
const answer = (from, records, to = from + 100_000) => ({
  window: { from, to, freq_hz: FREQ },
  cols: columns(records),
});

/* Four records 1000, 1100, 1300, 1700 — alternating lanes, so which one
   a move landed on is readable from its id alone. */
const FOUR = [
  { ts: 0, code: 1 },
  { ts: 100, code: 2 },
  { ts: 300, code: 1 },
  { ts: 700, code: 2 },
];

const fake = (t) => t.mock.timers.enable({ apis: ["setTimeout"] });

/* Where a record was drawn, from the same geometry the paint uses. */
const xOf = (ts, window_) => 96 + ((ts - window_.from) / (window_.to - window_.from)) * 704;

describe("timeline cursor", () => {
  it("announces the chain a selection sits in, not just the mark", (t) => {
    fake(t);
    const { timeline, chosen } = harness();
    timeline.apply(answer(1000, FOUR));

    assert.equal(timeline.select(2), true);
    const choice = chosen.at(-1);
    assert.equal(choice.kind, "mark");
    assert.equal(choice.record.id, "bind");
    assert.equal(choice.record.edge, "inject");
    assert.deepEqual(choice.record.fields, ["lr"]);
    assert.equal(choice.index, 2);
    assert.equal(choice.total, 4);
    assert.equal(choice.prev.id, "eoi");
    assert.equal(choice.next.id, "eoi");
    /* The gap from the previous mark, in real microseconds. */
    assert.equal(choice.dt, 200);
  });

  it("has no gap to report at the first mark", (t) => {
    fake(t);
    const { timeline, chosen } = harness();
    timeline.apply(answer(1000, FOUR));

    timeline.select(0);
    assert.equal(chosen.at(-1).dt, null);
    assert.equal(chosen.at(-1).prev, null);
  });

  it("steps from the record it is on, not from where that record was", (t) => {
    fake(t);
    const { timeline, chosen } = harness();
    timeline.apply(answer(1000, FOUR));
    timeline.select(1); /* the record at 1100 */
    assert.equal(chosen.at(-1).index, 1);

    /* A record five seconds on slides the live window past the oldest
       mark, so the same selection is now first rather than second. An
       index kept across that would name the record after the wrong one. */
    timeline.apply(answer(5_001_100, [{ ts: 0, code: 1 }]));

    timeline.step(+1);
    const choice = chosen.at(-1);
    assert.equal(choice.record.id, "bind", "stepped from a stale index");
    assert.equal(choice.record.ts, 1300);
    assert.equal(choice.index, 1);
    assert.equal(choice.total, 4);
  });

  it("walks back and forth through the same announcement", (t) => {
    fake(t);
    const { timeline, chosen } = harness();
    timeline.apply(answer(1000, FOUR));

    timeline.step(+1); /* nothing picked yet: start at the front */
    assert.equal(chosen.at(-1).index, 0);
    timeline.step(+1);
    assert.equal(chosen.at(-1).index, 1);
    timeline.step(-1);
    assert.equal(chosen.at(-1).index, 0);
    /* And it does not walk off either end. */
    timeline.step(-1);
    assert.equal(chosen.at(-1).index, 0);
  });

  it("prints the real gap however fast it is being replayed", (t) => {
    fake(t);
    const { timeline, chosen } = harness();
    timeline.apply(answer(1000, FOUR));
    timeline.select(0);

    timeline.play();
    assert.equal(timeline.isPlaying(), true);
    t.mock.timers.tick(89);
    assert.equal(chosen.length, 1, "playback moved before its own floor");
    t.mock.timers.tick(1);
    assert.equal(chosen.at(-1).record.ts, 1100);

    t.mock.timers.tick(90);
    t.mock.timers.tick(90);
    /* Each announcement carries the gap the firmware recorded, never
       the delay the player waited. */
    assert.deepEqual(
      chosen.map((choice) => choice.dt),
      [null, 100, 200, 400],
    );
    /* The last record is the end of it. */
    assert.equal(timeline.isPlaying(), false);
  });

  it("compresses an idle stretch without compressing what it says", (t) => {
    fake(t);
    const { timeline, chosen } = harness();
    /* Two seconds of nothing between the second and third records. */
    timeline.apply(
      answer(1000, [
        { ts: 0, code: 1 },
        { ts: 100, code: 2 },
        { ts: 2_000_100, code: 1 },
      ]),
    );
    timeline.select(0);
    timeline.play();
    t.mock.timers.tick(90);
    assert.equal(chosen.at(-1).record.ts, 1100);

    /* Two real seconds, waited as nine hundred milliseconds. */
    t.mock.timers.tick(899);
    assert.equal(chosen.length, 2, "playback outran its own ceiling");
    t.mock.timers.tick(1);
    assert.equal(chosen.at(-1).record.ts, 2_001_100);
    assert.equal(chosen.at(-1).dt, 2_000_000, "the printed delta was the delay");
  });

  it("stops playing where it is asked to", (t) => {
    fake(t);
    const { timeline, chosen } = harness();
    timeline.apply(answer(1000, FOUR));
    timeline.select(0);
    timeline.play();

    timeline.stop();
    assert.equal(timeline.isPlaying(), false);
    const settled = chosen.length;
    t.mock.timers.tick(1000);
    assert.equal(chosen.length, settled);
  });

  it("drops a selection that a new machine's records cannot mean", (t) => {
    fake(t);
    const { timeline, chosen } = harness();
    timeline.apply(answer(1000, FOUR));
    timeline.select(1);
    timeline.play();

    timeline.reset();
    assert.equal(timeline.isPlaying(), false);

    /* The same timestamps arrive again. A kept cursor would recognise
       one of them and step to the third; a dropped one starts over. */
    timeline.apply(answer(1000, FOUR));
    timeline.step(+1);
    assert.equal(chosen.at(-1).index, 0);
  });
});

describe("timeline following", () => {
  it("asks only for what has arrived since the last answer", (t) => {
    fake(t);
    const { timeline, asked } = harness();
    timeline.setLimits({ buckets: 1024 });

    timeline.note({ span: { from: 0, to: 4000, n: 12, freq_hz: FREQ } });
    t.mock.timers.tick(250);
    /* Holding nothing, it asks from the present rather than for the
       whole run — which would come back as density every time. */
    assert.deepEqual(asked.at(-1), { op: "window", from: 4000, to: 4000, buckets: 1024 });

    timeline.apply(answer(1000, FOUR));
    timeline.note({ span: { from: 0, to: 9000, n: 20, freq_hz: FREQ } });
    t.mock.timers.tick(250);
    assert.deepEqual(asked.at(-1), { op: "window", from: 1701, to: 9000, buckets: 1024 });
  });

  it("says on the strip which way it is pointed", (t) => {
    fake(t);
    const { timeline, strip, followButton } = harness();

    timeline.setFollow(false);
    assert.equal(strip.dataset.follow, "off");
    assert.equal(followButton.getAttribute("aria-pressed"), "false");

    timeline.setFollow(true);
    assert.equal(strip.dataset.follow, "on");
    assert.equal(followButton.getAttribute("aria-pressed"), "true");
  });
});

describe("timeline tour", () => {
  it("is a filtered window walked by the one cursor", (t) => {
    fake(t);
    const { timeline, asked, chosen, strip } = harness();
    timeline.note({ span: { from: 1000, to: 9000, n: 40, freq_hz: FREQ } });

    assert.equal(timeline.tour(["bind"], "인젝션"), true);
    assert.equal(timeline.touringLabel(), "인젝션");
    assert.equal(strip.dataset.follow, "off", "a tour is not the present");
    assert.deepEqual(asked.at(-1), {
      op: "window",
      from: 1000,
      to: 9000,
      buckets: 4096,
      events: ["bind"],
    });

    /* The answer starts the same cursor a click moves, and playing it
       is the walk — no second way to draw a record. */
    timeline.apply(answer(1000, FOUR, 9000));
    assert.equal(chosen.at(-1).kind, "mark");
    assert.equal(chosen.at(-1).index, 0);
    assert.equal(timeline.isPlaying(), true);
    assert.equal(timeline.touringLabel(), null);
  });

  it("refuses a tour of a run the bridge no longer holds", (t) => {
    fake(t);
    const { timeline, asked } = harness();
    assert.equal(timeline.tour(["bind"], "인젝션"), false);
    assert.equal(asked.length, 0);
  });
});

describe("timeline pointer and keys", () => {
  it("selects the record under a click, and only in its own lane", (t) => {
    fake(t);
    const { timeline, chosen, canvas } = harness();
    timeline.apply(answer(1000, FOUR));
    const window_ = { from: 1000, to: 1700 };

    /* Lane 0 is `bind`, in catalogue order, twenty pixels tall. */
    const at = { clientX: xOf(1300, window_), clientY: 10, pointerId: 1 };
    fire(canvas, "pointerdown", at);
    fire(canvas, "pointerup", at);

    assert.equal(chosen.at(-1).kind, "mark");
    assert.equal(chosen.at(-1).record.ts, 1300);
    assert.equal(chosen.at(-1).index, 2, "a click took a path of its own");
  });

  it("measures between two marks without moving the cursor", (t) => {
    fake(t);
    const { timeline, chosen, canvas } = harness();
    timeline.apply(answer(1000, FOUR));
    const window_ = { from: 1000, to: 1700 };
    const first = { clientX: xOf(1300, window_), clientY: 10, pointerId: 1 };
    fire(canvas, "pointerdown", first);
    fire(canvas, "pointerup", first);

    const second = { clientX: xOf(1700, window_), clientY: 25, pointerId: 1, shiftKey: true };
    fire(canvas, "pointerdown", second);
    fire(canvas, "pointerup", second);

    const choice = chosen.at(-1);
    assert.equal(choice.kind, "delta");
    assert.equal(choice.from.id, "bind");
    assert.equal(choice.to.id, "eoi");
    assert.equal(choice.micros, 400);
  });

  it("steps on an arrow key, and stepping is not following", (t) => {
    fake(t);
    const { timeline, chosen, canvas, strip } = harness();
    timeline.apply(answer(1000, FOUR));

    const key = (name) => fire(canvas, "keydown", gesture({ key: name }));
    key("ArrowRight");
    assert.equal(strip.dataset.follow, "off");
    assert.equal(chosen.at(-1).index, 0);
    key("ArrowRight");
    assert.equal(chosen.at(-1).index, 1);
    key("End");
    assert.equal(chosen.at(-1).index, 3);
    key("Home");
    assert.equal(chosen.at(-1).index, 0);

    key(" ");
    assert.equal(timeline.isPlaying(), true);
    key(" ");
    assert.equal(timeline.isPlaying(), false);
  });
});

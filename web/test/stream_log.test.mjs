/* The stream log: a scroll pin that belongs to the frame that lays the
   batch out, following that is the reader's to give up and take back,
   and rows grouped so a full log costs what is on screen. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { StreamLog } from "../workbench/js/primitives/stream_log.mjs";
import { element, findAll, fire, installDom } from "./dom.mjs";

/* A scroller with room to scroll, and frames the test releases itself
   rather than the fake DOM's synchronous ones. The bottom is
   scrollHeight − clientHeight, as the fake DOM clamps it. */
function harness() {
  installDom();
  const frames = [];
  globalThis.requestAnimationFrame = (frame) => {
    frames.push(frame);
    return frames.length;
  };
  const container = element("div");
  container.scrollHeight = 500;
  container.clientHeight = 100;
  container.scrollTop = 400;
  const log = new StreamLog({ container });
  const batch = (ts, grown) => {
    container.scrollHeight = grown;
    log.append(element("div"), ts);
    log.settle();
  };
  const frame = () => frames.shift()();
  /* The reader scrolls, and the browser tells the log so. */
  const scrollTo = (top) => {
    container.scrollTop = top;
    fire(container, "scroll");
  };
  return { log, container, frames, batch, frame, scrollTo };
}

describe("stream log pin", () => {
  it("pins in the frame, once for any number of batches before it", () => {
    const { container, frames, batch, frame } = harness();
    batch(1, 600);
    batch(2, 700);

    /* Reading the scroll height here would force the layout the batch
       left pending; nothing has moved until the frame. */
    assert.equal(container.scrollTop, 400);
    assert.equal(frames.length, 1);
    frame();
    assert.equal(container.scrollTop, 600);
    assert.equal(frames.length, 0);
  });

  it("asks for a frame again once the last one has run", () => {
    const { container, frames, batch, frame } = harness();
    batch(1, 600);
    frame();
    batch(2, 900);
    assert.equal(frames.length, 1);
    frame();
    assert.equal(container.scrollTop, 800);
  });

  it("is not fooled by its own pin arriving as a late scroll event", () => {
    const { container, batch, frame } = harness();
    batch(1, 600);
    frame(); /* pinned at 500 */
    /* The next batch lands before the browser reports the pin's scroll;
       the report finds the height already grown. Judged against the
       bottom, that is a reader who scrolled away — and the log would
       stop following for good. */
    batch(2, 800);
    fire(container, "scroll");
    frame();
    assert.equal(container.scrollTop, 700);
  });

  it("leaves a reader who scrolled up where they are", () => {
    const { container, batch, frame, scrollTo } = harness();
    batch(1, 600);
    frame();
    scrollTo(100);

    batch(2, 900);
    frame();
    assert.equal(container.scrollTop, 100);
  });

  it("keeps following when the pane grows and the browser clamps the position", () => {
    const { container, batch, frame } = harness();
    batch(1, 600);
    frame(); /* pinned at 500 */
    /* A taller pane: the bottom moves up to 400, where the browser clamps
       the position and reports a scroll nobody made. Judged against the
       last pin alone that is a reader who scrolled up — one who could
       never come back, the new bottom being above it. */
    container.clientHeight = 200;
    assert.equal(container.scrollTop, 400);

    batch(2, 800);
    frame();
    assert.equal(container.scrollTop, 600);
  });

  it("does not drag a reader reading forward past what they have not seen", () => {
    const { container, batch, frame, scrollTo } = harness();
    batch(1, 600);
    frame(); /* pinned at 500 */
    scrollTo(100);
    batch(2, 2000);
    frame();
    /* Below where the log last put them, above the bottom: still reading. */
    scrollTo(700);

    batch(3, 2400);
    frame();
    assert.equal(container.scrollTop, 700);
  });

  it("follows again once the reader reaches the bottom", () => {
    const { container, batch, frame, scrollTo } = harness();
    batch(1, 600);
    frame();
    scrollTo(100);
    batch(2, 900);
    frame();
    scrollTo(800); /* the bottom of 900 in a pane of 100 */

    batch(3, 1200);
    frame();
    assert.equal(container.scrollTop, 1100);
  });

  it("pins a pane shown for the first time twice, once its rows have rendered", () => {
    const { log, container, frames, frame } = harness();
    container.scrollHeight = 600;
    log.pin({ recheck: true });
    frame();
    assert.equal(container.scrollTop, 500);
    /* The rows that came into view were taller than their estimate. */
    container.scrollHeight = 660;
    assert.equal(frames.length, 1);
    frame();
    assert.equal(container.scrollTop, 560);
    assert.equal(frames.length, 0);
  });
});

/* Rows are grouped so the browser can skip most of a full log. What the
   group declares about itself is the whole of what a skipped one is. */
describe("stream log chunks", () => {
  function filled(count, { lineCap = 1000, from = 0 } = {}) {
    installDom();
    const container = element("div");
    const log = new StreamLog({ container, lineCap });
    for (let n = from; n < from + count; n += 1) {
      const row = element("div");
      row.textContent = `line ${n}`;
      log.append(row, (n + 1) * 1000);
    }
    return { log, container, chunks: () => findAll(container, "chunk") };
  }
  const rowsDeclared = (chunk) => Number(chunk.style.getPropertyValue("--rows"));

  it("drops the oldest rows across the boundary and takes the empty group with them", () => {
    const { log, container, chunks } = filled(260, { lineCap: 150 });
    log.settle();

    assert.equal([...log.rows()].length, 150);
    assert.equal([...log.rows()][0].textContent, "line 110");
    /* The first group emptied and went; the second is what the trim ate
       into. A group left in the container holding nothing would declare
       a height for rows that are not there. */
    assert.deepEqual(chunks().map(rowsDeclared), [90, 60]);
    assert.equal(container.children.length, 2);
  });

  it("walks rows in order across the boundary, both ways", () => {
    const { log } = filled(250);
    const seen = [];
    /* The cut walks row to row; the grouping must be invisible to it.
       The first cut has no boundary to start from, so it is one full
       pass — over every row, in order, across every group. */
    log.cutTo(120_500, (row, past) => {
      row.hidden = past;
      seen.push(row.textContent);
    });
    assert.equal(seen.length, 250);
    assert.deepEqual([seen[0], seen.at(-1)], ["line 0", "line 249"]);

    /* And the next is the boundary walk: the rows that changed side. */
    seen.length = 0;
    log.cutTo(null, (row, past) => {
      row.hidden = past;
      seen.push(row.textContent);
    });
    assert.equal(seen.length, 130); // rows 120..249, the ones past the cut
    assert.deepEqual([seen[0], seen.at(-1)], ["line 120", "line 249"]);
    assert.equal([...log.rows()].every((row) => !row.hidden), true);
  });

  it("declares the rows a group still shows, and hides a group showing none", () => {
    const { log, chunks } = filled(300);
    log.settle();
    assert.deepEqual(chunks().map(rowsDeclared), [100, 100, 100]);

    /* A cursor moved into the past: two whole groups are future. A group
       that only declared a height would leave it as empty space. */
    log.cutTo(100_000, (row, past) => {
      row.hidden = past;
    });
    assert.deepEqual(chunks().map(rowsDeclared), [100, 0, 0]);
    assert.deepEqual(chunks().map((chunk) => chunk.hidden), [false, true, true]);

    log.cutTo(null, (row, past) => {
      row.hidden = past;
    });
    assert.deepEqual(chunks().map(rowsDeclared), [100, 100, 100]);
    assert.deepEqual(chunks().map((chunk) => chunk.hidden), [false, false, false]);
  });

  it("brings a group back when a row arrives in one that shows nothing", () => {
    const { log, chunks } = filled(150);
    log.settle();
    /* The cursor at the very start: every row is future, every group is
       nothing to show. */
    log.cutTo(0, (row, past) => {
      row.hidden = past;
    });
    assert.deepEqual(chunks().map((chunk) => chunk.hidden), [true, true]);

    /* A remark this session makes: on no run clock, so no cut hides it.
       Landing in a group that is display:none would make it invisible —
       and nothing would say so until some later batch settled. */
    const notice = element("div");
    notice.hidden = log.append(notice, undefined);
    assert.equal(notice.hidden, false);
    assert.equal(notice.parentNode.hidden, false);
  });

  it("declares what its groups hold even where nobody is looking", () => {
    const { log, container, chunks } = filled(150);
    /* A console tab that is not the active one: the count it declares is
       owed all the same, or showing it would scroll into the space its
       stale estimate claims. Only the scroll waits for the pane. */
    container.hidden = true;
    log.settle();
    assert.deepEqual(chunks().map(rowsDeclared), [100, 50]);
    assert.equal(container.scrollTop, 0);
  });

  it("takes the count again when the caller hides rows by a rule of its own", () => {
    const { log, chunks } = filled(200);
    log.settle();

    /* A badge filter, which the stream knows nothing about. */
    for (const row of log.rows()) row.hidden = row.textContent.endsWith("7");
    log.restyled();
    assert.deepEqual(chunks().map(rowsDeclared), [90, 90]);

    for (const row of log.rows()) row.hidden = true;
    log.restyled();
    assert.deepEqual(chunks().map((chunk) => chunk.hidden), [true, true]);
  });
});

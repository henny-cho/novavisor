/* The stream log's scroll pin: it belongs to the frame that lays the
   batch out, and following is the reader's to give up and take back. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { StreamLog } from "../workbench/js/primitives/stream_log.mjs";
import { element, fire, installDom } from "./dom.mjs";

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

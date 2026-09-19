/* Console multiplexer test: tabs, merged view, guest logging, and the
   keys a reader presses back at the machine. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createConsole } from "../workbench/js/console.mjs";
import { setGuestSlots } from "../workbench/js/format.mjs";
import { StreamLog } from "../workbench/js/primitives/stream_log.mjs";
import { element, findAll, fire, gesture, installDom } from "./dom.mjs";

const FOCUS_CYCLE = "\u0014"; /* the byte Ctrl-T stands for */

/* The lines a pane holds. The stream groups them, so they are what a
   walk finds rather than what the pane's own children are — and a line
   shows only if its group does. */
const lines = (pane) => findAll(pane, "cline");
const showing = (row) => !row.hidden && !row.parentNode.hidden;

/* The control state main.mjs hands every control module; a machine is
   running unless a test is about the states where none is. */
const RUNNING = { phase: "running", paused: false, replaying: false, pending: null, halt: null };

function harness({ live = true } = {}) {
  installDom();
  const tabs = element("div");
  const logs = element("div");
  const banner = element("div");
  const form = element("form");
  const input = element("input");
  const focusButton = element("button");
  /* The link the input path answers to. `up` is writable so one test can
     lose it and get it back, which is the retry the console promises. */
  const link = { up: true };
  const sent = [];
  const send = (topic, data) => {
    if (!link.up) return false;
    sent.push([topic, data]);
    return true;
  };
  const notices = [];
  const onNotice = (msg) => notices.push(msg);

  const consoleView = createConsole({
    tabs,
    logs,
    banner,
    form,
    input,
    focusButton,
    send,
    onNotice,
  });
  if (live) consoleView.setState(RUNNING);

  return { consoleView, tabs, logs, banner, form, input, focusButton, link, sent, notices };
}

const submit = (form) => {
  const event = gesture();
  fire(form, "submit", event);
  return event;
};

const press = (input, key, held = {}) => {
  const event = gesture({ key, ctrlKey: true, altKey: false, metaKey: false, ...held });
  fire(input, "keydown", event);
  return event;
};

describe("console multiplexer", () => {
  it("sends nothing before the wire has said anything", () => {
    const { input, focusButton, form, sent } = harness({ live: false });

    /* The markup ships the line live; a page that has not heard from the
       bridge has no machine for the bytes to reach. */
    assert.equal(input.disabled, true);
    assert.equal(focusButton.disabled, true);
    input.value = "ls";
    submit(form);
    assert.deepEqual(sent, []);
  });

  it("stands down where the bridge would refuse: paused, and in a replay", () => {
    const { consoleView, input, focusButton, form, sent } = harness();

    /* A paused machine's pty would buffer the bytes and replay them into
       the guest on resume — the bridge refuses them, so the line does
       not offer to send them. */
    consoleView.setState({ ...RUNNING, paused: true });
    assert.equal(input.disabled, true);
    assert.equal(focusButton.disabled, true);
    input.value = "ls";
    submit(form);
    assert.deepEqual(sent, []);

    consoleView.setState({ ...RUNNING, phase: "replay", replaying: true });
    assert.equal(input.disabled, true);

    /* The machine runs again: the line is back, and what was typed with it. */
    consoleView.setState(RUNNING);
    assert.equal(input.disabled, false);
    submit(form);
    assert.deepEqual(sent, [["uart", { bytes: "ls\n" }]]);
  });

  it("initializes tabs and appends hypervisor lines to merged view", () => {
    const { consoleView, logs } = harness();
    consoleView.append({ vm: null, text: "booting nova EL2" }, 1e9);
    consoleView.settle();

    const merged = logs.children.find((p) => p.id === "log-all");
    assert.ok(merged, "merged log pane exists");
    assert.equal(lines(merged).length, 1);
    assert.match(lines(merged)[0].textContent, /EL2booting nova EL2/);
  });

  it("dynamically manages guest tabs from topology", () => {
    const { consoleView, tabs, logs } = harness();
    setGuestSlots({ max_guests: 4 });
    consoleView.setGuests([{ name: "linux", vcpus: 1 }, { name: "zephyr", vcpus: 1 }]);

    const guestTabs = findAll(tabs, "tab");
    assert.equal(guestTabs.length, 3); // all + vm0 + vm1

    consoleView.append({ vm: 0, text: "linux kernel started" }, 2e9);
    consoleView.append({ vm: 1, text: "zephyr boot banner" }, 3e9);
    consoleView.settle();

    const vm0Pane = logs.children.find((p) => p.id === "log-0");
    const vm1Pane = logs.children.find((p) => p.id === "log-1");
    assert.ok(vm0Pane);
    assert.ok(vm1Pane);
    assert.equal(lines(vm0Pane).length, 1);
    assert.equal(lines(vm1Pane).length, 1);
  });

  it("mints nothing for a slot the board cannot host", () => {
    const { consoleView, tabs, logs } = harness();
    /* The tag is a firmware field, but a line claiming a slot this
       machine has no room for is guest text that looks like one. */
    setGuestSlots({ max_guests: 2 });
    consoleView.append({ vm: 5, text: "[vm5] a line that only looks tagged" }, 1e9);
    consoleView.settle();

    assert.equal(findAll(tabs, "tab").length, 1); // the merged log alone
    const merged = logs.children.find((pane) => pane.id === "log-all");
    assert.equal(lines(merged).length, 1);
  });

  it("cuts future output when cursor moves into past", () => {
    const { consoleView, logs } = harness();
    consoleView.append({ vm: null, text: "early event" }, 1e9);
    consoleView.append({ vm: null, text: "late event" }, 5e9);

    const merged = logs.children.find((p) => p.id === "log-all");
    assert.equal(lines(merged).length, 2);

    consoleView.cutAt(3e9);
    assert.equal(showing(lines(merged)[0]), true);
    assert.equal(showing(lines(merged)[1]), false);

    consoleView.cutAt(null);
    assert.equal(showing(lines(merged)[1]), true);
  });

  it("keeps a pane nobody is looking at ready to be looked at", () => {
    const { consoleView, logs } = harness();
    setGuestSlots({ max_guests: 2 });
    consoleView.setGuests([{ name: "linux", vcpus: 1 }]);
    /* The merged tab is active, so vm0's pane is hidden. What its groups
       declare is still owed: activating it pins to a bottom computed
       from those declarations. */
    for (let i = 1; i <= 120; i += 1) consoleView.append({ vm: 0, text: `line ${i}` }, i * 1e9);
    consoleView.settle();

    const pane = logs.children.find((p) => p.id === "log-0");
    assert.equal(pane.hidden, true);
    assert.deepEqual(
      findAll(pane, "chunk").map((chunk) => Number(chunk.style.getPropertyValue("--rows"))),
      [100, 20],
    );
  });

  it("keeps up with a cursor moved repeatedly, and with a line that lands past it", () => {
    const { consoleView, logs } = harness();
    for (let i = 1; i <= 6; i += 1) consoleView.append({ vm: null, text: `line ${i}` }, i * 1e9);
    const merged = logs.children.find((p) => p.id === "log-all");
    const shown = () => lines(merged).map(showing);

    /* The cut walks out from where it last was rather than over every
       row, so what it left behind has to still be right after it has
       moved both ways. */
    consoleView.cutAt(3.5e9);
    assert.deepEqual(shown(), [true, true, true, false, false, false]);
    consoleView.cutAt(5.5e9);
    assert.deepEqual(shown(), [true, true, true, true, true, false]);
    consoleView.cutAt(1.5e9);
    assert.deepEqual(shown(), [true, false, false, false, false, false]);

    /* A line printed past the cut arrives hidden: waiting for the next
       cursor move would show the reader output from after the moment
       they are looking at. */
    consoleView.append({ vm: null, text: "later still" }, 7e9);
    assert.deepEqual(shown(), [true, false, false, false, false, false, false]);
    consoleView.cutAt(7.5e9);
    assert.deepEqual(shown(), [true, true, true, true, true, true, true]);

    /* The same, with nothing else past the cut: the boundary has to
       begin at the arriving line, or moving the cursor forward again
       never brings it back. */
    consoleView.append({ vm: null, text: "beyond" }, 9e9);
    assert.deepEqual(shown().at(-1), false);
    consoleView.cutAt(9.5e9);
    assert.deepEqual(shown().at(-1), true);
  });
});

describe("console input", () => {
  it("sends the typed line with the newline Enter stands for, and empties the box", () => {
    const { form, input, sent } = harness();
    input.value = "help";

    const event = submit(form);

    assert.deepEqual(sent, [["uart", { bytes: "help\n" }]]);
    assert.equal(input.value, "", "the line went out, so the box is clear for the next one");
    assert.ok(event.prevented, "a submit that reloaded the page would drop the session");
  });

  it("sends a bare newline when Enter is pressed on an empty box", () => {
    const { form, sent } = harness();

    submit(form);

    /* Enter at a prompt is a keystroke the guest answers, not nothing. */
    assert.deepEqual(sent, [["uart", { bytes: "\n" }]]);
  });

  it("keeps a line the bridge could not take, so it can be sent again", () => {
    const { form, input, link, sent, notices } = harness();
    link.up = false;
    input.value = "reboot";

    submit(form);

    assert.deepEqual(sent, []);
    assert.equal(input.value, "reboot", "typing survives a bridge that was not there");
    assert.match(notices.at(-1), /입력을 보내지 못했습니다/);

    link.up = true;
    submit(form);

    assert.deepEqual(sent, [["uart", { bytes: "reboot\n" }]]);
    assert.equal(input.value, "");
  });

  it("cycles focus on Ctrl-T with a control byte rather than a typed letter", () => {
    const { input, sent } = harness();

    const lower = press(input, "t");
    const upper = press(input, "T"); /* the same chord with shift held */

    assert.deepEqual(sent, [
      ["uart", { bytes: FOCUS_CYCLE }],
      ["uart", { bytes: FOCUS_CYCLE }],
    ]);
    assert.ok(lower.prevented && upper.prevented, "an unstopped chord types its letter too");
  });

  it("leaves a keystroke that is not the chord to the box", () => {
    const { input, sent } = harness();

    const typed = press(input, "t", { ctrlKey: false });
    press(input, "t", { altKey: true });
    press(input, "t", { metaKey: true });
    press(input, "r");

    assert.deepEqual(sent, [], "only Ctrl-T alone is the focus cycle");
    assert.ok(!typed.prevented, "a letter the console stopped would never reach the box");
  });

  it("cycles focus from the button and hands the caret back", () => {
    const { focusButton, input, sent } = harness();

    fire(focusButton, "click", gesture());

    assert.deepEqual(sent, [["uart", { bytes: FOCUS_CYCLE }]]);
    assert.ok(input.focused, "a reader who clicked the button still means to type");
  });
});

describe("console focus", () => {
  /* A guest set the board can host, so a slot the firmware names has a tab. */
  const twoGuests = () => {
    const kit = harness();
    setGuestSlots({ max_guests: 4 });
    kit.consoleView.setGuests([{ name: "vm0", vcpus: 1 }, { name: "vm1", vcpus: 1 }]);
    return kit;
  };
  const marked = (tabs) => findAll(tabs, "focused").map((tab) => tab.textContent);

  it("marks no tab until a switch is observed", () => {
    const { tabs } = twoGuests();

    assert.deepEqual(marked(tabs), [], "a browser that joined late has seen none");
  });

  it("marks the tab a focus switch named and unmarks the one before", () => {
    const { consoleView, tabs } = twoGuests();

    consoleView.note({ fields: { focus: "1" } });
    assert.deepEqual(marked(tabs), ["vm1"]);
    /* The tooltip carries the caveat the dot cannot: last seen, not live. */
    assert.match(findAll(tabs, "focused")[0].title, /현재 값 아님/u);

    consoleView.note({ fields: { focus: "0" } });
    assert.deepEqual(marked(tabs), ["vm0"], "focus is one tab at a time");
  });

  it("leaves the mark standing for an event that carries no focus", () => {
    const { consoleView, tabs } = twoGuests();
    consoleView.note({ fields: { focus: "1" } });

    consoleView.note({ fields: { vm: "0" } });
    consoleView.note({});

    assert.deepEqual(marked(tabs), ["vm1"], "only a focus switch says where typing goes");
  });

  it("drops the mark at a run boundary, which the new machine has not answered for", () => {
    const { consoleView, tabs } = twoGuests();
    consoleView.note({ fields: { focus: "1" } });

    consoleView.mark("── 10_console_mux ──");

    assert.deepEqual(marked(tabs), []);
  });
});

/* The cap the console and the event log are both built on. */
describe("stream cap", () => {
  it("holds its cap and drops the oldest, across a clear", () => {
    installDom();
    const container = element("div");
    const stream = new StreamLog({ container, lineCap: 100 });
    const put = (n) => {
      const row = element("div");
      row.textContent = `line ${n}`;
      stream.append(row, n * 1000);
    };
    const first = () => [...stream.rows()][0].textContent;

    /* The cap is met a group at a time, so 250 rows leave the last 50. */
    for (let n = 0; n < 250; n += 1) put(n);
    assert.equal([...stream.rows()].length, 50);
    assert.equal(first(), "line 200");

    /* The stream counts what it put there rather than asking the
       container, so the count has to survive everything that empties
       it — a stale one either lets the log grow without bound or trims
       a full buffer down to nothing. */
    stream.clear();
    for (let n = 0; n < 120; n += 1) put(n);
    assert.equal([...stream.rows()].length, 20);
    assert.equal(first(), "line 100");
  });
});

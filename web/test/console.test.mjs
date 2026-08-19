/* Console multiplexer test: tabs, merged view, guest logging, and the
   keys a reader presses back at the machine. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createConsole } from "../workbench/js/console.mjs";
import { element, findAll, fire, gesture, installDom } from "./dom.mjs";

const FOCUS_CYCLE = "\u0014"; /* the byte Ctrl-T stands for */

function harness() {
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
  it("initializes tabs and appends hypervisor lines to merged view", () => {
    const { consoleView, logs } = harness();
    consoleView.append({ vm: null, text: "booting nova EL2" }, 1e9);
    consoleView.settle();

    const merged = logs.children.find((p) => p.id === "log-all");
    assert.ok(merged, "merged log pane exists");
    assert.equal(merged.children.length, 1);
    assert.match(merged.children[0].textContent, /EL2booting nova EL2/);
  });

  it("dynamically manages guest tabs from topology", () => {
    const { consoleView, tabs, logs } = harness();
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
    assert.equal(vm0Pane.children.length, 1);
    assert.equal(vm1Pane.children.length, 1);
  });

  it("cuts future output when cursor moves into past", () => {
    const { consoleView, logs } = harness();
    consoleView.append({ vm: null, text: "early event" }, 1e9);
    consoleView.append({ vm: null, text: "late event" }, 5e9);

    const merged = logs.children.find((p) => p.id === "log-all");
    assert.equal(merged.children.length, 2);

    consoleView.cutAt(3e9);
    assert.equal(merged.children[0].hidden, false);
    assert.equal(merged.children[1].hidden, true);

    consoleView.cutAt(null);
    assert.equal(merged.children[1].hidden, false);
  });

  it("keeps up with a cursor moved repeatedly, and with a line that lands past it", () => {
    const { consoleView, logs } = harness();
    for (let i = 1; i <= 6; i += 1) consoleView.append({ vm: null, text: `line ${i}` }, i * 1e9);
    const merged = logs.children.find((p) => p.id === "log-all");
    const shown = () => merged.children.map((row) => !row.hidden);

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

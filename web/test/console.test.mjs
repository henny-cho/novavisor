/* Console multiplexer test: tabs, merged view, and guest logging. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createConsole } from "../workbench/js/console.mjs";
import { element, findAll, fire, installDom } from "./dom.mjs";

function harness() {
  installDom();
  const tabs = element("div");
  const logs = element("div");
  const banner = element("div");
  const form = element("form");
  const input = element("input");
  const focusButton = element("button");
  const sent = [];
  const send = (data) => sent.push(data);
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

  return { consoleView, tabs, logs, banner, form, input, focusButton, sent, notices };
}

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
});

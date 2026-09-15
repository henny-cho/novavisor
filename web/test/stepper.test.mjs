/* Stepper test, written from the rules rather than from the code: a
   control that disagrees with one of the names below is a finding. The
   state is an input here — a browser cannot be asked for `failed` while
   paused, and the sweep needs every combination. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createStepper } from "../workbench/js/stepper.mjs";
import { element, fire, installDom } from "./dom.mjs";

const CATALOGUE = [
  { id: "bind", label: "vGIC bind" },
  { id: "inject", label: "inject" },
  /* In the catalogue as a lane, but with no firmware symbol to break on. */
  { id: "drain", label: "eoi drain", stop: false },
];

const PHASES = ["idle", "building", "running", "verifying", "exited", "failed", "replay"];
const DRIVERS = ["advanceButton", "stepButton", "autoButton"];

/* `fresh` keeps the page's storage across a second mount, which is how
   a reader coming back tomorrow reaches the module. */
function harness({ sends = true, fresh = true } = {}) {
  if (fresh) installDom();
  const nodes = {
    pick: element("select"),
    countInput: element("input"),
    advanceButton: element("button"),
    stepButton: element("button"),
    autoButton: element("button"),
    abortButton: element("button"),
    note: element("span"),
  };
  const sent = [];
  const notices = [];
  const state = { phase: "idle", paused: false, replaying: false, pending: null, halt: null };

  const view = createStepper({
    ...nodes,
    send: (topic, data) => {
      sent.push([topic, data]);
      return sends;
    },
    onPending: (what) => {
      state.pending = what;
      view.setState(state);
    },
    onNotice: (message) => notices.push(message),
  });

  /* main.mjs's rule: anything the wire reports answers the request in
     flight, so this is also what re-arms a control. */
  const at = (patch) => {
    Object.assign(state, patch, { pending: null });
    view.setState(state);
  };
  return { view, at, running: () => at({ phase: "running" }), sent, notices, state, ...nodes };
}

const options = (node) => node.children.map((option) => option.value);

describe("stepper", () => {
  it("offers 정지 없음 and every catalogue entry the firmware can break on", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);

    assert.deepEqual(options(kit.pick), ["", "bind", "inject"]);
    assert.equal(kit.pick.children[0].textContent, "정지 없음");
    /* Nothing armed is a choice, and it is the one a fresh page holds. */
    assert.deepEqual(kit.view.chosen(), []);
  });

  it("keeps the reader's choice across a republish", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "inject";

    kit.view.setStops(CATALOGUE);
    assert.equal(kit.pick.value, "inject");
    assert.deepEqual(kit.view.chosen(), ["inject"]);
  });

  it("drops a choice the new catalogue no longer offers", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "inject";

    /* Another image, without that symbol: keeping the choice would arm
       a launch at a stop the run can never reach. */
    kit.view.setStops([{ id: "bind", label: "vGIC bind" }]);
    assert.equal(kit.pick.value, "");
    assert.deepEqual(kit.view.chosen(), []);
  });

  it("drives a running machine and nothing else", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";

    for (const phase of PHASES) {
      for (const paused of [false, true]) {
        for (const replaying of [false, true]) {
          for (const pending of [null, "advance"]) {
            for (const halt of [null, { cmd: "run" }]) {
              kit.view.setState({ phase, paused, replaying, pending, halt });
              /* A paused machine is a running one stopped where the reader
                 asked, which is exactly when stepping is wanted; one the
                 bridge holds for a command is refused a second. */
              const live = phase === "running" && !replaying && !pending && !halt;
              const where = `${phase} paused=${paused} replay=${replaying} pending=${pending} halt=${Boolean(halt)}`;
              for (const name of DRIVERS) assert.equal(kit[name].disabled, !live, `${name} ${where}`);
              assert.equal(kit.abortButton.hidden, !halt, `abort ${where}`);
            }
          }
        }
      }
    }
  });

  it("drives nothing before the wire has said anything", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";

    /* The markup ships the buttons live; a page that has not heard from
       the bridge yet has no machine to drive. */
    for (const name of DRIVERS) fire(kit[name], "click");
    assert.deepEqual(kit.sent, []);
  });

  it("lets a replay drive nothing", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";

    kit.at({ phase: "replay", replaying: true });
    for (const name of DRIVERS) fire(kit[name], "click");
    assert.deepEqual(kit.sent, []);
  });

  it("refuses to advance with no stop chosen, and says why", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.running();

    fire(kit.advanceButton, "click");
    assert.deepEqual(kit.sent, []);
    assert.equal(kit.note.hidden, false);
    assert.match(kit.note.textContent, /정지 지점/u);
  });

  it("runs to the chosen stop and stands down until the wire answers", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "inject";
    kit.running();

    fire(kit.advanceButton, "click");
    assert.deepEqual(kit.sent, [["halt", { cmd: "run", stops: ["inject"] }]]);
    assert.equal(kit.state.pending, "advance");
    /* The bridge holds one inspection at a time and rejects the rest, so
       a second click while the first is out buys a warning and nothing
       else. */
    fire(kit.advanceButton, "click");
    fire(kit.stepButton, "click");
    assert.equal(kit.sent.length, 1);

    /* The machine stopped: the request has been answered. */
    kit.at({ phase: "running", paused: true });
    fire(kit.advanceButton, "click");
    assert.equal(kit.sent.length, 2);
  });

  it("steps by instructions with no stop chosen", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.running();

    /* Stepping looks inside an event; it needs no event to aim at, and
       the count it ships with is the one a reader starts from. */
    fire(kit.stepButton, "click");
    assert.deepEqual(kit.sent, [["halt", { cmd: "step", count: 40 }]]);
    assert.equal(kit.state.pending, "step");
  });

  it("offers the ceiling the bridge published and no number of its own", () => {
    const kit = harness();

    /* 5000 is the bridge's clamp; a copy here would be free to drift
       from it, so the box has no bound until the topology brings one. */
    assert.equal(kit.countInput.max, undefined);
    kit.view.setLimits({ buckets: 8192, steps: 5000 });
    assert.equal(kit.countInput.max, "5000");
    kit.view.setLimits({ buckets: 8192, steps: 120 });
    assert.equal(kit.countInput.max, "120");
  });

  it("sends the reader's count", () => {
    const kit = harness();
    kit.view.setLimits({ steps: 5000 });
    kit.running();

    /* The use case: 400 instructions past a breakpoint to find where a
       list register was written. */
    kit.countInput.value = "400";
    fire(kit.stepButton, "click");
    assert.deepEqual(kit.sent, [["halt", { cmd: "step", count: 400 }]]);
  });

  it("clamps a count outside the band before it is sent", () => {
    const kit = harness();
    kit.view.setLimits({ steps: 5000 });
    kit.running();

    /* The bridge would cut it silently, and the reader would go on
       believing the machine advanced as far as they asked. */
    kit.countInput.value = "9999";
    fire(kit.stepButton, "click");
    assert.deepEqual(kit.sent.at(-1), ["halt", { cmd: "step", count: 5000 }]);
    assert.equal(kit.countInput.value, "5000");

    kit.at({ phase: "running", paused: true });
    kit.countInput.value = "";
    fire(kit.stepButton, "click");
    assert.deepEqual(kit.sent.at(-1), ["halt", { cmd: "step", count: 40 }]);
  });

  it("holds a count the published ceiling has since dropped below", () => {
    const kit = harness();
    kit.view.setLimits({ steps: 5000 });
    kit.countInput.value = "4000";

    /* Another bridge, a smaller bound: the box cannot keep offering a
       count that one would refuse. */
    kit.view.setLimits({ steps: 100 });
    assert.equal(kit.countInput.value, "100");
  });

  it("keeps the reader's count across a session", () => {
    const kit = harness();
    kit.countInput.value = "400";
    fire(kit.countInput, "change");

    const again = harness({ fresh: false });
    assert.equal(again.countInput.value, "400");
  });

  it("stands the forward controls down for the whole batch", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.view.setLimits({ steps: 5000 });
    kit.running();

    /* 5000 steps are taken serially, each with its own timeout, so the
       batch is in flight for as long as it runs and the note says so. */
    kit.countInput.value = "5000";
    fire(kit.stepButton, "click");
    for (const name of DRIVERS) assert.equal(kit[name].disabled, true, name);
    assert.match(kit.note.textContent, /5000 명령/u);

    kit.at({ phase: "running", paused: true });
    for (const name of DRIVERS) assert.equal(kit[name].disabled, false, name);
  });

  it("shows 중지 exactly while the bridge holds the machine", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";
    kit.running();
    assert.equal(kit.abortButton.hidden, true);

    /* A request out is not yet a hold; the bridge says when it has one,
       and from then on 중지 is the one thing left to press. */
    fire(kit.advanceButton, "click");
    assert.equal(kit.abortButton.hidden, true);
    kit.at({ halt: { cmd: "run" } });
    assert.equal(kit.abortButton.hidden, false);
    for (const name of DRIVERS) assert.equal(kit[name].disabled, true, name);

    /* Asking to abort does not end the hold; the bridge letting go does. */
    fire(kit.abortButton, "click");
    assert.deepEqual(kit.sent.at(-1), ["halt", { cmd: "abort" }]);
    assert.equal(kit.abortButton.hidden, false);
    kit.at({ paused: true, halt: null });
    assert.equal(kit.abortButton.hidden, true);
    assert.equal(kit.advanceButton.disabled, false);
  });

  it("offers 중지 for a hold this page never asked for", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";

    /* A stop armed at launch, or a page reloaded mid-run: the connect
       topology carries the hold, and the reader can still take the
       machine back. */
    kit.at({ phase: "running", halt: { cmd: "run" } });
    assert.equal(kit.abortButton.hidden, false);
    for (const name of DRIVERS) assert.equal(kit[name].disabled, true, name);
  });

  it("keeps 자동 pressed across the stops of its run and releases it with the hold", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";
    kit.running();

    fire(kit.autoButton, "click");
    assert.deepEqual(kit.sent, [["halt", { cmd: "run", stops: ["bind"], repeat: 50, period: 1 }]]);
    assert.equal(kit.autoButton.getAttribute("aria-pressed"), "true");
    kit.at({ halt: { cmd: "run" } });
    /* Each stop of the run answers, and the run is not over: the bridge
       still holds the machine for the next repeat. */
    kit.at({ paused: true });
    assert.equal(kit.autoButton.getAttribute("aria-pressed"), "true");
    assert.equal(kit.autoButton.disabled, false);
    assert.equal(kit.advanceButton.disabled, true);
    assert.equal(kit.abortButton.hidden, false);

    /* The fiftieth stop, or an abort: the bridge lets go and 자동 is over. */
    kit.at({ halt: null });
    assert.equal(kit.autoButton.getAttribute("aria-pressed"), "false");
    assert.equal(kit.abortButton.hidden, true);
  });

  it("knows when an arming only repeats the hold's last one", () => {
    const kit = harness();
    kit.at({ phase: "running", halt: { cmd: "run" } });

    /* The first arming of a hold is news; the repeats of an 자동 run
       restate it; a different stop set is news again. */
    assert.equal(kit.view.armed(["bind"]), false);
    assert.equal(kit.view.armed(["bind"]), true);
    assert.equal(kit.view.armed(["inject"]), false);

    /* A new hold starts afresh, even for the same stops. */
    kit.at({ halt: null });
    kit.at({ halt: { cmd: "run" } });
    assert.equal(kit.view.armed(["inject"]), false);
  });

  it("unpresses 자동 when the bridge refuses it before any hold", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";
    kit.running();

    fire(kit.autoButton, "click");
    assert.equal(kit.state.pending, "auto");
    kit.at({}); /* uplink-rejected: the attempt is over, nothing was held */
    assert.equal(kit.autoButton.getAttribute("aria-pressed"), "false");
  });

  it("lets 자동 be taken back while its own request is in flight", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";
    kit.running();

    fire(kit.autoButton, "click");
    assert.equal(kit.state.pending, "auto");
    /* Cancelling is the answer to a request in flight, so the flight is
       not what locks the reader out of it. */
    fire(kit.autoButton, "click");
    assert.deepEqual(kit.sent.at(-1), ["halt", { cmd: "abort" }]);
    assert.equal(kit.abortButton.hidden, true);
  });

  it("ends 자동 with the machine it was stepping", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";
    kit.running();
    fire(kit.autoButton, "click");

    kit.at({ phase: "exited" });
    assert.equal(kit.abortButton.hidden, true);
    assert.equal(kit.autoButton.getAttribute("aria-pressed"), "false");
  });

  it("says when the bridge is not there to take the request", () => {
    const kit = harness({ sends: false });
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";
    kit.running();

    fire(kit.advanceButton, "click");
    assert.equal(kit.notices.length, 1);
    /* Nothing is in flight, so the control is still there to press. */
    assert.equal(kit.state.pending, null);
    assert.equal(kit.advanceButton.disabled, false);
  });

  it("stops at a mark the timeline picked", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.running();

    assert.equal(kit.view.stopAt("inject"), true);
    assert.equal(kit.pick.value, "inject");
    assert.deepEqual(kit.sent, [["halt", { cmd: "run", stops: ["inject"] }]]);
    assert.match(kit.note.textContent, /inject/u);
    assert.equal(kit.state.pending, "advance");
  });

  it("clears the note and 자동 at a run boundary", () => {
    const kit = harness();
    kit.view.setStops(CATALOGUE);
    kit.pick.value = "bind";
    kit.running();
    fire(kit.autoButton, "click");
    kit.view.say("정지 · bind");

    kit.view.reset();
    assert.equal(kit.note.textContent, "");
    assert.equal(kit.note.hidden, true);
    assert.equal(kit.autoButton.getAttribute("aria-pressed"), "false");
  });
});

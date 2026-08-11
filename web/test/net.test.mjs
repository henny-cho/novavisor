import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { connect, createReceiver } from "../workbench/js/net.mjs";

class FakeSocket {
  static OPEN = 1;
  static instances = [];

  constructor() {
    this.readyState = FakeSocket.OPEN;
    this.listeners = new Map();
    this.sent = [];
    FakeSocket.instances.push(this);
  }

  addEventListener(name, callback) {
    this.listeners.set(name, callback);
  }

  send(payload) {
    this.sent.push(JSON.parse(payload));
  }

  fire(name, value = {}) {
    this.listeners.get(name)?.(value);
  }
}

const frame = (seq, topic, replyTo, data = {}) => ({
  v: 3,
  seq,
  topic,
  kind: "snapshot",
  ts: seq,
  src: "bridge",
  data,
  reply_to: replyTo,
});

describe("workbench request ownership", () => {
  it("accounts for foreign replies without delivering them or inventing a loss", () => {
    const delivered = [];
    const losses = [];
    const receiver = createReceiver(
      { onFrame: (value) => delivered.push(value), onLoss: (count) => losses.push(count) },
      { ownsReply: (requestId) => requestId.startsWith("mine:") },
    );
    receiver.opened();

    receiver.ingest(JSON.stringify([
      frame(1, "trace", "other:1"),
      frame(2, "trace", "mine:1"),
    ]));

    assert.deepEqual(delivered.map((value) => value.reply_to), ["mine:1"]);
    assert.deepEqual(losses, []);
  });

  it("sends one active query followed by only the latest replacement", (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    FakeSocket.instances = [];
    globalThis.WebSocket = FakeSocket;
    const delivered = [];
    const wire = connect(
      { onFrame: (value) => delivered.push(value) },
      { requestPrefix: "mine" },
    );
    const socket = FakeSocket.instances[0];
    socket.fire("open");

    wire.ask("trace", { from: 1 });
    wire.ask("trace", { from: 2 });
    wire.ask("trace", { from: 3 });
    assert.equal(socket.sent.length, 1);
    const first = socket.sent[0].request_id;

    socket.fire("message", {
      data: JSON.stringify([frame(1, "trace", first, { window: { from: 1 } })]),
    });

    assert.equal(socket.sent.length, 2);
    assert.deepEqual(socket.sent[1].data, { from: 3 });
    assert.equal(delivered.length, 1);
  });

  it("reconnects with only the newest unanswered query", (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    FakeSocket.instances = [];
    globalThis.WebSocket = FakeSocket;
    const wire = connect({}, { requestPrefix: "again" });
    const first = FakeSocket.instances[0];
    first.fire("open");
    wire.ask("trace", { from: 1 });
    wire.ask("trace", { from: 9 });

    first.fire("close");
    t.mock.timers.tick(500);
    const second = FakeSocket.instances[1];
    second.fire("open");

    assert.equal(second.sent.length, 1);
    assert.deepEqual(second.sent[0].data, { from: 9 });
  });
});

describe("workbench receiver faults", () => {
  it("reports malformed and non-batch messages with bounded consecutive counts", () => {
    const faults = [];
    const healthy = [];
    const receiver = createReceiver({
      onFault: (value) => faults.push(value),
      onHealthy: () => healthy.push(true),
    });
    receiver.opened();

    receiver.ingest("{");
    receiver.ingest("{");
    receiver.ingest("{}");
    receiver.ingest(JSON.stringify([frame(1, "topo", undefined)]));
    receiver.ingest("{");

    assert.deepEqual(faults.map(({ kind, count }) => [kind, count]), [
      ["payload", 1],
      ["payload", 2],
      ["batch", 1],
      ["payload", 1],
    ]);
    assert.equal(healthy.length, 1);
  });

  it("reports invalid envelopes and version mismatches without inventing loss", () => {
    const faults = [];
    const losses = [];
    const delivered = [];
    const receiver = createReceiver({
      onFault: (value) => faults.push(value),
      onLoss: (count) => losses.push(count),
      onFrame: (value) => delivered.push(value),
    });
    receiver.opened();

    receiver.ingest(JSON.stringify([
      { v: 3, topic: "trace", data: {} },
      { ...frame(1, "trace", undefined), v: 2 },
      frame(2, "trace", undefined),
    ]));

    assert.deepEqual(faults.map(({ kind, count }) => [kind, count]), [
      ["envelope", 1],
      ["protocol", 1],
    ]);
    assert.deepEqual(losses, []);
    assert.deepEqual(delivered.map((value) => value.seq), [2]);
  });

  it("continues with later frames after one frame handler fails", () => {
    const attempted = [];
    const faults = [];
    const receiver = createReceiver({
      onFault: (value) => faults.push(value),
      onFrame: (value) => {
        attempted.push(value.topic);
        if (value.topic === "broken") throw new Error("renderer exploded");
      },
    });
    receiver.opened();

    receiver.ingest(JSON.stringify([
      frame(1, "broken", undefined),
      frame(2, "healthy", undefined),
    ]));

    assert.deepEqual(attempted, ["broken", "healthy"]);
    assert.equal(faults.length, 1);
    assert.equal(faults[0].kind, "handler");
    assert.match(faults[0].detail, /renderer exploded/);
  });

  it("distinguishes reconnect gaps, stream loss, and sequence restart", () => {
    const gaps = [];
    const losses = [];
    const resets = [];
    const receiver = createReceiver({
      onGap: () => gaps.push(true),
      onLoss: (count) => losses.push(count),
      onReset: () => resets.push(true),
    });
    receiver.opened();
    receiver.ingest(JSON.stringify([frame(1, "trace", undefined)]));
    receiver.ingest(JSON.stringify([frame(3, "trace", undefined)]));

    receiver.opened();
    receiver.ingest(JSON.stringify([frame(6, "trace", undefined)]));
    receiver.opened();
    receiver.ingest(JSON.stringify([frame(1, "trace", undefined)]));

    assert.deepEqual(losses, [1]);
    assert.equal(gaps.length, 1);
    assert.equal(resets.length, 1);
  });

  it("surfaces WebSocket transport errors", (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    FakeSocket.instances = [];
    globalThis.WebSocket = FakeSocket;
    const faults = [];
    connect({ onFault: (value) => faults.push(value) }, { requestPrefix: "fault" });
    const socket = FakeSocket.instances[0];
    socket.fire("open");

    socket.fire("error", { message: "link failed" });

    assert.equal(faults.length, 1);
    assert.equal(faults[0].kind, "socket");
    assert.equal(faults[0].detail, "link failed");
  });
});

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

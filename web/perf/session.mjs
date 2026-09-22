/* A run as the wire delivers it.

   Every frame here is one the bridge really sends, in the shape its
   protocol states, so what the measurement drives is the client's own
   handling rather than a rehearsal of it. The machine comes from the
   bridge; what is written here is the run over it — two guests, a boot
   burst, a drain — a demo's numbers rather than a stress test's, because
   a tool that measures a run nobody makes reports a cost nobody pays. */

const US = 1_000;
let seq = 0;

const frame = (topic, data, kind = "event", src = "B") => ({
  v: 3,
  seq: (seq += 1),
  ts: seq * US * 1000,
  topic,
  kind,
  src,
  data,
});

export const snapshot = (topic, values, src = "S") =>
  frame(topic, { values }, "snapshot", src);

const GUESTS = [
  { name: "vm0", vcpus: 2, pa: 0x48000000, ipa: 0x40000000, size: 64 << 20, uart: "pl011" },
  { name: "vm1", vcpus: 2, pa: 0x4c000000, ipa: 0x40000000, size: 64 << 20, uart: "none" },
];

/* Everything a topology says about the machine rather than the run: the
   board, the stop catalogue, the manifest, the vocabularies, the limits.
   Handed in rather than written, because a copy drifts. */
let world = { observations: {} };

export function install(machine) {
  world = machine;
  const missing = Object.keys(world.observations).filter((topic) => !(topic in readings()));
  if (missing.length) {
    throw new Error(`no reading staged for ${missing.join(", ")}`);
  }
}

export const topics = () => Object.keys(world.observations);
export const rate = (topic) => world.observations[topic]?.rate;

export function topology() {
  return frame("topo", {
    session: "perf", run_id: 1, phase: "running", demo: "07-shm", variant: null,
    description: "공유 메모리 부트 카운터",
    /* The machine, from the bridge; the run's own placement, from here. */
    ...world,
    guests: GUESTS,
    memory: { regimes: [{ id: "el2.self", label: "EL2 · 자기", role: "self", root: "0x40100000" },
                        { id: "vm0.cpu", label: "VM 0 · CPU", role: "cpu", root: "0x40200000" }] },
    command: { period_us: 250, slots: 128, ops: [
      { name: "spi", label: "SPI 주입", action: "주입", desc: "물리 SPI를 게스트로",
        args: [{ kind: "vm", lo: 0, hi: 1 }, { kind: "int", lo: 32, hi: 1019, default: 48 }] },
      { name: "mark", label: "표식", action: "표식", args: [{ kind: "int", free: true, lo: 0, hi: 65535 }] },
    ] },
  }, "snapshot");
}

export const life = (phase, extra = {}) => frame("life", { phase, ...extra });

export const consoleLines = (count, from = 0) =>
  Array.from({ length: count }, (_, i) =>
    frame("console", {
      vm: (from + i) % 5 === 0 ? null : (from + i) % 2,
      text: `[BOOT] stage ${from + i} bringing up a subsystem with a realistic line length`,
    }));

const BADGES = ["TRAP", "IRQ", "VGIC", "GIC", "SCHED", "SMP", "DMA", "BOOT"];
export const events = (count, from = 0) =>
  Array.from({ length: count }, (_, i) =>
    frame("ev", { badge: BADGES[(from + i) % BADGES.length], severity: "INFO",
                  message: `path ${from + i} carried something worth a row`,
                  fields: { n: from + i } }));

const el1 = Object.fromEntries(
  ["sctlr", "ttbr0", "ttbr1", "tcr", "mair", "vbar", "sp", "elr", "spsr", "esr", "far",
   "contextidr", "tpidr", "cpacr", "afsr0", "afsr1", "amair", "csselr", "par", "cntkctl",
   "cntv_cval", "cntv_ctl"].map((name, i) => [name, `0x${(0x1000 + i).toString(16)}`]),
);
const trapCtx = {
  x: Array.from({ length: 31 }, (_, i) => `0x${(0x2000 + i).toString(16)}`),
  sp: "0x3000", elr: "0x3008", spsr: "0x3c5", esr: "0x96000045", far: "0x9000000",
};

/* One tick of everything the S layer publishes, so a scenario can pick
   the topics that share a rate rather than invent a mixture. */
export function readings() {
  const slots = 8;
  return {
    "sched.cpu": [{ current: 0, since: 3_990_000, fp: 0, fp_trap: false, idling: false },
                  { current: 4, since: 3_995_000, fp: null, fp_trap: false, idling: true }],
    "sched.run": Array.from({ length: slots }, (_, i) => (i % 4 ? null : { state: "kRunning" })),
    "sched.slots": Array.from({ length: slots }, (_, i) => (i % 4 ? "kOff" : "kOn")),
    "sched.slice": 10,
    "sched.affinity": Array.from({ length: slots }, () => 3),
    "sched.valid": Array.from({ length: slots }, (_, i) => i % 4 === 0),
    "vm.generation": [1, 1],
    "ctx.syndrome": Array.from({ length: slots }, (_, i) => (i % 4 ? null : { ec: 0x24, far: "0x9000000" })),
    "ctx.trap": Array.from({ length: slots }, () => ({ ctx: trapCtx })),
    "ctx.el1": Array.from({ length: slots }, () => ({ el1 })),
    "ctx.synced": Array.from({ length: slots }, () => ({ synced_at: 4000 })),
    "vgic.capacity": 4,
    "vgic.lr": Array.from({ length: slots }, (_, i) =>
      (i % 4 ? [] : [{ slot: 0, vintid: 33, state: "pending", prio: 160, group1: true,
                       eoi: false, pintid: 33, generation: 1 }])),
    "vgic.resident": [0, 4],
    "vgic.dist": [{ ctlr: "0x12", group1: [32, 33], enabled: [33], pending: [33] },
                  { ctlr: "0x0", group1: [32, 33], enabled: [], pending: [] }],
    "vgic.token": [[{ pintid: 48 }], []],
    "vgic.synced": Array.from({ length: slots }, () => ({ synced_at: 4000 })),
    "timer.queue": [[{ slot: 1, deadline: 4_010_000 }], [{ slot: 0, deadline: 4_002_000 }]],
    "timer.programmed": [4_002_000, 4_010_000],
    "timer.cntvoff": [625_000, 0],
    "dev.uart": [{ count: 3, head: 1, imsc: "0x10" }, { count: 0, head: 0, imsc: "0x0" }],
    "dev.dma": { entries_: [{ device_id: 0, owner_vm: 0, state: "kAssigned", generation: 1,
                              deadline: 0, bus_master_blocked: false }], count_: 1 },
    "dev.watchdog": [7, 7],
    "smmu.stream": [{ stream: 0, state: "translate", vmid: 1, root: "0x41000000" },
                    { stream: 1, state: "abort" }],
    "ivc.page": { a2b: { widx: "0x5", ridx: "0x2", slots: [0, 0, 0, 0] } },
    "smp.online": [true, true],
    "smp.lifecycle": [{ epoch_: 1, pending_mask_: 0, retries_: 0, active_: true }],
    "smp.mode": ["kRunning", "kRunning"],
    "smp.mail": [{ count: 0 }, { count: 2 }],
    "smp.budget": [3, 3],
    "vm.table": [{ vm: 0 }, { vm: 1 }],
  };
}

export const tick = (topics) => {
  const values = readings();
  return topics.map((topic) => snapshot(topic, values[topic]));
};

/* A drain: what the rings held, and the window the strip then asks for.
   `cols` are the columns the bridge packs, relative to the window. */
export function traceDrain(records, from = 1000) {
  const span = { from, to: from + records * 40, n: records, freq_hz: 1_000_000, full: false };
  return {
    summary: frame("trace", {
      span, edges: { inject: 12, sw: 40 },
      last: { inject: { event: "vgic.bind", vintid: 33 } },
      budget: { capacity: 4096, peak_rate: 3200, horizon_ms: 1280,
                worst_gap_ms: 41, gaps: { 10: 30, 50: 2 }, overrun: false },
    }),
    window: frame("trace", {
      span,
      window: { from, to: span.to, freq_hz: 1_000_000 },
      cols: {
        ts: Array.from({ length: records }, (_, i) => i * 40),
        code: Array.from({ length: records }, (_, i) => [1, 2, 3][i % 3]),
        cpu: Array.from({ length: records }, (_, i) => i % 2),
        a: Array.from({ length: records }, () => 33),
        b: Array.from({ length: records }, () => 0),
        c: Array.from({ length: records }, () => 0),
      },
    }, "snapshot"),
  };
}

export const cursor = (wire) => frame("cursor", { wire, unread: [] });

/* ---------- the memory view ----------
   The walk the bridge answers a probe with: one L0 table, eight L1,
   and folded runs of leaf slots the way a mapped region unfolds. A
   captured regime, the kind whose tables do not move under a run. */

const WALK = { read: 9, truncated: false, unreadable: [], wx: 0, wxn: true };

const leaf = (index) => ({
  level: 2, index, count: 8, base: "0x20000000", size: "0x2000",
  kind: "block", output: "0x48000000", w: true, x: false, memory: "RAM", af: true,
});
WALK.nodes = [
  {
    level: 0, index: 0, count: 512, base: "0x0", size: "0x800000000",
    kind: "table", output: "0x48200000",
    children: Array.from({ length: 8 }, (_, index) => ({
      level: 1, index, count: 64, base: "0x20000000", size: "0x2000000",
      kind: "table", output: "0x48100000",
      children: Array.from({ length: 38 }, (_, leafer) => leaf(index * 38 + leafer)),
    })),
  },
];

export function memoryAnswer() {
  return frame("probe", {
    regime: "el2.self",
    ground: "captured",
    root: "0x48200000",
    tree: WALK,
    beside: [],
    moving: false,
    isolation: null,
  });
}

/* The stamp topic the view reads live, even over a captured walk: the
   frame is what must not rebuild the tree. */
export const syncedTick = (at = 1_000_000) =>
  snapshot("ctx.synced", [{ synced_at: at }]);

/* A run as the wire delivers it.

   Every frame here is one the bridge really sends, in the shape its
   protocol states, so what the measurement drives is the client's own
   handling rather than a rehearsal of it. The numbers — two guests on
   two cores, a boot burst, the S-layer topics that publish at 20Hz —
   are a demo's, not a stress test's: a tool that measures a machine
   nobody runs reports a cost nobody pays. */

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

const BLOCKS = [
  { id: "gicd", layer: "ic", label: "GICD", base: 0x8000000, size: 0x10000 },
  { id: "gicr", layer: "ic", label: "GICR", base: 0x80a0000, size: 0x20000, cpu: 0 },
  { id: "smmu", layer: "ic", label: "SMMU", base: 0x9050000, size: 0x20000, sid_bits: 8, intids: [74, 75] },
  { id: "uart0", layer: "dev", label: "PL011", base: 0x9000000, size: 0x1000, owner: "el2", intid: 33 },
  { id: "virtio0", layer: "dev", label: "virtio-mmio", base: 0xa000000, size: 0x200, device_id: 0, intid: 48 },
];

const EDGES = [
  { id: "mmio", from: "band:el1", to: "trap", grade: "console", badges: ["TRAP"], label: "게스트 MMIO → 트랩" },
  { id: "inject", from: "vgic", to: "band:el1", grade: "direct", badges: ["VGIC"], label: "vGIC 주입" },
  { id: "spi", from: "gicd", to: "vgic", grade: "poll", topic: "vgic.dist", badges: ["GIC"], label: "SPI → vGIC" },
  { id: "dma", from: "virtio0", to: "smmu", grade: "poll", topic: "dev.dma", badges: ["DMA"], label: "DMA → SMMU" },
  { id: "sw", from: "sched", to: "band:pe", grade: "poll", topic: "sched.cpu", badges: ["SCHED"], label: "문맥 교환" },
];

export const STOPS = [
  { id: "vgic.bind", edge: "inject", args: ["pintid", "vintid"], label: "바인드", code: 1,
    fields: ["pintid|vintid", "", ""], stop: true, span: false },
  { id: "vgic.eoi", edge: "inject", args: [], label: "EoI", code: 2, fields: ["vintid", "", ""],
    stop: true, span: false },
  { id: "ctx.switch", edge: "sw", args: [], label: "문맥 교환", code: 3, fields: ["from", "to", ""],
    stop: true, span: false },
  { id: "trace.gap", edge: "", args: [], label: "관측되지 않은 구간", code: 9,
    fields: ["count", "from", ""], stop: false, span: true },
];

/* The manifest's rates, because they are what decides how often each
   scenario below actually happens on a reader's screen. */
export const RATES = {
  "sched.cpu": 20, "sched.run": 20, "sched.slots": 20, "sched.slice": 10,
  "sched.affinity": 2, "sched.valid": 2, "vm.generation": 2, "ctx.syndrome": 10,
  "ctx.trap": 2, "ctx.el1": 2, "ctx.synced": 10, "vgic.lr": 10, "vgic.capacity": 2,
  "vgic.dist": 10, "vgic.resident": 10, "vgic.token": 10, "timer.queue": 10,
  "timer.programmed": 10, "timer.cntvoff": 2, "dev.uart": 10, "dev.dma": 2,
  "dev.watchdog": 2, "ivc.page": 2, "smp.online": 2, "smp.lifecycle": 2,
  "smp.mode": 2, "smp.mail": 2, "smp.budget": 2, "vgic.synced": 10, "vm.table": 2,
};

export function topology() {
  return frame("topo", {
    session: "perf", run_id: 1, phase: "running", demo: "07-shm", variant: null,
    description: "공유 메모리 부트 카운터",
    catalog: [{ id: "07", name: "07-shm" }],
    guests: GUESTS,
    board: { name: "qemu-virt", cpus: 2, cpu: "cortex-a57", vcpu_stride: 4,
             blocks: BLOCKS, edges: EDGES,
             regions: {
               pa: [{ base: 0x40000000, size: 0x8000000, kind: "el2", name: "EL2" },
                    { base: 0x48000000, size: 0x8000000, kind: "guest", name: "게스트 창" },
                    { base: 0x50000000, size: 0x100000, kind: "shared", name: "공유" },
                    { base: 0x50100000, size: 0x100000, kind: "trace", name: "트레이스" }],
               ipa: [{ base: 0x40000000, size: 0x4000000, kind: "guest", name: "게스트" },
                     { base: 0x9000000, size: 0x1000, kind: "trap", name: "vUART" },
                     { base: 0xa000000, size: 0x200, kind: "assigned", name: "virtio" }],
             } },
    stops: STOPS,
    observations: Object.fromEntries(
      Object.entries(RATES).map(([topic, rate]) => [topic, { rate, asserted: false }]),
    ),
    taxonomy: { badges: ["TRAP", "IRQ", "VGIC", "GIC", "SCHED", "SMP", "PSCI", "DMA", "SMMU", "WDG", "BOOT", "MUX", "VUART", "FAULT"],
                esr_ec: { 36: "kDataAbortLower", 22: "kHvcAa64" } },
    timer_slots: ["watchdog", "slice", "vtimer"],
    limits: { buckets: 4096 },
    memory: { regimes: [{ id: "el2.self", label: "EL2 · 자기", role: "self", root: "0x40100000" },
                        { id: "vm0.cpu", label: "VM 0 · CPU", role: "cpu", root: "0x40200000" }] },
    command: { period_us: 250, ops: [
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
    "sched.cpu": [{ current: 0, fp: 0, fp_trap: false, idling: false },
                  { current: 4, fp: null, fp_trap: false, idling: true }],
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
    "vgic.dist": [{ spi_pending: "5" }, { spi_pending: "0" }],
    "vgic.token": [[{ pintid: 48 }], []],
    "vgic.synced": Array.from({ length: slots }, () => ({ synced_at: 4000 })),
    "timer.queue": [[{ slot: 1, deadline: "0x1234" }], [{ slot: 0, deadline: "0x99" }]],
    "timer.programmed": ["0x5000", "0x6000"],
    "timer.cntvoff": [0, 0],
    "dev.uart": [{ count: 3, head: 1, imsc: "0x10" }, { count: 0, head: 0, imsc: "0x0" }],
    "dev.dma": { entries_: [{ device_id: 0, owner_vm: 0, state: "kAssigned", generation: 1,
                              deadline: "0x0", bus_master_blocked: false }], count_: 1 },
    "dev.watchdog": [7, 7],
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

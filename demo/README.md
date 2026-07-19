# NovaVisor Demo Framework

Each NovaVisor roadmap phase is validated by a **demo** — an EL1 guest program (or a reference OS image) that exercises the features introduced in that phase. The demo is both a *demonstration* (what the hypervisor can now do) and a *verification* (automated pass/fail check in CI).

## Directory layout

```
demo/
├── README.md                    # this file
├── CMakeLists.txt               # builds all custom EL1 guests
├── common/
│   ├── startup.S                # EL1 entry: stack, BSS, call main(), HVC_EXIT
│   ├── linker.ld.S              # linker script template (window from nova/abi/guest_layout.h)
│   └── include/
│       ├── demo_hvc.h           # HVC ABI helpers (IDs from nova/abi/hvc_abi.h)
│       ├── gic_el1.h            # guest-side GICD/GICR programming (emulated MMIO)
│       └── ivc_shm.h            # guest-owned mailbox protocol on the IVC shared page
├── 01_hello/                    # Phase 5 demo
│   ├── CMakeLists.txt
│   ├── main.c
│   └── manifest.yml
├── 02_timer/                    # Phase 6 demo
│   ├── CMakeLists.txt
│   ├── main.c
│   ├── vectors.S                # guest EL1 vector table (vIRQ handler)
│   └── manifest.yml
├── 03_ivc_pingpong/             # Phase 7 demo — two VMs, shared page + doorbell
│   ├── CMakeLists.txt
│   ├── ping.c                   # initiator (VM 0): VM_START, publish, doorbell, poll
│   ├── pong.c                   # responder (VM 1): echo until last-round flag
│   ├── vectors.S
│   └── manifest.yml
├── 04_native_gic/               # Phase 8 demo — architecture-standard GIC + CNTV path
│   ├── CMakeLists.txt
│   ├── main.c                   # GICD/GICR programming + native periodic timer
│   ├── vectors.S                # IRQ handler: ack, re-arm CNTV, EOI
│   └── manifest.yml
├── 05_preempt/                  # Phase 9 demo — preemptive slice + wfi idle
├── 06_el1_mmu_fp/               # Phase 10 demo — EL1 MMU + lazy FP/SIMD
├── 07_lifecycle/                # Phase 11 demo — PSCI reset + watchdog recovery
├── 08_smp_pingpong/             # Phase 12 demo — two VMs on two physical cores
├── 09_guest_smp/                # Phase 13 demo — one VM, two vCPUs (PSCI CPU_ON + vSGI)
└── 10_console_mux/              # Phase 14 demo — vuart TX tagging + focus-routed RX echo
```

## Hypercall ABI (demo ↔ hypervisor contract)

Function IDs are defined once in `nova/abi/hvc_abi.h`, shared by the guest
stubs and the hypervisor dispatcher. This table documents them:

| Function ID (x0) | Name | Args | Description |
| --- | --- | --- | --- |
| `0x1000` | `PUTS`  | x1=ptr, x2=len | Write string to UART via hypervisor |
| `0x1001` | `PUTC`  | x1=char        | Write one character |
| `0x1002` | `EXIT`  | x1=code        | Guest termination. Hypervisor logs `demo_exit code=<n>` |
| `0x1003` | `YIELD` | —              | Yield the VCPU (cooperative round-robin) |
| `0x1004` | `HEARTBEAT` | x1=window ms | Re-arm the caller's watchdog; missing the window warm-resets the VM. 0 disarms |
| `0x1005` | `VM_START` | x1=vm index | Start a not-yet-running VM; 0 or -1 in x0 |
| `0x1100` | `IVC_DOORBELL` | x1=vm index | Inject doorbell vIRQ (SGI 0) into the target VM; 0 or -1 in x0 |
| `0x1200` | `TIMER_SET` | x1=ticks | One-shot: injects vINTID 27 after `ticks` counter cycles; returns 0 in x0. Legacy since Phase 8 — new guests use CNTV directly |

PSCI (Phase 11): guests control their own power through the standard
SMCCC range (`0x8400_xxxx`, HVC conduit) — `SYSTEM_OFF` stops the VM,
`SYSTEM_RESET` warm-reboots it from a pristine image (the IVC shared
page survives a reset; the guest window does not). IDs in
`nova/abi/psci.h`, stubs in `common/include/guest_psci.h`.

SMP (Phase 12): guest_table slots carry a static physical-core
affinity — slots 0/1 run on core 0, slots 2/3 on core 1 — so a
manifest places a guest on the other core purely by `load_addr`.
Guests on different cores run truly in parallel and can talk through
the lock-free SPSC rings in the IVC shared page (layout in
`nova/abi/ivc_ring.h`, helpers in `common/include/guest_ring.h`,
acquire/release only — no exclusives, safe with the EL1 MMU off).

Console mux + vuart (Phase 14): every guest line printed through the
demo HVC or a guest's own PL011 arrives `[vmN]`-tagged and line-atomic
(the hypervisor multiplexes per-VM line buffers onto the single
physical UART). VMs declaring `uart: vuart` see an emulated PL011 at
the standard virt address (`pl011_el1.h` driver helpers) — TX by DR
polling, RX by the UART SPI (33): enable it at the distributor
(`gicd_enable_spi()`) and unmask IMSC. Host input goes to the focused
VM; Ctrl-T (0x14) cycles focus across live vuart VMs. A manifest
`expect` entry may carry `send:` — bytes written to the guest console
after that pattern matches. Guests also each get a private virtual
counter (per-VM CNTVOFF): CNTVCT restarts near zero on every (re)boot.

Guest SMP (Phase 13): the boot slot's VM carries two vCPUs (cores 0
and 1) and the manifest `vcpus:` field is validated against the
supported range. A guest identifies its vCPU by MPIDR Aff0, brings the
sibling up with `psci_cpu_on()` (entry stub in `common/secondary.S` —
pass the stack top as context_id; BSS is not re-zeroed), programs its
own redistributor frame (`gicr_wake_at()` — frames are strided per
vCPU with per-frame GICR_TYPER), and sends IPIs with `icc_send_sgi()`
— the ICC_SGI1R write is trapped and routed across physical cores.
`psci_cpu_off()` retires one vCPU; `psci_affinity_info()` observes it.

Multi-VM notes (Phase 7): every guest links against the same IPA window
and is loaded at its own PA slot (`load_addr` = `NOVA_GUEST_IPA_BASE +
index * NOVA_GUEST_PA_STRIDE`). Scheduling is cooperative — a waiting
guest must poll + `hvc_yield()`; a bare `wfi` stalls the whole core.
The 4 KiB IVC shared page appears in every VM at `NOVA_IVC_SHM_IPA`
(protocol helpers in `common/include/ivc_shm.h`).

vGIC notes (Phase 8): the GICD/GICR frames at the QEMU virt addresses
are emulated by the hypervisor (`gic_el1.h` helpers). The vGIC is the
delivery authority: SGIs (e.g. the IVC doorbell) are enabled at reset,
but a guest that wants a PPI — including vINTID 27 from `TIMER_SET` —
must enable it at its redistributor (`gicr_wake()` + `gicr_enable()`)
first. Guests may also drive their virtual timer natively via
CNTV_CTL/TVAL: each expiry is delivered as vINTID 27 with the timer
masked (IMASK); re-arming CNTV_CTL unmasks it.

The hypervisor's HVC dispatcher recognizes these IDs. Guest programs use the inline helpers in `common/include/demo_hvc.h`.

## Manifest schema (`demo/NN_name/manifest.yml`)

```yaml
name: "01_hello"                     # required, unique
description: "..."                   # required, human-readable
phase: 5                             # required, roadmap phase number
enabled: false                       # required; CI skips demos where enabled=false
timeout_seconds: 10                  # required, total budget

# Guest binaries to load. Built binaries are searched in
# build/demo/<manifest-dir>/<binary>. Prebuilt external images may
# reside in external/cache/guests/<name> with a fetch.sh sibling.
guests:
  - name: "hello"
    binary: "hello.bin"              # file name relative to build dir
    load_addr: 0x50000000            # PA where QEMU places the binary
    ipa_base:  0x50000000            # IPA where hypervisor Stage 2 maps it
    entry:     0x50000000            # EL1 entry PC
    memory_size: 0x00100000          # IPA window size
    vcpus: 1
    uart: vuart                      # optional: none (default) | vuart

# Ordered list of output patterns the harness must observe.
expect:
  - pattern: "Hello from EL1 guest!"
    within_seconds: 5
  - pattern: "demo_exit code=0"
    within_seconds: 10
```

### Patterns

Patterns are Python `re` regular expressions matched against UART bytes. `within_seconds` is the deadline from the moment the *previous* pattern was matched (or from QEMU start for the first one).

### External guest images

For Phases 8+ that reference real OSes (Zephyr, Linux), add a `fetch.sh` in the demo directory. It must produce binaries into `external/cache/guests/<name>/` and be idempotent. CI caches this path.

## Running a demo

```
# Build hypervisor + all custom guests, then verify a single demo
./scripts/task.sh demo verify 1

# List all demos with their phase and enabled status
./scripts/task.sh demo list

# Run all enabled demos (CI uses this)
./scripts/task.sh demo verify-all

# Launch a demo interactively (no pattern checking)
./scripts/task.sh demo run 1
```

## Writing a new demo

1. Create `demo/NN_name/` with:
   - `main.c` (or sender/ + receiver/ for multi-guest demos)
   - `CMakeLists.txt` calling `add_demo_guest(NAME <sources...>)`
   - `manifest.yml` with `enabled: false`
2. Add `add_subdirectory(NN_name)` to `demo/CMakeLists.txt`.
3. Implement the hypervisor features the demo exercises.
4. Once `./scripts/task.sh demo verify NN_name` passes locally, flip `enabled: true` in the manifest. That commit marks the phase as complete.

## How the harness runs a demo

`scripts/demo_runner.py` does the following:

1. Build the hypervisor (`scripts/task.sh build`).
2. Build all custom demo guests (`cmake --build build/demo`).
3. Construct a QEMU command with the hypervisor ELF as `-kernel` and each manifest guest as a separate `-device loader,file=...,addr=...,force-raw=on`.
4. Spawn QEMU via `pexpect`, stream stdout to the console, and assert each `expect.pattern` appears within `within_seconds`.
5. Terminate QEMU on success (all patterns matched) or failure (timeout / EOF).

The harness exits 0 on PASS and non-zero on any failure, which CI uses as the gate.

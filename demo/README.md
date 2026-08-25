# NovaVisor Demo Framework

A **demo** is an EL1 guest program (or a reference OS image) plus a
`manifest.yml` saying what a run of it must show. It is both a
demonstration — this is what the hypervisor can now do — and the
verification CI gates on.

`./nova demo list` prints the catalogue: every demo, its roadmap phase,
whether CI runs it, and one line on what it proves. That listing is the
catalogue; this file is the vocabulary a manifest is written in.

## What a manifest says

```yaml
name: "03_ivc_pingpong"          # required, unique, matches the directory
description: "..."               # required, one line — `demo list` prints it
phase: 7                         # required, roadmap phase number
enabled: true                    # required; CI skips a demo that says false
timeout_seconds: 30              # required, the whole run's budget

guests:                          # what to load, and where
  - name: "ping"
    binary: "ping.bin"           # under build/demo/<demo>/, or external/cache/guests/<demo>/
    load_addr: 0x50000000        # PA slot QEMU places it at
    ipa_base:  0x50000000        # IPA the hypervisor maps it to
    entry:     0x50000000        # EL1 entry PC
    memory_size: 0x00100000      # IPA window size
    vcpus: 1
    uart: vuart                  # optional: none (default) | vuart

steps: [...]                     # what the run must show — see below
```

Optional keys:

| Key | Means |
| --- | --- |
| `config:` | the guest config YAML the hypervisor is built with (`configs/*.yml`). Omitted = `configs/default.yml` |
| `preset:` | the CMake preset — which components are underneath. Omitted = the development profile. Per variant |
| `qemu_devices:` | extra `-device` arguments. Per variant |
| `payload_mode:` | `loader` (default) or `embedded`, where the guests travel inside the ELF |
| `forbid:` | patterns that invalidate the run if they ever appear |
| `expects_panic:` | this demo's subject *is* the firmware's failure report, so that one global guard stands down |
| `variants:` | run the demo once per entry, each a full build + QEMU + verify, sharing `guests` |

A variant is `{name, config, preset, qemu_devices, steps}` — anything a
whole run can differ in. Two demos use it to run the same guest under
two configurations; four use it to run the same steps on two
compositions. A YAML anchor keeps one copy of the steps:

```yaml
variants:
  - name: "full"
    steps: &steps
      - pattern: "core 1 online"
        within_seconds: 10
  - name: "standard"
    preset: "aarch64-standard-release"
    steps: *steps
```

## The step vocabulary

A step names its kind by the key it carries, and every step takes
`within_seconds` — the deadline measured from the moment the *previous*
step was carried out.

There are six kinds, and they differ in **who is answering**. That is
the whole point of having more than one: a guest printing "ok" is the
guest's account of itself, and the hypervisor's half of the same claim
is a different fact.

### `pattern` — what reached the console

```yaml
- pattern: "pingpong: 1000 rounds ok"
  within_seconds: 20
- pattern: "~ #"                       # a shell prompt
  within_seconds: 30
  send: "uname -a\n"                   # bytes written to the guest after it matched
```

A Python `re` regular expression matched against UART bytes. `send:`
writes to the focused guest's console once the pattern has matched — the
only way a step drives a guest.

Cheap, and the only kind that works with no image behind it. Weak
evidence when the line is the guest's own claim.

### `observe` — a value the firmware published

```yaml
- observe: "smmu.stream"
  where: { stream: 16 }                # which entry, when the topic reads as a list
  equals: { state: "translate" }       # what it must read
  within_seconds: 5
```

Read out of the S layer: the machine takes its own copy of named globals
on a period of its own, and this reads that copy. Only equality, and
only on a value that stays — a sampler asked about a moment answers
whichever side of it the look landed on.

Topics come from the observation manifest
(`novakit/services/workbench/observations.py`);
`./nova inspect symbols` prints the ones a build carries, and the ones
its composition does not.

### `event` — a moment the machine recorded

```yaml
- event: "vgic.private"
  where: { vintid: 27 }
  within_seconds: 5
```

Read out of the T layer: fixed points in the firmware emit a 32-byte
record into a ring. A transition shorter than the S layer's interval is
absent from it rather than merely late, so this is what catches one.

Records are matched from an anchor the scenario moves as steps carry, so
a second `event` step means "and then this", not "this again". A hole in
the ring is reported as itself: "it did not happen" and "the reader was
too far behind to see it" are different findings.

The catalogue is `novakit/services/workbench/events.py`.

### `walk` — where an address actually lands

```yaml
- walk: "vm1.v0.el1.high"
  address: "0xffff8000807c12a4"
  equals: { output: "0x507c12a4", through: "0x50fc12a4", fault: "" }
  within_seconds: 20
```

Follows the page tables the machine built, in the regime named: a guest
VA through its own Stage 1 to an IPA, and that IPA through Stage 2 to a
PA — one answer, closing the whole chain. `fault: ""` says nothing
along the way was a fault.

### `command` — something the host asked EL2 to do

```yaml
- command: "stop 0"
  within_seconds: 10
- command: "spi 0 33"
  expect_result: "ok"                  # default; a refusal is as nameable as an acceptance
  within_seconds: 10
```

Writes one record into the command page and waits for the verdict EL2
answers with on the trace ring. The vocabulary is
`nova/abi/command_ring.h` (`mark`, `spi`, `slice`, `stop`, `reset`,
`start`); `./nova workbench command` is the terminal twin.

### `forbid` — output that invalidates the run

```yaml
forbid:
  - "edu dma: round-trip failed"
```

Not a step: a band watched for the whole run, including the drain after
the last step. The firmware's own failure report and any nonzero guest
exit are watched everywhere without being listed — those two strings are
read from the headers that define them
(`hal/panic.hpp`, `nova/abi/hvc_abi.h`), so a demo never spells them.

### Which kind to reach for

Prefer the one that answers the claim being made. A guest counting its
own interrupts is a `pattern`; the hypervisor posting them is an
`event`. Do not add a predicate to a step merely because the topic
exists — `./nova ci static` reports how much of what the workbench can
see is ever held to, and a predicate written to make that number move is
worth less than the honest gap.

## The guest side

Guest programs are freestanding C linked against `common/`:
`startup.S` sets up the stack, zeroes BSS, calls `main()` and exits
through the ABI; `linker.ld.S` takes the guest window from
`nova/abi/guest_layout.h`, the same header Stage 2 is built from.

The headers in `common/include/` are the contracts a guest speaks:

| Header | What it is for |
| --- | --- |
| `demo_hvc.h` | the NovaVisor hypercall ABI — IDs from `nova/abi/hvc_abi.h` |
| `guest_psci.h` | PSCI over the standard SMCCC range: reset, off, CPU_ON/OFF, AFFINITY_INFO, FEATURES |
| `guest_smccc.h` | the SMCCC Arch service: version, discovery, the speculation workarounds |
| `gic_el1.h` | GICD/GICR programming and the ICV CPU interface |
| `pl011_el1.h` | the emulated PL011 |
| `fdt_el1.h` | reading the DTB the hypervisor passes in x0 |
| `ivc_shm.h` | the mailbox protocol on the IVC shared page |
| `guest_ring.h` | the lock-free SPSC rings in that page (`nova/abi/ivc_ring.h`) |
| `el1_mmu.h` | building a guest's own Stage 1 tables |
| `edu_el1.h` | the EDU test device's registers |

Function IDs are never restated here — `nova/abi/hvc_abi.h` is the one
place they are written, shared by the dispatcher, the guest stubs and the
startup assembly.

### What a guest has to know

**Interrupts.** The GICD/GICR frames sit at the standard addresses but
are unmapped in Stage 2: every access traps and the vGIC emulates it.
This is the path an unmodified OS takes. SGIs are enabled at reset, but
a PPI — including the virtual timer's INTID 27 — must be enabled at the
guest's own redistributor (`gicr_wake()`, `gicr_enable()`) before it
will be delivered. The CPU interface is *not* MMIO: EL1 `ICC_*` accesses
are hardware-virtualized into the `ICV_*` view.

**Timers.** A guest drives `CNTV_CTL`/`CNTV_TVAL` directly; those are
never trapped. Each expiry arrives as vINTID 27 with the timer masked,
and re-arming unmasks it. Every VM gets a private `CNTVOFF`, so
`CNTVCT` restarts near zero on every boot and reboot.

**Placement.** Every guest links against the same IPA window and is
loaded at its own PA slot (`load_addr` = `NOVA_GUEST_IPA_BASE + index *
NOVA_GUEST_PA_STRIDE`); Stage 2 separates them. Slot index also decides
the physical core: slots 0/1 run on core 0, slots 2/3 on core 1 — so a
manifest puts a guest on the other core purely by `load_addr`. Guests on
one core are time-shared and preemptible; a guest waiting on another
must poll and `hvc_yield()` or park in `wfi`.

**Power.** Guests control their own through PSCI, not through a private
hypercall: `SYSTEM_OFF` stops the VM, `SYSTEM_RESET` warm-reboots it
from the pristine image (the IVC shared page survives; the guest window
does not). `CPU_ON` brings up a sibling vCPU at the stub in
`common/secondary.S` — pass the stack top as `context_id`; BSS is not
re-zeroed. A vCPU identifies itself by MPIDR Aff0, programs its own
redistributor frame (`gicr_wake_at()`), and sends IPIs with
`icc_send_sgi()`, whose `ICC_SGI1R` write is trapped and routed across
physical cores.

**Console.** Every line printed through the hypercall ABI or a guest's
own PL011 arrives `[vmN]`-tagged and whole — the mux assembles per-VM
line buffers onto the one physical UART, so two VMs printing in parallel
never splice. A VM whose descriptor says `uart: vuart` sees an emulated
PL011 at the standard address: TX by polling `DR`, RX by the UART SPI
(33), which must be enabled at the distributor (`gicd_enable_spi()`) and
unmasked in `IMSC`. Host input goes to the focused VM; `Ctrl-T` (0x14)
cycles focus across live vuart VMs.

**Configuration.** The hypervisor's guest table is built at boot from
per-guest DTBs generated out of a config YAML. Each boot vCPU gets its
DTB's IPA in x0 — parse it with `fdt_el1.h` to learn the window size and
vCPU count, rather than assuming them.

## Running one

```bash
./nova demo list             # the catalogue
./nova demo verify 3         # by ID, or by directory name
./nova demo verify --all     # every enabled demo — what CI runs
./nova demo run 3            # interactive, no checking
./nova demo run 3 --debug    # ...halted, with a GDB stub on :1234
./nova demo soak 15 --runs 10
./nova workbench serve 3     # the same run, in a browser
```

A demo with external images (Zephyr, Linux) carries a `fetch.sh` that
builds them into `external/cache/guests/<demo>/` and is idempotent;
`./nova demo fetch 13` runs it, and CI caches that path.

## Writing one

1. `demo/NN_name/` with `main.c`, a `CMakeLists.txt` calling
   `add_demo_guest(NAME <sources...>)`, and a `manifest.yml` with
   `enabled: false`.
2. `add_subdirectory(NN_name)` in `demo/CMakeLists.txt`.
3. Implement the hypervisor features it exercises.
4. When `./nova demo verify NN_name` passes, flip `enabled: true`. That
   commit marks the phase complete.

Guests are built GP-register-only by default; a demo that means to use
FP/SIMD says so with the `FPSIMD` keyword, so its use is a decision
rather than an artifact of `-O2` vectorization.

## How a run happens

1. Build the hypervisor for the variant's `preset`, after syncing its
   `config` and payload selection into the preset's build tree.
2. Build every in-tree guest.
3. Compose the QEMU command: the hypervisor ELF as `-kernel`, each guest
   as its own `-device loader` (skipped under `payload_mode: embedded`,
   where they travel inside the ELF), plus the variant's `qemu_devices`.
4. When any step needs more than the console, attach the observation
   surfaces — guest RAM as a file the reader maps, a QMP socket, a GDB
   socket — and open the machine behind them.
5. Spawn QEMU, stream its output, and carry out each step within its
   deadline while the forbidden bands stay watched.
6. Terminate on success or failure; exit 0 only if every step was
   carried out. CI gates on that.

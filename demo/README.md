# NovaVisor Demo Framework

Each NovaVisor roadmap phase is validated by a **demo** — an EL1 guest program (or a reference OS image) that exercises the features introduced in that phase. The demo is both a *demonstration* (what the hypervisor can now do) and a *verification* (automated pass/fail check in CI).

## Directory layout

```
demo/
├── README.md                    # this file
├── CMakeLists.txt               # builds all custom EL1 guests
├── common/
│   ├── startup.S                # EL1 entry: stack, BSS, call main(), HVC_EXIT
│   ├── linker.ld                # default linker script (IPA base 0x50000000)
│   └── include/demo_hvc.h       # HVC ABI helpers
├── 01_hello/                    # Phase 5 demo
│   ├── CMakeLists.txt
│   ├── main.c
│   └── manifest.yml
├── 02_timer/                    # Phase 6 demo (created when Phase 6 starts)
├── 03_ivc_pingpong/             # Phase 7
├── 04_zephyr/                   # Phase 8 (references external Zephyr image)
├── 05_linux/                    # Phase 9 (references external Linux kernel)
├── 06_mixed_workload/           # Phase 10
├── 07_fault_recovery/           # Phase 11
├── 08_configurable/             # Phase 12
└── 09_passthrough/              # Phase 13
```

## Hypercall ABI (demo ↔ hypervisor contract)

| HVC imm16 | Name | Args | Description |
| --- | --- | --- | --- |
| `0x1000` | `PUTS`  | x1=ptr, x2=len | Write string to UART via hypervisor |
| `0x1001` | `PUTC`  | x1=char        | Write one character |
| `0x1002` | `EXIT`  | x1=code        | Guest termination. Hypervisor logs `demo_exit code=<n>` |
| `0x1003` | `YIELD` | —              | Yield the VCPU (Phase 7+) |
| `0x1004` | `HEARTBEAT` | x1=vm_id   | Liveness tick (Phase 11) |
| `0x1100..0x11FF` | IVC range    | see Phase 7 | Inter-VM communication |
| `0x1200..0x12FF` | Timer range  | see Phase 6 | Timer helpers |

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
./scripts/task.sh demo verify 01_hello

# List all demos with their phase and enabled status
./scripts/task.sh demo list

# Run all enabled demos (CI uses this)
./scripts/task.sh demo verify-all

# Launch a demo interactively (no pattern checking)
./scripts/task.sh demo run 01_hello
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

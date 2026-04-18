# NovaVisor

NovaVisor is a C++23-based, component-assembled embedded multi-core hypervisor that pursues zero-cost abstraction by eliminating runtime callback overhead. It targets ARM64 (AArch64) / EL2 bare-metal; the QEMU `virt` board is the primary development target, with real-hardware ports (Raspberry Pi 4 / i.MX8) on the roadmap.

Core technologies: Intel `compile-time-init-build (cib)` for static composition, ETL for heap-free containers, C++23 freestanding standard library (`std::expected`, `std::span`, `std::mdspan`), and the ARM GNU cross toolchain `aarch64-none-elf-gcc` 15.2.Rel1.

---

## 🚀 Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/<org>/novavisor.git
cd novavisor
```

### 2. Set up the development environment

Pick one of the two methods below. Both produce the same toolchain, pre-commit hooks, and `scripts/task.sh` interface; the only difference is whether the environment lives inside a container.

#### Method A — VS Code Dev Container (recommended ✨)

This is the easiest and most reliable method for a consistent C++23 IntelliSense experience and host OS isolation.

1. Install **Docker** and **VS Code**, then add the `Dev Containers` extension to VS Code.
2. Open the project root in VS Code.
3. Either click the prompt in the bottom right or open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) and run **"Dev Containers: Reopen in Container"**.
4. On first run the container image is built from `.devcontainer/Dockerfile` (Debian 13 base + `aarch64-none-elf-gcc` 15.2.Rel1 + QEMU + clang-format/tidy + pre-commit). This takes about 3–5 minutes.
5. When the container is ready, open the integrated terminal and verify: `aarch64-none-elf-gcc --version`.

#### Method B — Manual local setup (Linux / WSL / macOS)

Use the automated setup script. It detects the host CPU architecture (x86_64 or aarch64) and fetches the matching toolchain tarball.

1. Install the apt dependencies and the ARM GNU toolchain:
   ```bash
   ./scripts/setup_env.sh
   ```
2. The toolchain lands under `.toolchain/` at the project root, and pre-commit hooks are installed.
3. Each new terminal session needs the toolchain on its PATH:
   ```bash
   source .toolchain/env.sh
   ```
4. You can now invoke any build / test / demo command via `./scripts/task.sh`.

---

## 🔧 Development Workflow

All routine tasks go through `scripts/task.sh`. It handles CMake preset selection, toolchain activation, and concurrency.

### Build

```bash
./scripts/task.sh build               # Debug cross-build (aarch64)
./scripts/task.sh build --release     # Release
./scripts/task.sh build --clean       # wipe build/ first
```

### Run / Debug in QEMU

```bash
./scripts/task.sh run                 # launch QEMU; Ctrl-A x to exit
./scripts/task.sh debug               # QEMU halted with GDB server on :1234
```

While `debug` is running, connect in a second terminal:

```bash
aarch64-none-elf-gdb build/aarch64-debug/novavisor.elf -ex 'target remote :1234'
```

### Host unit tests

```bash
./scripts/task.sh test
```

Runs the GTest suite for header-only utilities (ESR parser, Stage 2 descriptor encoding, Stage 2 identity-map builder). These tests require no toolchain and execute as a native x86_64 binary.

### Demo verification — the phase gate

Each roadmap phase is validated by a **demo** in `demo/NN_name/`. A demo consists of an EL1 guest program (or a reference OS image) plus a `manifest.yml` declaring expected UART output patterns. The demo simultaneously demonstrates the phase's feature set and gates phase completion.

```bash
./scripts/task.sh demo list           # show all demos and their enabled status
./scripts/task.sh demo run 01_hello   # interactive launch (no pattern check)
./scripts/task.sh demo verify 01_hello
./scripts/task.sh demo verify-all     # every enabled demo — used by CI
```

Before a phase is complete its demo has `enabled: false` in the manifest, and `demo verify-all` skips it. Full details live in [`demo/README.md`](demo/README.md).

### Format & lint

```bash
./scripts/task.sh format              # apply clang-format
./scripts/task.sh format --check      # dry-run (CI uses this)
./scripts/task.sh lint                # clang-tidy over the release build cache
```

### Misc inspection helpers

```bash
./scripts/task.sh size                # section sizes of novavisor.elf
./scripts/task.sh objdump             # disassembly interleaved with source
./scripts/task.sh clean               # remove build/
```

---

## ✅ Before Pushing — run the CI pipeline locally

```bash
./scripts/task.sh ci
```

This is equivalent to, and parallelizes wherever safe:

```
format --check   ∥   build --release   →   lint --release   →   test   →   demo verify-all
```

If `ci` passes locally, the branch is expected to pass GitHub Actions as well. A local green run is effectively mandatory before opening a pull request.

---

## 🤖 Continuous Integration

`.github/workflows/ci.yml` runs on every push and every PR targeting `main`:

1. Install apt dependencies — CMake, Ninja, clang-{format,tidy}, `qemu-system-aarch64`, `python3-yaml`, `python3-pexpect`.
2. Execute `./scripts/setup_env.sh` to fetch and unpack the ARM toolchain.
3. Check formatting, build Release, lint, run host tests, and run `demo verify-all`.
4. On failure, upload `build/demo/**/*.{bin,elf}` as an artifact so QEMU-reproducible binaries are available from the run logs.

Dependabot (`.github/dependabot.yml`) updates the devcontainer base image weekly.

---

## 🤝 Contribution Guide

### Branching model

`main` is the only long-lived branch. Work on feature branches named by type:

- `feat/<scope>` — new feature (typically a roadmap phase or a slice of one)
- `fix/<scope>` — bug fix
- `test/<scope>` — tests only
- `build/<scope>` — build system, CMake, toolchain
- `ci/<scope>` — CI pipeline, workflows
- `docs/<scope>` — documentation
- `refactor/<scope>` — code movement without behavior change

Example: `feat/phase6-vgicv3-list-register`.

### Commit message convention

Conventional Commits style. The scope (parenthesized segment) is optional but preferred for anything non-trivial. Wrap the body at 72 columns. Explain **why** more than **what**.

```
feat(core_mmu): activate Stage 2 MMU with identity-mapped guest window
test(core_mmu): add Stage 2 descriptor encoding with host GTest
build(devcontainer): install python deps for demo harness
ci: run demo verify-all as the final pipeline gate
```

### Pre-commit hooks

Installed by both setup methods. On `git commit`:

- **File hygiene** — `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files`.
- **clang-format** — auto-fixes formatting; if it modifies files, re-stage them and retry the commit.
- **shellcheck** — fails the commit on shell script warnings.

`clang-tidy` is **not** a pre-commit hook (cross-compile flags are only available after CMake configure). Run it manually via `./scripts/task.sh lint` before pushing. If a hook fails, fix the underlying issue — do not bypass with `--no-verify`.

### Demo-driven phase completion

Every roadmap phase ends with its demo's `manifest.enabled` flipping from `false` to `true`. The recommended flow:

1. **Scaffold** — create `demo/NN_name/{main.c, CMakeLists.txt, manifest.yml}` with `enabled: false`. (If you already did this in an earlier commit, skip.)
2. **Implement** — land hypervisor features in small atomic commits. Each commit keeps `./scripts/task.sh ci` green; Host-testable units ship with GTest cases.
3. **Integrate** — once `./scripts/task.sh demo verify NN_name` passes locally, the demo is ready.
4. **Close** — the final commit of the phase flips `enabled: true` in the manifest. This commit is the phase-completion marker; CI now gates every future PR against this demo.

### Pull requests

- Title follows the same convention as the lead commit (`feat(scope): …`).
- Description: motivation, summary of changes, test plan (which `task.sh` subcommands were run; whether `demo verify-all` is green).
- Keep a PR focused on one phase — or one logical slice within a phase.
- CI must be green before merge. No force-push to `main`.

---

## 📜 License

Apache License 2.0. See `LICENSE` once added, or the license header in source files.

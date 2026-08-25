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

Both setup methods use the same pinned tools and the same `./nova` interface.

#### Method A — VS Code Dev Container (recommended ✨)

This is the easiest and most reliable method for a consistent C++23 IntelliSense experience and host OS isolation.

1. Install **Docker** and **VS Code**, then add the `Dev Containers` extension to VS Code.
2. Open the project root in VS Code.
3. Either click the prompt in the bottom right or open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) and run **"Dev Containers: Reopen in Container"**.
4. The container builds from the shared toolchain image definition used by CI.
5. When the container is ready, open the integrated terminal and verify: `aarch64-none-elf-gcc --version`.

#### Method B — Manual local setup (Linux / WSL / macOS)

Use the automated setup script. It detects the host CPU architecture (x86_64 or aarch64) and fetches the matching toolchain tarball.

1. Install the apt dependencies and the ARM GNU toolchain:
   ```bash
   ./bootstrap
   ```
2. The toolchain and pinned Typer CLI environment land under `.toolchain/`, and pre-commit hooks are installed.
3. Each new terminal session needs the toolchain on its PATH:
   ```bash
   source .toolchain/env.sh
   ```
4. Run build, test, and demo commands through `./nova`.

---

## 🔧 Development Workflow

All routine tasks go through `./nova`.

Common workspace operations are top-level commands. Scoped operations use
`nova <domain> <operation>`; for example, `nova inspect size` and
`nova firmware verify qemu-tfa`.

### Shell completion

Nova provides tab completion for commands, options, and typed values through
Typer. Run the installer once through its repository path; it registers the
repository root on `PATH`, installs completion, and reports every file it created,
updated, or left unchanged:

```bash
./nova completion install
exec "$SHELL"
```

Shell detection is automatic. To select one explicitly, pass `--shell zsh`,
`--shell bash`, `--shell fish`, `--shell powershell`, or `--shell pwsh`.
The generated startup block checks the current `PATH` before prepending the
repository, and repeated installs replace the same block instead of appending
duplicates.

To remove both the completion script and the managed `PATH` registration:

```bash
nova completion uninstall
exec "$SHELL"
```

Completion then follows the full command hierarchy:

```text
nova d<TAB>             # completes "demo"
nova demo v<TAB>        # completes "verify"
nova demo run <TAB>     # lists demo directory names
nova build --p<TAB>     # completes "--preset"
```

### Build

```bash
./nova build            # Debug cross-build (aarch64)
./nova build --release  # Release
./nova build --clean    # wipe build/ first
```

### Run / Debug in QEMU

```bash
./nova run          # launch QEMU; Ctrl-A x to exit
./nova run --debug  # QEMU halted with GDB server on :1234
```

While `debug` is running, connect in a second terminal:

```bash
aarch64-none-elf-gdb build/aarch64-debug/novavisor.elf -ex 'target remote :1234'
```

### One-click debugging in VS Code

`.vscode/{launch,tasks,settings,extensions}.json` ship a ready-made configuration:

- **Hypervisor only** — the CMake Tools **Debug** button (or `F5` with `QEMU Remote (aarch64-debug)`) starts `./nova run --debug` and attaches `cppdbg` to port `1234`.
- **Demo guest + hypervisor** — `F5` with `QEMU Demo Debug` prompts for a demo, runs `./nova demo run <name> --debug`, and loads the generated guest symbol file.

Prerequisites:

- Install the recommended extensions when VS Code prompts (`ms-vscode.cpptools`, `ms-vscode.cmake-tools`).
- Toolchain must be available via the devcontainer or `./bootstrap`. `novakit/aarch64-gdb` resolves the GDB binary in both layouts.
- Linux / macOS / WSL only — the `postDebugTask` uses `pkill` to tear down QEMU.
- The GDB stub uses port `1234`; run at most one debug session per host to avoid collisions.

#### Personal overrides

VS Code settings precedence is `Default < User < Workspace (.code-workspace) < Folder (.vscode/)`. Keep personal tweaks at the narrowest layer that fits so the committed `.vscode/` stays shared:

- **User Settings** for globally personal preferences (theme, font, keybindings). Command Palette → `Preferences: Open User Settings (JSON)`.
- **`novavisor.code-workspace`** for repo-specific personal overrides, extra launch configs, or one-off tasks. Copy the template and open it via `File > Open Workspace from File...`:
  ```bash
  cp novavisor.code-workspace.example novavisor.code-workspace
  ```
  `*.code-workspace` is git-ignored, so personal workspaces stay local. Settings in the `.code-workspace` file override the folder `settings.json`; `launch` and `tasks` entries are *merged* with the committed ones (personal additions appear alongside the shared configs in the picker).

### Host unit tests

```bash
./nova test
```

Runs the GTest suite for header-only utilities (ESR parser, Stage 2 descriptor encoding, Stage 2 identity-map builder). These tests require no toolchain and execute as a native x86_64 binary.

### Demo verification — the phase gate

Each roadmap phase is validated by a **demo** in `demo/NN_name/`. A demo consists of an EL1 guest program (or a reference OS image) plus a `manifest.yml` saying what a run of it must show. The demo simultaneously demonstrates the phase's feature set and gates phase completion.

```bash
./nova demo list          # show all demos and their enabled status
./nova demo run 1         # interactive launch (no pattern check)
./nova demo verify 1      # ID or full directory name
./nova demo verify --all  # every enabled demo
```

Before a phase is complete its demo has `enabled: false` in the manifest, and
`demo verify --all` skips it.

A manifest says more than which console lines must appear: a step may
also read a value the firmware published, name a moment it recorded,
walk an address through the page tables it built, or issue a command and
wait for the verdict EL2 answers with. The vocabulary, and which kind
answers which question, is [`demo/README.md`](demo/README.md).

### Live workbench

```bash
./nova workbench serve 10  # live console/event UI on http://127.0.0.1:8787/
```

A browser UI over a live QEMU session: per-VM consoles with UART input,
classified event log, live firmware-state panels polled straight out of guest
RAM, and a pause button that freezes the machine to read EL2 system registers.
The full manual — user guide and developer guide — lives in
[`WORKBENCH.md`](WORKBENCH.md).

### Format & lint

```bash
./nova format          # apply clang-format
./nova format --check  # dry-run
./nova lint            # run-clang-tidy over the debug compile database
```

### Misc inspection helpers

```bash
./nova inspect size         # section sizes of novavisor.elf
./nova inspect disassemble  # disassembly interleaved with source
./nova clean                # remove build/
```

---

## ✅ Before Pushing — run the CI pipeline locally

```bash
./nova ci all
```

The same `host`, `static`, and `runtime` handlers run locally and in GitHub Actions.

---

## 🤖 Continuous Integration

The CI workflow runs on every push and pull request targeting `main`:

- **host** — formatting, native CTest, Python tests, and architecture boundaries.
- **static** — Ruff, ShellCheck, actionlint, and cross-target clang-tidy.
- **runtime** — release profiles, firmware handoff, and enabled demo verification.
- **ci** — aggregates all lanes into one stable branch-protection gate.

Static and runtime use the same digest-pinned toolchain image and run in parallel. A newer
push to the same branch or PR cancels the superseded run.

Dependabot (`.github/dependabot.yml`) updates the devcontainer base image and the workflow's GitHub Actions weekly.

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
ci: verify all demos as the final pipeline gate
```

### Pre-commit hooks

Installed by both setup methods. On `git commit`:

- **File hygiene** — `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files`.
- **clang-format** — auto-fixes formatting; if it modifies files, re-stage them and retry the commit.
- **shellcheck** — fails the commit on shell script warnings.

`clang-tidy` is **not** a pre-commit hook because it needs a configured cross build. Run `./nova lint` before pushing. If a hook fails, fix the underlying issue rather than bypassing it.

### Demo-driven phase completion

Every roadmap phase ends with its demo's `manifest.enabled` flipping from `false` to `true`. The recommended flow:

1. **Scaffold** — create `demo/NN_name/{main.c, CMakeLists.txt, manifest.yml}` with `enabled: false`.
2. **Implement** — land hypervisor features in small atomic commits. Each commit keeps `./nova ci all` green; host-testable units ship with GTest cases.
3. **Integrate** — once `./nova demo verify NN_name` passes locally, the demo is ready.
4. **Close** — the final commit of the phase flips `enabled: true` in the manifest. This commit is the phase-completion marker; CI now gates every future PR against this demo.

### Pull requests

- Title follows the same convention as the lead commit (`feat(scope): …`).
- Description: motivation, summary of changes, and the `./nova` checks that passed.
- Keep a PR focused on one phase — or one logical slice within a phase.
- CI must be green before merge. No force-push to `main`.

---

## 📜 License

Apache License 2.0. See `LICENSE` once added, or the license header in source files.

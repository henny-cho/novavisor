# NovaVisor

NovaVisor is a C++23-based, component-assembled embedded multi-core hypervisor aiming to achieve Zero-cost Abstraction by eliminating runtime callback overhead.

---

## 🚀 Getting Started (Development Setup Guide)

To develop for this project, setting up the official ARM cross-compiler (version 15.2.Rel1 or higher) and CMake environment is essential. Depending on your preference and convenience, you can choose one of the following **two methods**.

### Method 1: Using VSCode DevContainer (Highly Recommended ✨)

This is the easiest and most reliable method to ensure a consistent C++23 IntelliSense experience and host OS isolation.

1. Install **Docker** and **VSCode** on your PC, then install the `Dev Containers` extension in VSCode.
2. Open the NovaVisor project root folder with VSCode.
3. Click the prompt at the bottom right of the screen or open the Command Palette (`Ctrl+Shift+P` or `Cmd+Shift+P`) and select **"Dev Containers: Reopen in Container"**.
4. (On first run) It will boot an Ubuntu 24.04 base Docker image in the background and automatically download and configure system packages, `pre-commit`, and the ARM cross-toolchain. (Takes about 3-5 minutes)
5. Once fully booted, open the VSCode integrated terminal and check `aarch64-none-elf-gcc --version` to verify the setup, then begin development.

---

### Method 2: Manual Local Setup for General Users (Linux / WSL / macOS)

If you are setting up a server daemon CI environment or prefer terminal-based development in your local OS without VSCode extensions, use the automated setup script. (It automatically detects and supports both x86_64 and aarch64 host CPU architectures.)

1. Open your terminal and run the script to download necessary distribution (apt) system dependencies and the latest ARM GNU toolchain.
   ```bash
   ./scripts/setup_env.sh
   ```
2. Once the process completes without errors, the compiler will be located in the hidden `.toolchain` folder inside the project, and the `pre-commit` Git hooks will be installed.
3. Every time you open a new terminal session for project work, load the toolchain into your environment variables with the following command:
   ```bash
   source .toolchain/env.sh
   ```
4. You can now execute C++23 targeted `CMake` builds in that terminal session using `./scripts/task.sh build`.

---

## 🛡 Code Quality & Linting (Quality Assurance)

To entirely prevent spaghetti code and memory corruption (e.g., dynamic allocation using `new` or `malloc`) that can occur in a zero-cost bare-metal structure, a **Pre-commit hook** system has been configured.

In a fully set-up environment, whenever a user executes the `git commit` command, the `.clang-format` rules and `.clang-tidy` static analysis rules run in the background. **If dangerous code or incorrect formatting is detected, the commit will be immediately and automatically rejected.**

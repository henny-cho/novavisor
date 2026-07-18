#pragma once

// hal/console.hpp
//
// Board-agnostic console facade. This is the ONE place where generic code
// binds to the active board's UART: components include this header — never
// a hal/board_*/ one — so porting to a new board touches only this file
// (and the new board driver).

#include "hal/board/qemu_virt/include/uart.hpp"
#include "nova/fmt.hpp"
#include "nova/sync.hpp"

#include <cstdint>
#include <string_view>

namespace nova::console {

// The UART is one shared FIFO — serialize each write across cores.
// Hypervisor lines assembled from several write calls can still
// interleave between calls; line-level ownership is console_mux's job.
inline sync::SpinLock g_lock;

inline void write(std::string_view sv) noexcept {
  sync::Guard guard{g_lock};
  board::qemu_virt::uart_write(sv);
}

// Null-terminated C strings (extern "C" boundaries, __func__-style values).
inline void write(const char* str) noexcept {
  sync::Guard guard{g_lock};
  board::qemu_virt::uart_puts(str);
}

// 16 zero-padded lowercase hex digits, no "0x" prefix.
inline void write_hex64(std::uint64_t v) noexcept {
  fmt::HexBuf buf{};
  write(fmt::to_hex64(v, buf));
}

// Base 10, no leading zeros.
inline void write_dec64(std::uint64_t v) noexcept {
  fmt::DecBuf buf{};
  write(fmt::to_dec64(v, buf));
}

} // namespace nova::console

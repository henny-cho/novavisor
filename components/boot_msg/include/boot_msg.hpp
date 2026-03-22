#pragma once

// Boot Message Component
//
// Extends cib::RuntimeStart — runs once after all initialization is complete.
// Uses an ETL fixed-capacity array to compose the boot banner, demonstrating
// static (heap-free) container usage without depending on strlen/memcpy.
// The banner bytes are written to the PL011 UART via uart_puts().

#include "hal/board_qemu_virt/include/uart.hpp"

#include <cib/top.hpp>
#include <flow/flow.hpp>
#include <string_view>

namespace novavisor {
namespace boot_msg_detail {

constexpr std::string_view BANNER = "NovaVisor Booted! [CIB + ETL + std::span]\n";

} // namespace boot_msg_detail

struct boot_msg_component {
  constexpr static auto PRINT_BOOT_MSG =
      flow::action<"boot_msg">([]() noexcept { board::qemu_virt::uart_puts(boot_msg_detail::BANNER.data()); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*PRINT_BOOT_MSG));
};

} // namespace novavisor

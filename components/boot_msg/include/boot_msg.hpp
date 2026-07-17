#pragma once

// Boot Message Component
//
// Extends cib::RuntimeStart — runs once after all initialization is complete.
// Uses a std::string_view banner constant, preserving the string length
// without relying on null-termination.

#include "hal/console.hpp"

#include <cib/top.hpp>
#include <flow/flow.hpp>
#include <string_view>

namespace nova {
namespace boot_msg_detail {

constexpr std::string_view BANNER = "NovaVisor Booted! [CIB + ETL + std::span]\n";

} // namespace boot_msg_detail

struct boot_msg_component {
  constexpr static auto PRINT_BOOT_MSG =
      flow::action<"boot_msg">([]() noexcept { console::write(boot_msg_detail::BANNER); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*PRINT_BOOT_MSG));
};

} // namespace nova

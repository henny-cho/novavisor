#pragma once

#include "hal/panic.hpp"

#include <stdx/ct_string.hpp>
#include <string_view>

namespace nova {

// Custom stdx panic handler for bare-metal. Any cib/stdx assertion
// failure (e.g. calling an uninitialized service) ends in the same
// first-failure report every other fatal EL2 path uses.
struct NovaPanicHandler {
  template <typename... Args>
  static auto panic(Args&&... /*args*/) noexcept -> void {
    ::nova::panic::fail("stdx assertion failed");
  }

  template <stdx::ct_string S, typename... Args>
  static auto panic(Args&&... /*args*/) noexcept -> void {
    ::nova::panic::fail(std::string_view{S.data(), S.size()});
  }
};

} // namespace nova

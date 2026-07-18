// Override the default stdx panic handler with our bare-metal implementation.
// This must be in a .cpp (not a header) to avoid multiple-definition errors.

#include "nova_panic/nova_panic.hpp"

#include "hal/console.hpp"

#include <stdx/panic.hpp>

namespace stdx {
// Specialize the panic_handler variable template for the default (empty)
// template argument list. This replaces the no-op default_panic_handler
// with our UART-based halt implementation.
template <>
inline auto panic_handler<> = nova::NovaPanicHandler{};
} // namespace stdx

namespace std {

// Freestanding definition of libstdc++'s precondition-violation sink
// (normally provided by the hosted runtime). With this in the link,
// std::array/std::string_view subscript and friends are usable in every
// component at any optimization level — and an out-of-bounds access
// panics instead of silently corrupting memory.
// No [[noreturn]] here: GCC's c++config.h declares it via _GLIBCXX_NORETURN,
// but Clang (clang-tidy) sees that first declaration without the attribute
// and rejects a definition that adds it. The attribute is inherited from the
// declaration; the body ends in nova::halt() either way.
void __glibcxx_assert_fail(const char* /*file*/, int /*line*/, const char* function, const char* condition) noexcept {
  using nova::console::write;
  write("[NOVA PANIC] libstdc++ assertion failed: ");
  write(condition != nullptr ? condition : "?");
  write("\n  in: ");
  write(function != nullptr ? function : "?");
  write("\n");
  nova::halt();
}

} // namespace std

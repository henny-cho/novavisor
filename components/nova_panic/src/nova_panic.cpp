// Override the default stdx panic handler with our bare-metal implementation.
// This must be in a .cpp (not a header) to avoid multiple-definition errors.

#include "components/nova_panic/include/nova_panic.hpp"

#include <stdx/panic.hpp>

namespace stdx {
// Specialize the panic_handler variable template for the default (empty)
// template argument list. This replaces the no-op default_panic_handler
// with our UART-based halt implementation.
template <> inline auto panic_handler<> = nova::NovaPanicHandler{};
} // namespace stdx

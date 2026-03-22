// components/nova_panic/include/pw_assert_backend/assert_backend.h
//
// pw_assert backend for NovaVisor bare-metal.
// Included by pw_assert/assert.h via:
//   #include "pw_assert_backend/assert_backend.h"
//
// All three macros converge on pw_assert_HandleFailure() which calls
// nova_panic (UART write + WFI halt) — the same path as CIB stdx::panic.

#pragma once

// clang-format off
extern "C" {
[[noreturn]] void pw_assert_HandleFailure(void);
}

#define PW_ASSERT_HANDLE_FAILURE(expression)                                   \
    pw_assert_HandleFailure()

#define PW_HANDLE_ASSERT_FAILURE(condition_string, ...)                        \
    pw_assert_HandleFailure()

#define PW_HANDLE_ASSERT_BINARY_COMPARE_FAILURE(                               \
    arg_a_str, arg_a_val, comparison_op_str, arg_b_str, arg_b_val, type_fmt,  \
    ...)                                                                        \
    pw_assert_HandleFailure()
// clang-format on

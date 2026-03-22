// components/nova_panic/include/pw_assert_backend/check_backend.h
//
// pw_assert.check backend for NovaVisor bare-metal.
// Included by pw_assert/check.h via:
//   #include "pw_assert_backend/check_backend.h"
//
// Delegates to assert_backend.h which defines the shared
// pw_assert_HandleFailure() entry point.

#pragma once

#include "pw_assert_backend/assert_backend.h"

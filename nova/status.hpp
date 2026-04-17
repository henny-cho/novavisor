#pragma once

#include <cstdint>

namespace nova {

// Canonical status taxonomy for NovaVisor HAL / component APIs.
// Intended as the E type in std::expected<T, nova::Status> (nova::Result<T>).
// Values follow a standard status code vocabulary for readability, but the
// numeric encoding is not API; do not persist or transmit over the wire.
enum class Status : std::uint8_t {
  Ok = 0,
  Cancelled,
  Unknown,
  InvalidArgument,
  DeadlineExceeded,
  NotFound,
  AlreadyExists,
  PermissionDenied,
  ResourceExhausted,
  FailedPrecondition,
  Aborted,
  OutOfRange,
  Unimplemented,
  Internal,
  Unavailable,
  DataLoss,
};

[[nodiscard]] constexpr auto ok(Status s) noexcept -> bool {
  return s == Status::Ok;
}

} // namespace nova

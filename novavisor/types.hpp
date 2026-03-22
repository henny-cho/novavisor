#pragma once

// novavisor/types.hpp
//
// Zero-overhead type alias hub for the NovaVisor project.
//
// All ETL and Pigweed types used across components are re-exported under the
// novavisor:: namespace via 'using' declarations. No wrapper classes, no
// virtual dispatch, no additional code generation — a 'using' alias is resolved
// entirely at compile time and produces identical object code to using the
// underlying type directly.
//
// Usage:
//   #include "novavisor/types.hpp"
//   novavisor::Vec<uint32_t, 16> regs;   // == etl::vector<uint32_t, 16>
//
// Intentional omissions (see comments at bottom of file).

#include <etl/bip_buffer_spsc_atomic.h>
#include <etl/circular_buffer.h>
#include <etl/delegate.h>
#include <etl/deque.h>
#include <etl/flat_map.h>
#include <etl/flat_set.h>
#include <etl/function.h>
#include <etl/list.h>
#include <etl/queue_spsc_atomic.h>
#include <etl/unordered_map.h>
#include <etl/vector.h>

// Pigweed headers are included only if the corresponding pw_ CMake target is
// linked. Guard each block so this file remains includable in host-side unit
// tests that may not link all Pigweed modules.
#if __has_include("pw_span/span.h")
#include "pw_span/span.h"
#endif
#if __has_include("pw_result/result.h")
#include "pw_result/result.h"
#include "pw_status/status.h"
#endif
#if __has_include("pw_string/string.h")
#include "pw_string/string.h"
#include "pw_string/string_builder.h"
#endif

namespace novavisor {

// =============================================================================
// ETL Container Aliases
// =============================================================================

/// Dynamic array with compile-time maximum capacity. Heap-free.
template <typename T, std::size_t N> using Vec = etl::vector<T, N>;

/// Double-ended queue with compile-time maximum capacity.
template <typename T, std::size_t N> using Deque = etl::deque<T, N>;

/// Doubly linked list with compile-time maximum node count.
template <typename T, std::size_t N> using List = etl::list<T, N>;

/// Sorted array-backed map. Iteration is cache-friendly; insertion is O(N).
/// Prefer over HashMap when N is small (< ~32) or ordered iteration is needed.
template <typename K, typename V, std::size_t N> using FlatMap = etl::flat_map<K, V, N>;

/// Hash map with O(1) average lookup. Use when N > ~32 and order is irrelevant.
template <typename K, typename V, std::size_t N> using HashMap = etl::unordered_map<K, V, N>;

/// Sorted array-backed set.
template <typename T, std::size_t N> using FlatSet = etl::flat_set<T, N>;

// =============================================================================
// ETL Queue / Buffer Aliases
// =============================================================================

/// Lock-free Single-Producer / Single-Consumer queue.
/// Primary type for IVC ring buffer (fixed-size packet path).
template <typename T, std::size_t N> using SpscQueue = etl::queue_spsc_atomic<T, N>;

/// Lock-free SPSC binary inplace buffer.
/// Use for IVC when payloads are variable-length (DMA-friendly contiguous
/// slots, avoids per-element copy).
template <std::size_t N> using BipBuffer = etl::bip_buffer_spsc_atomic<N>;

/// Circular buffer that overwrites oldest data when full.
/// Use for console output log (loss of old data is acceptable).
template <typename T, std::size_t N> using CircBuf = etl::circular_buffer<T, N>;

// =============================================================================
// ETL Callable Aliases
// =============================================================================

/// Zero-overhead wrapper for a free function or member function pointer.
/// No heap allocation. Prefer over Function<> when captures are not needed.
template <typename Sig> using Delegate = etl::delegate<Sig>;

/// Callable wrapper that can store a capturing lambda.
/// N is the maximum capture-list storage size in bytes.
template <typename Sig, std::size_t N> using Function = etl::function<Sig, N>;

// =============================================================================
// Pigweed Type Aliases
// =============================================================================

#if __has_include("pw_span/span.h")
/// Memory view with bounds-checking on access. Triggers pw_assert on overflow.
/// Preferred over std::span for IVC shared memory boundaries.
template <typename T> using Span = pw::span<T>;

/// Read-only memory view.
template <typename T> using ConstSpan = pw::span<const T>;
#endif

#if __has_include("pw_result/result.h")
/// Return type carrying either a value T or a pw::Status error code.
/// Canonical return type for all HAL and component API functions.
template <typename T> using Result = pw::Result<T>;

/// Error code type. Use pw::Status::OutOfRange(), NotFound(), etc.
using Status = pw::Status;
#endif

#if __has_include("pw_string/string.h")
/// Fixed-capacity string for storing log messages or names.
template <std::size_t N> using InlineString = pw::InlineString<N>;

/// Fixed-capacity string buffer with Format() for UART output composition.
template <std::size_t N> using StringBuffer = pw::StringBuffer<N>;
#endif

} // namespace novavisor

// =============================================================================
// Intentional Omissions
// =============================================================================
//
// The following types are deliberately NOT aliased here:
//
// std::array<T,N>    — C++23 standard; use directly. etl::array is identical.
// std::optional<T>   — C++23 freestanding; prefer over etl::optional.
// std::string_view   — C++23 standard; zero-copy string reference.
// pw::Function<>     — pw ecosystem internal; no alias keeps its origin visible.
// CIB types          — consteval DSL; cannot be meaningfully aliased.
// etl::string<N>     — only for ETL container storage; not for formatting.

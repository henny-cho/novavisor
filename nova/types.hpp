#pragma once

/**
 * @file nova_types.hpp
 * @brief Global Type Definitions for NovaVisor (C++23 Bare-metal Environment)
 * * @note [CONTRIBUTOR GUIDELINES]
 * Do NOT use standard STL containers (std::vector, std::string) or dynamic memory allocation.
 * Always use the aliased types defined in the `nova::` namespace below.
 */

// --- 1. System Headers (Freestanding C++23) ---
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <mdspan>
#include <optional>
#include <span>
#include <string_view>
#include <utility>
#include <variant>

// --- 2. ETL (Embedded Template Library) Headers ---
#include <etl/bip_buffer_spsc_atomic.h>
#include <etl/bitset.h>
#include <etl/circular_buffer.h>
#include <etl/cyclic_buffer.h>
#include <etl/delegate.h>
#include <etl/deque.h>
#include <etl/flat_map.h>
#include <etl/flat_set.h>
#include <etl/function.h>
#include <etl/list.h>
#include <etl/pool.h>
#include <etl/queue.h>
#include <etl/queue_spsc_atomic.h>
#include <etl/unordered_map.h>
#include <etl/vector.h>

// --- 3. Pigweed Headers ---
#include <pw_containers/intrusive_list.h>
#include <pw_function/function.h>
#include <pw_result/result.h>
#include <pw_ring_buffer/ring_buffer.h>
#include <pw_span/span.h>
#include <pw_status/status.h>
#include <pw_string/string.h>
#include <pw_string/string_builder.h>
#include <pw_sync/interrupt_spin_lock.h>

// --- 4. CIB Headers ---
#include <cib/cib.hpp>

// ============================================================================
// HARDWARE LEVEL POISONING
// Prevent any accidental usage of heap memory in the entire project.
// ============================================================================
#pragma GCC poison malloc free new delete

namespace nova {

// ============================================================================
// [CATEGORY 1] Standard C++23 (Zero-cost Freestanding)
// Rule: If it exists in C++ freestanding standard, we MUST use it.
// ============================================================================

// Error Handling
// Reason: std::expected is the C++23 standard for exception-less error handling.
// Replaces pw::Result or etl::expected to avoid 3rd-party dependency for core logic.
template <typename T, typename E>
using Expected = std::expected<T, E>;

// Memory Views
// Reason: Zero-overhead, bounds-checked views of contiguous memory.
template <typename T, std::size_t Extent = std::dynamic_extent>
using Span = std::span<T, Extent>;

// Reason: Multi-dimensional array view (C++23). Crucial for mapping Stage 2 MMU
// page tables (which are 2D/3D structures) securely without raw pointer math.
template <typename T, typename Extents, typename LayoutPolicy = std::layout_right>
using MdSpan = std::mdspan<T, Extents, LayoutPolicy>;

// Text Views
// Reason: std::string_view does not allocate memory and is perfect for passing
// read-only string literals or sliced Pigweed strings around.
using StringView = std::string_view;

// Fixed-size Array
// Reason: std::array is freestanding and has zero overhead compared to C-arrays.
template <typename T, std::size_t N>
using Array = std::array<T, N>;

// Unions / Optional
// Reason: Type-safe unions without dynamic allocation. Essential for IPC message parsing.
template <typename... Types>
using Variant = std::variant<Types...>;

// Reason: Represents optional values gracefully without using raw null pointers.
template <typename T>
using Optional = std::optional<T>;

// Concurrency
// Reason: Hardware-level atomic operations. Foundation for building spinlocks.
template <typename T>
using Atomic = std::atomic<T>;

// ============================================================================
// [CATEGORY 2] Pigweed (System Tooling, Interfacing & Callbacks)
// Rule: Use Pigweed for external communication, strings, and dynamic traits.
// ============================================================================

// Status Codes
// Reason: Standardized Google status codes (OK, INVALID_ARGUMENT, etc.).
// Use this as the 'E' type in nova::Expected<T, nova::Status>.
using Status = pw::Status;

// Result Type
// Reason: pw::Result<T> carries either a value T or a pw::Status error code.
// Canonical return type for all HAL and component API functions.
template <typename T>
using Result = pw::Result<T>;

// Span (Pigweed)
// Reason: pw::span provides bounds-checking on access (triggers pw_assert on overflow).
// Preferred over std::span for IVC shared memory boundary checks.
template <typename T>
using PwSpan = pw::span<T>;

template <typename T>
using PwConstSpan = pw::span<const T>;

// String Manipulation
// Reason: pw::InlineString allocates space on the stack or inline within objects.
// It guarantees null-termination and protects against buffer overflows.
template <std::size_t Capacity>
using String = pw::InlineString<Capacity>;

// String Builder
// Reason: pw::StringBuffer adds Format() for composing UART output safely without heap.
template <std::size_t Capacity>
using StringBuffer = pw::StringBuffer<Capacity>;

// Intrusive Data Structures
// Reason: pw::IntrusiveList embeds pointers directly inside the objects (like Linux kernel).
// Perfect for O(1) scheduling queues where VCPU objects move between wait/ready lists.
template <typename T>
using IntrusiveList = pw::IntrusiveList<T>;

// Callbacks
// Reason: pw::Function is a fixed-size, heap-less alternative to std::function.
// Ideal for registering dynamic hardware interrupt handlers (ISRs).
template <typename Callable, std::size_t InlineBufferSize = 16>
using Function = pw::Function<Callable, InlineBufferSize>;

// Byte-stream Buffers
// Reason: pw::ring_buffer is perfect for raw byte streams, such as multiplexing
// UART console I/O between Linux and Zephyr VMs.
using ByteRingBuffer = pw::ring_buffer::RingBuffer;

// Synchronization
// Reason: Bare-metal safe spinlock that masks interrupts to prevent deadlocks
// in mixed-criticality SMP (Symmetric Multi-Processing) environments.
using InterruptSpinLock = pw::sync::InterruptSpinLock;

// ============================================================================
// [CATEGORY 3] ETL (Internal Data Structures)
// Rule: Use ETL to store and manage internal system states safely.
// ============================================================================

// Dynamic-like Arrays
// Reason: etl::vector provides std::vector API but capacity is fixed at compile time.
// Use for VM lists, allocated memory region trackers, etc.
template <typename T, std::size_t MaxSize>
using Vector = etl::vector<T, MaxSize>;

// Double-ended Queue
// Reason: etl::deque provides O(1) push/pop at both ends, compile-time capacity.
template <typename T, std::size_t MaxSize>
using Deque = etl::deque<T, MaxSize>;

// Doubly Linked List (ETL)
// Reason: etl::list with compile-time maximum node count. Useful for scheduler run-queues
// where insertion/removal must be O(1) without heap.
template <typename T, std::size_t MaxSize>
using List = etl::list<T, MaxSize>;

// Key-Value Maps
// Reason: etl::flat_map uses contiguous memory (cache-friendly) instead of nodes.
// Excellent for translating virtual interrupts (vIRQ) to physical interrupts (pIRQ).
template <typename Key, typename Value, std::size_t MaxSize>
using FlatMap = etl::flat_map<Key, Value, MaxSize>;

// Hash Map
// Reason: etl::unordered_map provides O(1) average lookup. Prefer over FlatMap
// when N > ~32 and ordered iteration is not needed.
template <typename Key, typename Value, std::size_t MaxSize>
using HashMap = etl::unordered_map<Key, Value, MaxSize>;

// Sorted Set
// Reason: etl::flat_set for unique key collections backed by contiguous memory.
template <typename T, std::size_t MaxSize>
using FlatSet = etl::flat_set<T, MaxSize>;

// Hardware Bit Manipulation
// Reason: Safely manipulate GIC (Interrupt Controller) or CPU system registers.
template <std::size_t Bits>
using BitSet = etl::bitset<Bits>;

// Object Pooling
// Reason: Pre-allocates a chunk of memory for specific objects at boot.
// Used when we need "dynamic-like" creation of VCPU or IVC channel contexts.
template <typename T, std::size_t MaxSize>
using ObjectPool = etl::pool<T, MaxSize>;

// Message Queues
// Reason: etl::queue is highly optimized for FIFO structures. Good for fixed-size
// message passing queues within the hypervisor internal logic.
template <typename T, std::size_t MaxSize>
using Queue = etl::queue<T, MaxSize>;

// Lock-free SPSC Queue
// Reason: etl::queue_spsc_atomic provides a lockless Single-Producer/Single-Consumer
// queue. Primary type for IVC ring buffer (fixed-size packet path).
template <typename T, std::size_t MaxSize>
using SpscQueue = etl::queue_spsc_atomic<T, MaxSize>;

// SPSC BipBuffer
// Reason: Lock-free SPSC binary inplace buffer for variable-length IVC payloads.
// DMA-friendly: contiguous slots avoid per-element copy overhead.
template <std::size_t MaxSize>
using BipBuffer = etl::bip_buffer_spsc_atomic<MaxSize>;

// Object Ring Buffers
// Reason: Unlike pw::ring_buffer (which is for raw bytes), etl::cyclic_buffer
// is strongly typed for objects. Good for logging structured events.
template <typename T, std::size_t MaxSize>
using CyclicBuffer = etl::cyclic_buffer<T, MaxSize>;

// Circular Buffer (overwrite on full)
// Reason: etl::circular_buffer overwrites the oldest data when full.
// Use for console output logs where loss of old data is acceptable.
template <typename T, std::size_t MaxSize>
using CircBuf = etl::circular_buffer<T, MaxSize>;

// Callable: Delegate (no capture)
// Reason: etl::delegate is a zero-overhead wrapper for free or member function pointers.
// No heap allocation. Prefer over Function<> when captures are not needed.
template <typename Sig>
using Delegate = etl::delegate<Sig>;

// Callable: ETL Function (with capture)
// Reason: etl::function can store a capturing lambda within a fixed inline buffer.
// N is the maximum capture-list storage size in bytes.
template <typename Sig, std::size_t InlineSize>
using EtlFunction = etl::function<Sig, InlineSize>;

// ============================================================================
// [CATEGORY 4] CIB (Architecture & Event Loop)
// Rule: Use CIB for compile-time design, state machines, and zero-cost routing.
// ============================================================================

// State Machines
// Reason: Validates state transitions (e.g., VM Booting -> Running -> Panic -> Reboot)
// at compile-time. If an invalid transition is written, the build fails.
template <typename... Configs>
using StateMachine = cib::state_machine<Configs...>;

// Event Router (Nexus)
// Reason: Compile-time dependency injection and event loop. Resolves which VM
// should receive a specific hardware interrupt with absolute zero runtime overhead.
template <typename... Configs>
using EventNexus = cib::nexus<Configs...>;

// ============================================================================
// [CATEGORY 5] Hardware Primitives (Future Scalability)
// Rule: Strictly type physical/virtual addresses to avoid catastrophic casting bugs.
// ============================================================================

// Memory Addresses
// Reason: Distinct types prevent accidentally passing a Virtual Address where a
// Physical Address is expected (a common hypervisor bug leading to crashes).
enum class PhysAddr : uintptr_t {};
enum class VirtAddr : uintptr_t {};

// Register Sizes
// Reason: Explicit sizing for MMIO (Memory Mapped I/O) access.
using Reg32 = volatile uint32_t;
using Reg64 = volatile uint64_t;

// Raw Memory
// Reason: std::byte enforces safe byte-level memory access without unintended
// arithmetic operations that char/uint8_t would allow.
using Byte = std::byte;

} // namespace nova

#pragma once

// Producer/consumer phase model shared by command and event queues.

#include <cstdint>

namespace nova::smmu {

inline constexpr std::uint8_t  kMaxQueueLog2Entries = 19;
inline constexpr std::uint32_t kEventQueueOverflow  = 1U << 31U;

struct QueueState {
  std::uint8_t  log2_entries = 0;
  std::uint32_t producer     = 0;
  std::uint32_t consumer     = 0;

  [[nodiscard]] constexpr auto valid() const noexcept -> bool { return log2_entries <= kMaxQueueLog2Entries; }

  [[nodiscard]] constexpr auto capacity() const noexcept -> std::uint32_t {
    return valid() ? std::uint32_t{1} << log2_entries : 0;
  }

  [[nodiscard]] constexpr auto index_mask() const noexcept -> std::uint32_t { return valid() ? capacity() - 1U : 0; }

  [[nodiscard]] constexpr auto wrap_mask() const noexcept -> std::uint32_t { return valid() ? capacity() : 0; }

  [[nodiscard]] constexpr auto pointer_mask() const noexcept -> std::uint32_t {
    return valid() ? (wrap_mask() << 1U) - 1U : 0;
  }

  [[nodiscard]] constexpr auto producer_index() const noexcept -> std::uint32_t { return producer & index_mask(); }

  [[nodiscard]] constexpr auto consumer_index() const noexcept -> std::uint32_t { return consumer & index_mask(); }

  [[nodiscard]] constexpr auto used() const noexcept -> std::uint32_t {
    return valid() ? (producer - consumer) & pointer_mask() : 0;
  }

  [[nodiscard]] constexpr auto consistent() const noexcept -> bool { return valid() && used() <= capacity(); }

  [[nodiscard]] constexpr auto empty() const noexcept -> bool { return consistent() && used() == 0U; }

  [[nodiscard]] constexpr auto full() const noexcept -> bool { return consistent() && used() == capacity(); }

  constexpr auto try_produce() noexcept -> bool {
    if (!consistent() || full()) {
      return false;
    }
    producer = advance(producer);
    return true;
  }

  constexpr auto try_consume() noexcept -> bool {
    if (!consistent() || empty()) {
      return false;
    }
    consumer = advance(consumer);
    return true;
  }

private:
  [[nodiscard]] constexpr auto advance(std::uint32_t value) const noexcept -> std::uint32_t {
    return (value & ~pointer_mask()) | ((value + 1U) & pointer_mask());
  }
};

[[nodiscard]] constexpr auto event_overflow_pending(const QueueState& queue) noexcept -> bool {
  return ((queue.producer ^ queue.consumer) & kEventQueueOverflow) != 0U;
}

constexpr void mark_event_overflow(QueueState& queue) noexcept {
  if (!event_overflow_pending(queue)) {
    queue.producer ^= kEventQueueOverflow;
  }
}

constexpr void acknowledge_event_overflow(QueueState& queue) noexcept {
  queue.consumer = (queue.consumer & ~kEventQueueOverflow) | (queue.producer & kEventQueueOverflow);
}

constexpr auto try_record_event(QueueState& queue) noexcept -> bool {
  if (!queue.consistent()) {
    return false;
  }
  if (queue.full()) {
    mark_event_overflow(queue);
    return false;
  }
  return queue.try_produce();
}

} // namespace nova::smmu

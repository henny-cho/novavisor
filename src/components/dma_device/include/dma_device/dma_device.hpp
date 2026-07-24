#pragma once

#include "core_vcpu/core_vcpu.hpp"
#include "nova/abi/dma.hpp"
#include "smmu/smmu.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova::dma_device {

enum class QuiesceResult : std::uint8_t {
  kComplete,
  kPending,
  kFailed,
};

void init() noexcept;

// Stop new requests, drain the device, then block its SMMU streams.
// VMs without an assigned board device are unaffected.
[[nodiscard]] auto begin_quiesce(std::size_t vm) noexcept -> QuiesceResult;
[[nodiscard]] auto poll_quiesce(std::size_t vm) noexcept -> QuiesceResult;

// Install the new Stage-2 context before allowing device requests.
[[nodiscard]] auto resume_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool;
[[nodiscard]] auto can_start(std::size_t vm) noexcept -> bool;
[[nodiscard]] auto is_active(std::size_t vm, std::uint64_t generation) noexcept -> bool;
[[nodiscard]] auto start_dma(dma::DeviceId device_id, std::size_t vm, std::uint64_t generation, std::uint64_t source,
                             std::uint64_t destination, std::uint64_t count, bool to_ram) noexcept -> bool;

} // namespace nova::dma_device

namespace nova {

struct dma_device_component {
  constexpr static auto INIT = flow::action<"dma_device_init">([]() noexcept { dma_device::init(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(core_vcpu_component::INIT >> *INIT));
};

} // namespace nova

#pragma once

// components/trap_handler/include/trap_handler/dma_fault.hpp
//
// Isolated DMA fault notification. The SMMU quarantines the stream and
// publishes; VM lifecycle decides recovery.
//
// The declaration lives with trap_handler's other service types for the
// same reason they do: a subscriber must compile without the publisher
// in the composition. A profile with VM power but no device isolation
// still names this handler, and cib rejects a subscriber whose service
// nobody exports — so the export sits in the component every profile
// composes rather than in the device stack.

#include "nova/abi/dma.hpp"

#include <nexus/callback.hpp>

namespace nova {

struct DmaFaultCall {
  dma::FaultNotice notice{};
  bool             handled = false;
};

struct DmaFaultService : public callback::service<DmaFaultCall*> {};

} // namespace nova

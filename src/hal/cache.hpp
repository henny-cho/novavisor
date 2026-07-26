#pragma once

// hal/cache.hpp
//
// Cache-maintenance facade; the arch tree is selected at build time.
// Guest handoff cleans to the Point of Coherency, because a guest
// enters with its MMU and caches off.

#include "hal/arch/aarch64/cache.hpp"

namespace nova {

namespace cache = arch::cache;

} // namespace nova

// Host-side GTest suite for MMIO read widening. The syndrome decode
// itself is pinned by static_asserts in the header, where every build
// checks it; what is left here is the case analysis over access size,
// sign extension and register width.

#include "nova/arch/data_abort.hpp"

#include <gtest/gtest.h>

using namespace nova::esr;

TEST(ExtendMmioRead, TruncatesToAccessSize) {
  EXPECT_EQ(extend_mmio_read(0xAABB'CCDDULL, 1, false, false), 0xDDULL);
  EXPECT_EQ(extend_mmio_read(0xAABB'CCDDULL, 2, false, false), 0xCCDDULL);
}

TEST(ExtendMmioRead, SignExtendsToRegisterWidth) {
  // ldrsb w0: byte 0x80 → 0xFFFFFF80 (W register clamps to 32 bits)
  EXPECT_EQ(extend_mmio_read(0x80ULL, 1, true, false), 0xFFFF'FF80ULL);
  // ldrsh x0: halfword 0x8000 → sign-extended through 64 bits
  EXPECT_EQ(extend_mmio_read(0x8000ULL, 2, true, true), 0xFFFF'FFFF'FFFF'8000ULL);
}

TEST(ExtendMmioRead, PositiveValueUnchangedBySse) {
  EXPECT_EQ(extend_mmio_read(0x7FULL, 1, true, true), 0x7FULL);
}

TEST(ExtendMmioRead, FullWidth64Bit) {
  EXPECT_EQ(extend_mmio_read(0x1122'3344'5566'7788ULL, 8, false, true), 0x1122'3344'5566'7788ULL);
}

TEST(ExtendMmioRead, WRegisterClampsWideValue) {
  EXPECT_EQ(extend_mmio_read(0x1122'3344'5566'7788ULL, 8, false, false), 0x5566'7788ULL);
}

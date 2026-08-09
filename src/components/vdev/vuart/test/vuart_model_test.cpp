// Host-side GTest suite for the pure PL011 register model: RX FIFO,
// flag and interrupt views, register decode.

#include "vuart/vuart_model.hpp"

#include <gtest/gtest.h>

using namespace nova::vuart;

TEST(VuartFifo, PushPopFifoOrder) {
  UartState u{};
  EXPECT_TRUE(rx_empty(u));
  EXPECT_TRUE(rx_push(u, 'a'));
  EXPECT_TRUE(rx_push(u, 'b'));
  EXPECT_EQ(rx_pop(u), 'a');
  EXPECT_EQ(rx_pop(u), 'b');
  EXPECT_TRUE(rx_empty(u));
}

TEST(VuartFifo, FullFifoDropsAndWrapsAround) {
  UartState u{};
  for (std::size_t i = 0; i < kFifoDepth; ++i) {
    EXPECT_TRUE(rx_push(u, static_cast<std::uint8_t>(i)));
  }
  EXPECT_FALSE(rx_push(u, 0xFF)); // overrun: dropped

  EXPECT_EQ(rx_pop(u), 0U);
  EXPECT_TRUE(rx_push(u, 0x42)); // wraps past the array end
  for (std::size_t i = 1; i < kFifoDepth; ++i) {
    EXPECT_EQ(rx_pop(u), i);
  }
  EXPECT_EQ(rx_pop(u), 0x42U);
}

TEST(VuartIrq, MisIsRisGatedByImsc) {
  UartState u{};
  EXPECT_EQ(ris(u), 0U);
  EXPECT_TRUE(rx_push(u, 'x'));
  EXPECT_EQ(ris(u), kIntRx); // level follows occupancy
  EXPECT_EQ(mis(u), 0U);     // masked at reset

  u.imsc = kIntRx;
  EXPECT_EQ(mis(u), kIntRx);
  (void)rx_pop(u);
  EXPECT_EQ(mis(u), 0U); // draining DR clears the level
}

TEST(VuartReg, DrReadPopsAndFrTracksFifo) {
  UartState u{};
  EXPECT_EQ(reg_read(u, kUartFr).value, kFrTxfe | kFrRxfe); // TX always empty

  EXPECT_TRUE(rx_push(u, 'z'));
  EXPECT_EQ(reg_read(u, kUartFr).value, kFrTxfe);
  EXPECT_EQ(reg_read(u, kUartDr).value, 'z');
  EXPECT_EQ(reg_read(u, kUartFr).value, kFrTxfe | kFrRxfe);
}

TEST(VuartReg, DrWriteReportsTxByte) {
  UartState  u{};
  const auto e = reg_write(u, kUartDr, 'A');
  EXPECT_TRUE(e.known);
  EXPECT_TRUE(e.tx);
  EXPECT_EQ(e.tx_byte, 'A');
}

TEST(VuartReg, ImscRoundTripsMasked) {
  UartState u{};
  EXPECT_TRUE(reg_write(u, kUartImsc, ~0ULL).known);
  EXPECT_EQ(reg_read(u, kUartImsc).value, kImscMask);
}

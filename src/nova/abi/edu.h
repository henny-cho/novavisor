#ifndef NOVA_ABI_EDU_H
#define NOVA_ABI_EDU_H

// Register contract of the edu device, shared by C guests and the C++
// board backend. Guest-side accessors live with the other EL1 device
// helpers (demo/common/include/edu_el1.h).
// NOVA_EDU_BAR0_IPA (the guest-visible window) is in guest_layout.h.
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)
#define NOVA_EDU_IDENTITY_REG 0x00U
#define NOVA_EDU_IRQ_STATUS   0x24U
#define NOVA_EDU_IRQ_RAISE    0x60U
#define NOVA_EDU_IRQ_ACK      0x64U
#define NOVA_EDU_DMA_SOURCE   0x80U
#define NOVA_EDU_DMA_DEST     0x88U
#define NOVA_EDU_DMA_COUNT    0x90U
#define NOVA_EDU_DMA_COMMAND  0x98U

#define NOVA_EDU_IDENTITY        0x010000EDU
#define NOVA_EDU_DMA_RUN         (1U << 0U)
#define NOVA_EDU_DMA_TO_PCI      (1U << 1U)
#define NOVA_EDU_DMA_IRQ         (1U << 2U)
#define NOVA_EDU_DMA_IRQ_STATUS  (1U << 8U)
#define NOVA_EDU_INTERNAL_BUFFER 0x00040000ULL
#define NOVA_EDU_BUFFER_SIZE     4096U
// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_ABI_EDU_H */

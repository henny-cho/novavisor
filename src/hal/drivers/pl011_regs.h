/* hal/drivers/pl011_regs.h
 *
 * PL011 register offsets and the bits this hypervisor touches. Plain
 * #defines only: boot.S pokes the same UART for its pre-console
 * breadcrumbs, so the layout has to survive the assembler as well as the
 * C++ driver — and it must be one definition, or the breadcrumb path and
 * the console could disagree about which register they are writing.
 */

#ifndef NOVA_PL011_REGS_H
#define NOVA_PL011_REGS_H

// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#define NOVA_PL011_DR   0x00 /* data */
#define NOVA_PL011_FR   0x18 /* flags */
#define NOVA_PL011_CR   0x30 /* control */
#define NOVA_PL011_IMSC 0x38 /* interrupt mask set/clear */

#define NOVA_PL011_FR_RXFE     0x10 /* receive FIFO empty */
#define NOVA_PL011_FR_TXFF     0x20 /* transmit FIFO full */
#define NOVA_PL011_FR_TXFF_BIT 5

#define NOVA_PL011_CR_UARTEN 0x001 /* UART enable */
#define NOVA_PL011_CR_TXE    0x100 /* transmit enable */
#define NOVA_PL011_CR_RXE    0x200 /* receive enable */

#define NOVA_PL011_IMSC_RX 0x10 /* receive interrupt */

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_PL011_REGS_H */

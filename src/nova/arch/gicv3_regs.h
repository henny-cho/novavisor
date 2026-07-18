/* nova/arch/gicv3_regs.h
 *
 * GICv3 memory-mapped register layout — offsets and bit fields fixed by
 * the GIC architecture. The single source shared by everything that
 * programs or emulates a GICv3 frame:
 *   - the board GIC driver        (EL2, physical GIC bring-up)
 *   - components/vgic/vgic_model  (EL2, register emulation)
 *   - demo guest helpers          (EL1, guest programming the vGIC)
 *
 * Only architecture facts belong here; frame base addresses are board /
 * guest-platform decisions and advertised identification values are
 * emulation policy.
 *
 * Plain #defines only: this header must survive the assembler and the
 * C/C++ compilers alike.
 */

#ifndef NOVA_GICV3_REGS_H
#define NOVA_GICV3_REGS_H

/* Macros are the point here (C and assembler consumers) — the usual
 * constexpr guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

/* Frame sizes. */
#define NOVA_GICD_FRAME_SIZE 0x10000 /* 64 KiB distributor */
#define NOVA_GICR_FRAME_SIZE 0x20000 /* one redistributor: RD + SGI frame */

/* Distributor (GICD_BASE + offset). */
#define NOVA_GICD_CTLR  0x0000
#define NOVA_GICD_TYPER 0x0004
#define NOVA_GICD_IIDR  0x0008
#define NOVA_GICD_PIDR2 0xFFE8

/* GICD_CTLR bits (affinity-routing view). */
#define NOVA_GICD_CTLR_ENABLE_GRP1 (1U << 1)
#define NOVA_GICD_CTLR_ARE         (1U << 4)

/* Redistributor RD_base frame (GICR_BASE + offset). */
#define NOVA_GICR_CTLR     0x0000
#define NOVA_GICR_IIDR     0x0004
#define NOVA_GICR_TYPER    0x0008 /* 64-bit */
#define NOVA_GICR_TYPER_HI 0x000C
#define NOVA_GICR_WAKER    0x0014
#define NOVA_GICR_PIDR2    0xFFE8

/* GICR_TYPER bits (low word). */
#define NOVA_GICR_TYPER_LAST (1U << 4) /* last redistributor frame */

/* GICR_WAKER bits. */
#define NOVA_GICR_WAKER_PROCESSOR_SLEEP (1U << 1)
#define NOVA_GICR_WAKER_CHILDREN_ASLEEP (1U << 2)

/* Redistributor SGI_base frame (RD_base + 64 KiB + offset). */
#define NOVA_GICR_SGI_FRAME  0x10000
#define NOVA_GICR_IGROUPR0   (NOVA_GICR_SGI_FRAME + 0x0080)
#define NOVA_GICR_ISENABLER0 (NOVA_GICR_SGI_FRAME + 0x0100)
#define NOVA_GICR_ICENABLER0 (NOVA_GICR_SGI_FRAME + 0x0180)
#define NOVA_GICR_ISPENDR0   (NOVA_GICR_SGI_FRAME + 0x0200)
#define NOVA_GICR_ICPENDR0   (NOVA_GICR_SGI_FRAME + 0x0280)
#define NOVA_GICR_IPRIORITYR (NOVA_GICR_SGI_FRAME + 0x0400)
#define NOVA_GICR_ICFGR0     (NOVA_GICR_SGI_FRAME + 0x0C00)
#define NOVA_GICR_ICFGR1     (NOVA_GICR_SGI_FRAME + 0x0C04)

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_GICV3_REGS_H */

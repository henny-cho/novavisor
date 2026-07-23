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
#define NOVA_GICD_CTLR   0x0000
#define NOVA_GICD_TYPER  0x0004
#define NOVA_GICD_IIDR   0x0008
#define NOVA_GICD_TYPER2 0x000C
#define NOVA_GICD_PIDR2  0xFFE8

/* Distributor banks. Word 0 covers private INTIDs and is the
 * redistributor's job under affinity routing. */
#define NOVA_GICD_IGROUPR    0x0080
#define NOVA_GICD_ISENABLER  0x0100
#define NOVA_GICD_ICENABLER  0x0180
#define NOVA_GICD_ISPENDR    0x0200
#define NOVA_GICD_ICPENDR    0x0280
#define NOVA_GICD_ISACTIVER  0x0300
#define NOVA_GICD_ICACTIVER  0x0380
#define NOVA_GICD_IGRPMODR   0x0D00
#define NOVA_GICD_IGROUPR1   (NOVA_GICD_IGROUPR + 4)
#define NOVA_GICD_ISENABLER1 (NOVA_GICD_ISENABLER + 4)
#define NOVA_GICD_ICENABLER1 (NOVA_GICD_ICENABLER + 4)
#define NOVA_GICD_ISPENDR1   (NOVA_GICD_ISPENDR + 4)
#define NOVA_GICD_ICPENDR1   (NOVA_GICD_ICPENDR + 4)
#define NOVA_GICD_ISACTIVER1 (NOVA_GICD_ISACTIVER + 4)
#define NOVA_GICD_ICACTIVER1 (NOVA_GICD_ICACTIVER + 4)
#define NOVA_GICD_IPRIORITYR 0x0400 /* byte-indexed by INTID */
#define NOVA_GICD_ICFGR      0x0C00
#define NOVA_GICD_ICFGR2     (NOVA_GICD_ICFGR + 8)
#define NOVA_GICD_ICFGR3     (NOVA_GICD_ICFGR + 12)
#define NOVA_GICD_IGRPMODR1  (NOVA_GICD_IGRPMODR + 4)
#define NOVA_GICD_IROUTER    0x6000 /* 64-bit, +8 per INTID */

/* GICD_CTLR bits (affinity-routing view). */
#define NOVA_GICD_CTLR_ENABLE_GRP1 (1U << 1)
#define NOVA_GICD_CTLR_ARE         (1U << 4)
#define NOVA_GICD_CTLR_DS          (1U << 6)
#define NOVA_GICD_CTLR_RWP         (1U << 31)

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
#define NOVA_GICR_ISACTIVER0 (NOVA_GICR_SGI_FRAME + 0x0300)
#define NOVA_GICR_ICACTIVER0 (NOVA_GICR_SGI_FRAME + 0x0380)
#define NOVA_GICR_IPRIORITYR (NOVA_GICR_SGI_FRAME + 0x0400)
#define NOVA_GICR_ICFGR0     (NOVA_GICR_SGI_FRAME + 0x0C00)
#define NOVA_GICR_ICFGR1     (NOVA_GICR_SGI_FRAME + 0x0C04)
#define NOVA_GICR_IGRPMODR0  (NOVA_GICR_SGI_FRAME + 0x0D00)

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_GICV3_REGS_H */

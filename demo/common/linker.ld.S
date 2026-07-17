/* NovaVisor demo EL1 guest linker script TEMPLATE.
 *
 * Preprocessed by demo/CMakeLists.txt (cc -E -P -x c) into the linker.ld
 * actually passed to ld, so the guest window comes from nova/guest_layout.h
 * — the same header the hypervisor's Stage 2 map is built from.
 *
 * A demo that needs a different base can copy and override this file
 * in its own directory.
 */

#include "nova/guest_layout.h"

ENTRY(_start)

MEMORY {
    RAM (rwx) : ORIGIN = NOVA_GUEST_IPA_BASE, LENGTH = NOVA_GUEST_IPA_SIZE
}

SECTIONS {
    . = ORIGIN(RAM);

    .text : ALIGN(4K) {
        KEEP(*(.text.start))
        *(.text*)
    } > RAM

    .rodata : ALIGN(4K) {
        *(.rodata*)
    } > RAM

    .data : ALIGN(4K) {
        *(.data*)
    } > RAM

    __bss_start = .;
    .bss : ALIGN(16) {
        *(.bss*)
        *(COMMON)
    } > RAM
    __bss_end = .;

    /* Stack grows down from the top of the allocated region.
     * A demo can override __stack_top via --defsym if it needs a
     * different location. */
    __stack_top = ORIGIN(RAM) + LENGTH(RAM);

    /DISCARD/ : {
        *(.note*)
        *(.comment*)
        *(.eh_frame*)
    }
}

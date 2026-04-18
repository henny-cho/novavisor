/* NovaVisor demo EL1 guest linker script.
 *
 * Default layout: load at IPA 0x50000000, 1 MiB window.
 * A demo that needs a different base can copy and override this file
 * in its own directory.
 */

ENTRY(_start)

MEMORY {
    RAM (rwx) : ORIGIN = 0x50000000, LENGTH = 1M
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

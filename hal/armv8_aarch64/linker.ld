/*
 * bare-metal linker script for QEMU virt board
 * AArch64 Cortex-A57
 */
ENTRY(_start)

MEMORY
{
    /* QEMU virt board memory usually starts at 0x40000000 */
    RAM (rwx) : ORIGIN = 0x40000000, LENGTH = 128M
}

SECTIONS
{
    .text : {
         KEEP(*(.text.boot))   /* boot.S: _start entry point, must be first */
         . = ALIGN(2048);      /* VBAR_EL2 requires 2^11-byte alignment */
         KEEP(*(.text.vec))    /* vec.S: EL2 exception vector table */
         *(.text*)
    } > RAM

    .rodata : {
        . = ALIGN(8);
        *(.rodata*)
    } > RAM

    .data : {
        . = ALIGN(8);
        *(.data*)
    } > RAM

    .bss (NOLOAD) : {
        . = ALIGN(8);
        bss_start = .;
        *(.bss*)
        *(COMMON)
        bss_end = .;
    } > RAM

    /* Allocate 16KB for stack */
    . = ALIGN(16);
    . = . + 0x4000;
    __stack_top = .;
}

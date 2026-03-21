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
         KEEP(*(.text.boot)) /* Ensure boot.S is placed first */
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

# Oracle fixture for the ELF structure analyser.
#
# Built by the cross presets so the analyser is checked against a real
# linked image produced by the same toolchain as the hypervisor, not
# against a synthesised one. The host test lane has no cross toolchain and
# skips the oracle case when this artifact is absent — the same
# skip-if-missing shape tests/support/image.py already uses for the
# hypervisor ELF.
#
# Deliberately independent of nova_platform: the fixture's whole value is
# that its call graph is fixed by hand, so it must not inherit board
# headers, components, or link options that could change it.

add_executable(callgraph_fixture callgraph.S)

set_target_properties(callgraph_fixture PROPERTIES
    OUTPUT_NAME callgraph
    SUFFIX .elf
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/tests")

# No libc, no start files, no build-id: the symbol table should hold the
# fixture's own routines and nothing else. `fx_entry` becomes e_entry, which
# is the analyser's first root.
target_link_options(callgraph_fixture PRIVATE
    -nostdlib -nostartfiles -static
    -Wl,-e,fx_entry
    -Wl,--build-id=none)

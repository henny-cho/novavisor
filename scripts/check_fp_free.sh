#!/usr/bin/env bash
# Verify an EL2 image contains no FP/SIMD instructions outside the
# explicit allow-list. EL2 runs with CPTR_EL2.TFP set (guest FP state is
# switched lazily), so any stray FP/SIMD use would self-trap — or, with
# the trap momentarily clear during a bank swap, corrupt guest state.
# -mgeneral-regs-only keeps compiler-generated code clean; this catches
# what the flag cannot see (prebuilt libc routines, hand-written asm).
#
# Usage: check_fp_free.sh <objdump> <elf>
set -euo pipefail

OBJDUMP="$1"
ELF="$2"

# Only the FP bank save/restore may touch FP/SIMD registers.
ALLOW='^(nova_fp_save|nova_fp_restore)$'

violations=$("$OBJDUMP" -d "$ELF" | awk -v allow="$ALLOW" '
  /^[0-9a-f]+ </ { fn = substr($2, 2, length($2) - 3) }
  /\t(f(mov|add|sub|mul|div|neg|abs|sqrt|cvt[a-z]*|cmp[e]?|ccmp[e]?|csel|madd|msub|nmadd|nmsub|min[a-z]*|max[a-z]*|rint[a-z]*)|scvtf|ucvtf|movi|mvni|dup|ins|umov|smov|ld[1-4]r?|st[1-4]|tbl|tbx)\t/ ||
  /\t(ldr|str|ldp|stp|ldur|stur)\t[qdshb][0-9]/ {
    if (fn !~ allow) print fn ": " $0
  }
')

if [[ -n "$violations" ]]; then
  echo "error: FP/SIMD instructions in EL2 image outside the allow-list:" >&2
  echo "$violations" | sort -u >&2
  exit 1
fi

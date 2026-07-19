// Guest-side minimal FDT reader.
//
// The hypervisor hands the boot vCPU its configuration blob's IPA in
// x0 (see common/startup.S). This walker extracts what a guest needs
// to know about itself from the yml2dtb schema: /memory reg size and
// the /cpus child count. FDT is big-endian; aarch64 EL1 here runs
// little-endian with the MMU off, so reads are 4-byte aligned u32
// loads (the structure block is 4-aligned throughout) swapped with
// the bswap intrinsic — a single rev instruction.

#ifndef NOVAVISOR_DEMO_FDT_EL1_H
#define NOVAVISOR_DEMO_FDT_EL1_H

#include <stdint.h>

#define FDT_MAGIC      0xD00DFEEDU
#define FDT_BEGIN_NODE 1U
#define FDT_END_NODE   2U
#define FDT_PROP       3U
#define FDT_NOP        4U
#define FDT_END        9U

struct fdt_guest_info {
  int      ok;
  uint64_t mem_size; /* /memory reg, second u64 cell pair */
  uint32_t cpus;     /* number of /cpus/cpu@N children */
};

static inline uint32_t fdt_be32(const uint8_t* p) {
  return __builtin_bswap32(*(const uint32_t*)p);
}

/* Node-name match up to an optional unit-address ("memory" matches
 * "memory@50000000" but not "memo..."). */
static inline int fdt_name_is(const char* name, const char* want) {
  unsigned i = 0;
  for (; want[i] != '\0'; ++i) {
    if (name[i] != want[i]) {
      return 0;
    }
  }
  return name[i] == '\0' || name[i] == '@';
}

static inline int fdt_streq(const char* a, const char* b) {
  unsigned i = 0;
  for (; a[i] != '\0' && a[i] == b[i]; ++i) {
  }
  return a[i] == b[i];
}

static inline struct fdt_guest_info fdt_parse_guest(unsigned long dtb) {
  struct fdt_guest_info info = {0, 0, 0};
  const uint8_t*        b    = (const uint8_t*)dtb;

  if (dtb == 0 || fdt_be32(b + 0) != FDT_MAGIC) {
    return info;
  }
  const uint8_t* s           = b + fdt_be32(b + 8); /* struct block */
  const char*    strs        = (const char*)b + fdt_be32(b + 12);
  uint32_t       size_struct = fdt_be32(b + 36);

  uint32_t pos   = 0;
  int      depth = 0;
  int      node  = 0; /* current depth-1 node: 1 = memory, 2 = cpus */
  while (pos + 4 <= size_struct) {
    uint32_t tok = fdt_be32(s + pos);
    pos += 4;
    if (tok == FDT_BEGIN_NODE) {
      const char* name = (const char*)(s + pos);
      uint32_t    len  = 0;
      while (name[len] != '\0') {
        ++len;
      }
      pos += (len + 1U + 3U) & ~3U;
      ++depth;
      if (depth == 2) {
        node = fdt_name_is(name, "memory") ? 1 : fdt_name_is(name, "cpus") ? 2 : 0;
      } else if (depth == 3 && node == 2 && fdt_name_is(name, "cpu")) {
        ++info.cpus;
      }
    } else if (tok == FDT_END_NODE) {
      --depth;
      if (depth < 2) {
        node = 0;
      }
    } else if (tok == FDT_PROP) {
      uint32_t       len     = fdt_be32(s + pos);
      uint32_t       nameoff = fdt_be32(s + pos + 4);
      const uint8_t* data    = s + pos + 8;
      if (depth == 2 && node == 1 && len >= 16 && fdt_streq(strs + nameoff, "reg")) {
        info.mem_size = ((uint64_t)fdt_be32(data + 8) << 32) | fdt_be32(data + 12);
      }
      pos += 8U + ((len + 3U) & ~3U);
    } else if (tok != FDT_NOP) {
      break; /* FDT_END or malformed */
    }
  }
  info.ok = info.cpus > 0 && info.mem_size > 0;
  return info;
}

#endif /* NOVAVISOR_DEMO_FDT_EL1_H */

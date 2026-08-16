/* Bitfield decomposition popover inspector for 32/64-bit hardware registers. */

import { clear, el } from "../format.mjs";

/* Common ARM64 ESR / System Register bitfield templates. */
export const BITFIELD_TEMPLATES = {
  esr: {
    name: "ESR_EL2 (Exception Syndrome)",
    fields: [
      { name: "EC (Exception Class)", shift: 26, width: 6 },
      { name: "IL (Instruction Length)", shift: 25, width: 1 },
      { name: "ISS (Instruction Specific)", shift: 0, width: 25 },
    ],
  },
  sctlr: {
    name: "SCTLR_EL2 (System Control)",
    fields: [
      { name: "WXN", shift: 19, width: 1, desc: "Write permission implies XN" },
      { name: "I", shift: 12, width: 1, desc: "Instruction access Cacheability" },
      { name: "SA", shift: 3, width: 1, desc: "SP Alignment check" },
      { name: "C", shift: 2, width: 1, desc: "Data access Cacheability" },
      { name: "A", shift: 1, width: 1, desc: "Alignment check enable" },
      { name: "M", shift: 0, width: 1, desc: "MMU enable" },
    ],
  },
  pending_mask: {
    name: "vCPU Pending Bitmask",
    fields: [
      { name: "vCPU 3", shift: 3, width: 1 },
      { name: "vCPU 2", shift: 2, width: 1 },
      { name: "vCPU 1", shift: 1, width: 1 },
      { name: "vCPU 0", shift: 0, width: 1 },
    ],
  },
};

export class BitfieldPopover {
  constructor() {
    this.overlay = null;
  }

  ensureDOM() {
    if (this.overlay || typeof document === "undefined") return;
    this.overlay = el("div", "bitfield-popover");
    this.overlay.hidden = true;
    document.body.append(this.overlay);

    document.addEventListener("click", (e) => {
      if (this.overlay && !this.overlay.hidden && !this.overlay.contains(e.target) && !e.target.closest(".bitfield-trigger")) {
        this.hide();
      }
    });
  }

  show(targetNode, templateKey, rawValue) {
    const template = BITFIELD_TEMPLATES[templateKey];
    if (!template) return;

    this.ensureDOM();
    if (!this.overlay) return;

    const val = typeof rawValue === "string" ? BigInt(rawValue) : BigInt(rawValue ?? 0);
    clear(this.overlay);

    const head = el("div", "bp-head");
    head.append(el("strong", "", template.name));
    head.append(el("span", "bp-val", `0x${val.toString(16).toUpperCase()}`));
    this.overlay.append(head);

    const list = el("div", "bp-fields");
    for (const f of template.fields) {
      const mask = (1n << BigInt(f.width)) - 1n;
      const extracted = (val >> BigInt(f.shift)) & mask;
      const row = el("div", "bp-row");
      row.append(el("span", "bp-fn", f.name));
      row.append(el("span", "bp-fv", `0x${extracted.toString(16)} (${extracted.toString()})`));
      if (f.desc) row.append(el("span", "bp-fd", f.desc));
      list.append(row);
    }
    this.overlay.append(list);

    if (targetNode && typeof targetNode.getBoundingClientRect === "function") {
      const rect = targetNode.getBoundingClientRect();
      this.overlay.style.top = `${rect.bottom + (window.scrollY || 0) + 6}px`;
      this.overlay.style.left = `${Math.max(10, rect.left + (window.scrollX || 0) - 40)}px`;
    }
    this.overlay.hidden = false;
  }

  hide() {
    if (this.overlay) this.overlay.hidden = true;
  }
}

export const globalBitfieldPopover = new BitfieldPopover();

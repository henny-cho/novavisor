/* Shared UI component primitives: Evidence badges, chips, and status tags. */

import { el } from "../format.mjs";

/* Evidence grade symbols and descriptions (C, S, M, H, —). */
export const EVIDENCE_GRADES = {
  console: { code: "C", name: "콘솔", desc: "콘솔 이벤트 — 시각 정확" },
  poll: { code: "S", name: "폴링", desc: "스냅샷 폴링 — 틱에 양자화된 관측" },
  direct: { code: "M", name: "실측", desc: "이 경로에서 머신을 세울 수 있다 — 표본이 아니라 사건 그 자체" },
  halt: { code: "H", name: "정지", desc: "일시정지 시점의 확정값" },
  none: { code: "—", name: "미관측", desc: "증거원 없음 — 구조만 표시" },
};

/* Evidence badge component. */
export function evidenceBadge(grade, label = "") {
  const info = EVIDENCE_GRADES[grade] || { code: grade, name: label || grade, desc: "" };
  const badge = el("span", `src ${grade}`, label || info.name || info.code);
  if (info.desc) badge.title = info.desc;
  return badge;
}

/* Reusable Chip button. */
export function chipButton({ label, accent = "", pressed = false, onClick, title = "", className = "" }) {
  const chip = el("button", `fchip ${className}`.trim(), label);
  chip.type = "button";
  if (accent) chip.style.setProperty("--chipc", accent);
  chip.setAttribute("aria-pressed", String(pressed));
  if (title) chip.title = title;
  if (onClick) chip.addEventListener("click", onClick);
  return chip;
}

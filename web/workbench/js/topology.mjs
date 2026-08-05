/* Target picker and guest topology summary. Both are driven by the topo
   snapshot: the catalog fills the picker, the guest list fills the rail.
   This module is the only sender of target uplinks. */

import { send } from "./net.mjs";
import { clear, el, vmSlot } from "./format.mjs";

const TARGET_KEY = "nv-wb-target";
const VM_SLOTS = 4; /* accent classes v0..v3 cycle */

function stored() {
  try {
    return localStorage.getItem(TARGET_KEY) || "";
  } catch (error) {
    return "";
  }
}

function remember(demo) {
  try {
    localStorage.setItem(TARGET_KEY, demo);
  } catch (error) {
    /* private mode: the picker still works for this session */
  }
}

export function createTopology({ select, runButton, rerunButton, pane, onStart, onNotice }) {
  let catalogKey = null;
  let lastTarget = stored();

  function fillPicker(catalog) {
    const list = Array.isArray(catalog) ? catalog : [];
    const key = list.map((item) => `${item && item.id}:${item && item.name}`).join("|");
    if (key === catalogKey) return;
    catalogKey = key;
    const keep = select.value || lastTarget;
    clear(select);
    for (const item of list) {
      const name = String((item && item.name) || "");
      if (!name) continue;
      const id = (item && item.id) || "-";
      const option = el("option", "", `${id} · ${name}`);
      option.value = name;
      select.append(option);
    }
    if (keep) select.value = keep;
    if (!select.value && select.options.length) select.selectedIndex = 0;
  }

  function describe(topo, catalog) {
    clear(pane);
    const demo = topo.demo ? String(topo.demo) : "";
    if (!demo) {
      const count = Array.isArray(catalog) ? catalog.length : 0;
      pane.append(
        el("div", "empty", `실행 중인 타깃이 없습니다. 데모 ${count}개 중 하나를 선택해 실행하세요.`),
      );
      return;
    }
    const entry = (Array.isArray(catalog) ? catalog : []).find(
      (item) => item && String(item.name) === demo,
    );
    const variant = topo.variant ? ` · variant ${topo.variant}` : "";
    pane.append(el("div", "demo-id", `ID ${(entry && entry.id) || "-"}${variant}`));
    pane.append(el("div", "demo-name", demo));
    if (topo.description) pane.append(el("div", "demo-desc", topo.description));

    const guests = Array.isArray(topo.guests) ? topo.guests : [];
    pane.append(el("div", "rail-sec", `게스트 ${guests.length}`));
    if (!guests.length) {
      pane.append(el("div", "empty", "게스트 정의 없음"));
      return;
    }
    const glist = el("div", "glist");
    guests.forEach((guest, index) => {
      const id = vmSlot(guest, index);
      const slot = `vm${id}`;
      const item = el("div", `gitem v${id % VM_SLOTS}`);
      item.append(el("span", "gi", slot));
      const name = String((guest && guest.name) || "");
      /* Some manifests name a guest after its slot; repeating it adds nothing. */
      if (name && name !== slot) {
        const label = el("span", "gn", name);
        label.title = name;
        item.append(label);
      }
      const vcpus = Number(guest && guest.vcpus);
      item.append(el("span", "gv", Number.isFinite(vcpus) ? `vCPU ${vcpus}` : "vCPU ?"));
      glist.append(item);
    });
    pane.append(glist);
  }

  function render(topo) {
    const data = topo && typeof topo === "object" ? topo : {};
    fillPicker(data.catalog);
    describe(data, data.catalog);
    if (data.demo) lastTarget = String(data.demo);
  }

  function start(demo) {
    const target = demo ? String(demo) : "";
    if (!target) {
      onNotice?.("실행할 타깃이 없습니다");
      return;
    }
    if (!send("target", { demo: target, variant: null })) {
      onNotice?.("브리지에 연결되지 않아 실행 요청을 보내지 못했습니다");
      return;
    }
    lastTarget = target;
    remember(target);
    onStart?.(target);
  }

  runButton.addEventListener("click", () => start(select.value));
  rerunButton.addEventListener("click", () => start(lastTarget || select.value));

  return { render };
}

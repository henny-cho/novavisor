/* Launch control, target picker and guest topology summary. The picker
   and the rail are driven by the topo snapshot: the catalog fills one,
   the guest list the other. This module is the only sender of target and
   stop uplinks, and the only owner of the button that carries both. */

import { clear, el, vmAccent, vmSlot } from "./format.mjs";

const TARGET_KEY = "nv-wb-target";

/* Phases in which a machine exists to be stopped. `building` counts: the
   select holds the session lock, so the stop lands once it launches. */
const ACTIVE = new Set(["building", "running", "verifying"]);

/* The whole launch choice, not only the demo: which variant to build and
   whether to verify are as much a part of what to run as its name. An
   older stored value (a bare demo name) does not parse and reads as none. */
function stored() {
  try {
    const held = JSON.parse(localStorage.getItem(TARGET_KEY));
    return held && typeof held === "object" ? held : {};
  } catch {
    return {};
  }
}

function remember(choice) {
  try {
    localStorage.setItem(TARGET_KEY, JSON.stringify(choice));
  } catch {
    /* private mode: the picker still works for this session */
  }
}

export function createTopology({
  select,
  variantSelect,
  verifyBox,
  runButton,
  pane,
  send,
  stops,
  onStart,
  onNotice,
}) {
  let catalogKey = null;
  /* One button, two meanings. Its text is the machine's presence and its
     click is whichever of launch and stop that presence leaves open;
     re-arming waits for the next phase, so a click storm is one request.
     A replay has no machine and refuses both. */
  let active = false;
  let replaying = false;
  const held = stored();
  let lastTarget = held.demo || "";
  const variants = new Map();
  verifyBox.checked = Boolean(held.verify);

  const variantsOf = (item) =>
    (Array.isArray(item && item.variants) ? item.variants : []).map(String);

  /* A demo's own variants, or nothing to pick between: the catalogue
     names only real ones, so a plain manifest hides the picker. */
  function fillVariants(demo) {
    const names = variants.get(String(demo || "")) || [];
    const keep = variantSelect.value || (demo === held.demo ? held.variant : "");
    clear(variantSelect);
    for (const name of names) {
      const option = el("option", "", name);
      option.value = name;
      variantSelect.append(option);
    }
    /* Said rather than left to the browser's default selection: this is
       the value `start` sends, so it has to be the one on screen. */
    variantSelect.value = names.includes(keep) ? keep : names[0] || "";
    variantSelect.hidden = !names.length;
  }

  function fillPicker(catalog) {
    const list = Array.isArray(catalog) ? catalog : [];
    const key = list
      .map((item) => `${item && item.id}:${item && item.name}:${variantsOf(item)}`)
      .join("|");
    if (key === catalogKey) return;
    catalogKey = key;
    const keep = select.value || lastTarget;
    clear(select);
    variants.clear();
    for (const item of list) {
      const name = String((item && item.name) || "");
      if (!name) continue;
      const id = (item && item.id) || "-";
      const option = el("option", "", `${id} · ${name}`);
      option.value = name;
      select.append(option);
      variants.set(name, variantsOf(item));
    }
    if (keep) select.value = keep;
    if (!select.value && select.options.length) select.selectedIndex = 0;
    fillVariants(select.value);
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
      const item = el("div", `gitem ${vmAccent(id)}`);
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
    /* Three knobs the uplink has always taken and the page never sent:
       which variant to build, whether to run the verification scenario
       instead of an interactive machine, and a stop armed at launch —
       before the guest can reach the event and pass it by. */
    const data = { demo: target, variant: variantSelect.value || null, verify: verifyBox.checked };
    const armed = stops?.() || [];
    if (armed.length) data.stops = armed;
    if (!send("target", data)) {
      onNotice?.("브리지에 연결되지 않아 실행 요청을 보내지 못했습니다");
      return;
    }
    lastTarget = target;
    remember({ demo: target, variant: data.variant, verify: data.verify });
    arm(false);
    onStart?.(target);
  }

  function stop() {
    if (!send("stop", {})) {
      onNotice?.("브리지에 연결되지 않아 정지 요청을 보내지 못했습니다");
      return;
    }
    arm(false);
    onNotice?.("정지 요청");
  }

  function arm(on) {
    runButton.disabled = replaying || !on;
  }

  function setPhase(phase) {
    replaying = String(phase) === "replay";
    active = ACTIVE.has(String(phase));
    runButton.textContent = active ? "정지" : "실행";
    arm(true);
  }

  select.addEventListener("change", () => fillVariants(select.value));
  runButton.addEventListener("click", () => (active ? stop() : start(select.value)));

  return { render, setPhase, arm };
}

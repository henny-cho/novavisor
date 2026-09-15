/* The launch group — target picker, variant, verify, run/stop and pause
   — and the guest topology summary. The picker and the rail are driven
   by the topo snapshot: the catalog fills one, the guest list the other.
   Every control the group owns follows the shared control state. */

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

function store(choice) {
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
  pauseButton,
  pane,
  send,
  stops,
  onStart,
  onPending,
  onNotice,
}) {
  let catalogKey = null;
  /* One button, two meanings. Its text is the machine's presence and its
     click is whichever of launch and stop that presence leaves open. */
  let active = false;
  let replaying = false;
  let building = false;
  let paused = false;
  /* The one memory of what 실행 sends next: the last launch this page
     made or saw. Held here for the picker, stored for the next reload. */
  let held = stored();
  let adoptedRun; /* the run whose launch the picker last took as its own */
  const variants = new Map();
  verifyBox.checked = Boolean(held.verify);

  function remember(choice) {
    held = choice;
    store(choice);
  }

  const variantsOf = (item) =>
    (Array.isArray(item && item.variants) ? item.variants : []).map(String);

  /* A demo's own variants, or nothing to pick between: the catalogue
     names only real ones, so a plain manifest hides the picker. */
  function fillVariants(demo, keep = variantSelect.value || (demo === held.demo ? held.variant : "")) {
    const names = variants.get(String(demo || "")) || [];
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
    const keep = select.value || held.demo;
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
    /* A run boundary: whoever launched the machine — this page, another,
       the CLI — its launch is what 실행 sends next. Once per run, and only
       a target the catalogue offers, so a republish within a run leaves
       the reader's pick alone. A replay carries no run to adopt. */
    const demo = data.demo ? String(data.demo) : "";
    if (data.run_id !== undefined && data.run_id !== adoptedRun && demo && variants.has(demo)) {
      adoptedRun = data.run_id;
      adopt({ demo, variant: data.variant ? String(data.variant) : null, verify: false });
    }
  }

  /* The machine's launch, taken as the reader's next one: a run that
     happened was not a verify, so the box follows. */
  function adopt(choice) {
    remember(choice);
    select.value = choice.demo;
    fillVariants(choice.demo, choice.variant || "");
    verifyBox.checked = false;
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
    remember({ demo: target, variant: data.variant, verify: data.verify });
    onPending?.("launch");
    onStart?.(target);
  }

  function stop() {
    if (!send("stop", {})) {
      onNotice?.("브리지에 연결되지 않아 정지 요청을 보내지 못했습니다");
      return;
    }
    onPending?.("stop");
    /* A stop taken during a build lands after the launch — the session
       lock holds it — so the machine starts and is at once terminated.
       The outcome is the one asked for; the delay is what to say. */
    onNotice?.(building ? "정지 요청 — 빌드가 끝난 뒤 적용됩니다" : "정지 요청 — 머신을 정지합니다");
  }

  /* A replay has no machine for any of this group to act on, so the
     whole of it stands down. Re-arming the two buttons waits for the
     wire to answer the request in flight, so a click storm is one. */
  function setState(state) {
    const phase = String(state.phase);
    replaying = Boolean(state.replaying) || phase === "replay";
    active = ACTIVE.has(phase);
    building = phase === "building";
    paused = Boolean(state.paused);
    const busy = replaying || Boolean(state.pending);
    select.disabled = replaying;
    variantSelect.disabled = replaying;
    verifyBox.disabled = replaying;
    runButton.textContent = active ? "정지" : "실행";
    runButton.disabled = busy;
    /* Only a running machine can be paused; the button offered in any
       other phase would halt one that is no longer there, and one the
       bridge already holds for a command is refused a second. */
    pauseButton.hidden = phase !== "running";
    pauseButton.textContent = paused ? "재개" : "일시정지";
    pauseButton.disabled = busy || Boolean(state.halt);
  }

  select.addEventListener("change", () => fillVariants(select.value));
  runButton.addEventListener("click", () => (active ? stop() : start(select.value)));

  /* One button, two directions, read from the state the wire reports. */
  pauseButton.addEventListener("click", () => {
    if (!send("halt", { cmd: paused ? "cont" : "stop" })) {
      onNotice?.("브리지에 연결되지 않아 요청을 보내지 못했습니다");
    }
  });

  return { render, setState };
}

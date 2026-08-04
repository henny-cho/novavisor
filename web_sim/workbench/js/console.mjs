/* Console view: one merged log plus one tab per guest, and the input that
   feeds the firmware's UART. Lines arrive already split by the bridge
   (vm === null means a hypervisor line), so this module only renders. */

import { send } from "./net.mjs";
import { atBottom, clear, el, toBottom, trim, vmSlot } from "./format.mjs";

const LINE_CAP = 5000; /* per tab; oldest lines drop out */
const MERGED = "all";
const VM_SLOTS = 4; /* accent classes v0..v3 cycle */
/* console_mux focus-cycle byte (Ctrl-T). An empty payload is never sent:
   a stray control byte could reach QEMU's own escape handling. */
const FOCUS_CYCLE = "\u0014";

export function createConsole({ tabs, logs, banner, form, input, focusButton, onNotice }) {
  const views = new Map();
  let active = MERGED;
  let signature = null;

  const slot = (index) => `v${index % VM_SLOTS}`;

  function makeView(key, label, name, accent) {
    const tab = el("button", accent ? `tab ${accent}` : "tab");
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", "false");
    tab.append(el("span", "tt", label));
    if (name) {
      tab.append(el("span", "tn", name));
      tab.title = name;
    }
    tab.addEventListener("click", () => activate(key));

    const pane = el("div", accent ? `log ${accent}` : "log");
    pane.id = `log-${key}`;
    pane.setAttribute("role", "tabpanel");
    pane.setAttribute("aria-label", name || label);
    tab.setAttribute("aria-controls", pane.id);
    pane.hidden = true;
    const view = { tab, pane, stick: true };
    pane.addEventListener("scroll", () => {
      view.stick = atBottom(pane);
    });

    views.set(key, view);
    tabs.append(tab);
    logs.append(pane);
    return view;
  }

  function merged() {
    return views.get(MERGED) || makeView(MERGED, "전체", "", "");
  }

  function guestView(index) {
    return views.get(index) || makeView(index, `vm${index}`, "", slot(index));
  }

  function activate(key) {
    active = views.has(key) ? key : MERGED;
    for (const [id, view] of views) {
      const on = id === active;
      view.pane.hidden = !on;
      view.tab.setAttribute("aria-selected", String(on));
      if (on && view.stick) toBottom(view.pane);
    }
  }

  /* Rebuild the guest tabs only when the guest set actually changed, so a
     reconnect replay of the same topology keeps every buffer intact. Tabs
     are keyed by VM slot — the same id the console frames carry. */
  function setGuests(guests) {
    const list = Array.isArray(guests) ? guests : [];
    const next = list
      .map((guest, index) => `${vmSlot(guest, index)}:${(guest && guest.name) || ""}`)
      .join("|");
    if (next === signature) return;
    signature = next;
    for (const [key, view] of views) {
      if (key === MERGED) continue;
      view.tab.remove();
      view.pane.remove();
      views.delete(key);
    }
    merged();
    list.forEach((guest, index) => {
      const id = vmSlot(guest, index);
      const label = `vm${id}`;
      const name = String((guest && guest.name) || "");
      makeView(id, label, name === label ? "" : name, slot(id));
    });
    activate(active);
  }

  function push(view, vm, text) {
    const row = el("div", vm === null ? "cline hyp" : `cline guest ${slot(vm)}`);
    row.append(el("span", "cg", vm === null ? "EL2" : `vm${vm}`));
    row.append(el("span", "ct", text === undefined || text === null ? "" : text));
    const pinned = view.pane.hidden ? view.stick : atBottom(view.pane);
    view.pane.append(row);
    trim(view.pane, LINE_CAP);
    view.stick = pinned;
    if (pinned && !view.pane.hidden) toBottom(view.pane);
  }

  function append(line) {
    const vm = Number.isInteger(line.vm) ? line.vm : null;
    push(merged(), vm, line.text);
    if (vm !== null) push(guestView(vm), vm, line.text);
  }

  /* Session divider in the merged log, so two runs never read as one. */
  function mark(text) {
    const view = merged();
    const row = el("div", "cline mark", text);
    const pinned = view.pane.hidden ? view.stick : atBottom(view.pane);
    view.pane.append(row);
    trim(view.pane, LINE_CAP);
    if (pinned && !view.pane.hidden) toBottom(view.pane);
  }

  function setBanner(text) {
    clear(banner);
    banner.hidden = !text;
    if (!text) return;
    banner.append(el("span", "bl", "PANIC"));
    banner.append(el("span", "bt", text));
  }

  function clearAll() {
    setBanner(null);
    signature = null;
    for (const [key, view] of views) {
      if (key === MERGED) {
        clear(view.pane);
        view.stick = true;
        continue;
      }
      view.tab.remove();
      view.pane.remove();
      views.delete(key);
    }
    activate(MERGED);
  }

  function transmit(bytes) {
    if (!bytes) return; /* never send an empty payload */
    if (!send("uart", { bytes })) onNotice?.("브리지에 연결되지 않아 입력을 보내지 못했습니다");
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    transmit(`${input.value}\n`); /* Enter appends the newline */
    input.value = "";
  });

  input.addEventListener("keydown", (event) => {
    if (!event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.key !== "t" && event.key !== "T") return;
    /* Focus cycling is a control byte, not typed text. */
    event.preventDefault();
    transmit(FOCUS_CYCLE);
  });

  focusButton.addEventListener("click", () => {
    transmit(FOCUS_CYCLE);
    input.focus();
  });

  merged();
  activate(MERGED);
  return { setGuests, append, mark, setBanner, clearAll };
}

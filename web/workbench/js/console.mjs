import { MAX_VM_SLOT, clear, el, vmAccent, vmSlot } from "./format.mjs";
import { StreamLog } from "./primitives/stream_log.mjs";


const LINE_CAP = 5000; /* per tab; oldest lines drop out */
const MERGED = "all";
/* console_mux focus-cycle byte (Ctrl-T). An empty payload is never sent:
   a stray control byte could reach QEMU's own escape handling. */
const FOCUS_CYCLE = "\u0014";

export function createConsole({ tabs, logs, banner, form, input, focusButton, send, onNotice }) {
  const views = new Map();
  let active = MERGED;
  let signature = null;

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
    const view = { tab, pane, stream: new StreamLog({ container: pane, lineCap: LINE_CAP }) };
    views.set(key, view);
    tabs.append(tab);
    logs.append(pane);
    return view;
  }


  function merged() {
    return views.get(MERGED) || makeView(MERGED, "전체", "", "");
  }

  function guestView(index) {
    return views.get(index) || makeView(index, `vm${index}`, "", vmAccent(index));
  }

  function activate(key) {
    active = views.has(key) ? key : MERGED;
    for (const [id, view] of views) {
      const on = id === active;
      view.pane.hidden = !on;
      view.tab.setAttribute("aria-selected", String(on));
      if (on) view.stream.pin();
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
      makeView(id, label, name === label ? "" : name, vmAccent(id));
    });
    activate(active);
  }

  function push(view, vm, text, ts) {
    const row = el("div", vm === null ? "cline hyp" : `cline guest ${vmAccent(vm)}`);
    row.append(el("span", "cg", vm === null ? "EL2" : `vm${vm}`));
    row.append(el("span", "ct", text === undefined || text === null ? "" : text));
    /* The stream stamps the row and holds the cut, so it is also what
       says whether this line is the future of where the reader is. */
    row.hidden = view.stream.append(row, ts);
  }

  /* Hide everything printed after `ts`, or show it all again with null.
     Hidden rather than removed: a cursor moves both ways, and a console
     that discarded the future would make the second move a re-fetch of
     what is already on the page. */
  function cutAt(ts) {
    for (const view of views.values()) {
      view.stream.cutTo(ts, (row, past) => {
        row.hidden = past;
      });
    }
  }

  /* One scroll write per batch instead of a forced layout per line —
     a boot burst carries thousands of lines in one flush. `stick` is
     maintained solely by each pane's scroll listener. */
  function settle() {
    for (const view of views.values()) {
      if (!view.pane.hidden) view.stream.settle();
    }
  }

  function append(line, ts) {
    const vm = Number.isInteger(line.vm) ? line.vm : null;
    push(merged(), vm, line.text, ts);
    /* A tab is a slot the board can host; guest text that merely looks
       like a tag stays in the merged log and mints nothing. */
    if (vm !== null && vm >= 0 && vm < MAX_VM_SLOT) push(guestView(vm), vm, line.text, ts);
  }

  /* Session divider in the merged log, so two runs never read as one. */
  function mark(text) {
    const view = merged();
    view.stream.append(el("div", "cline mark", text));
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
        view.stream.clear();
        continue;
      }
      view.tab.remove();
      view.pane.remove();
      views.delete(key);
    }
    activate(MERGED);
  }

  function transmit(bytes) {
    if (!bytes) return false; /* never send an empty payload */
    if (send("uart", { bytes })) return true;
    onNotice?.("브리지에 연결되지 않아 입력을 보내지 못했습니다");
    return false;
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    /* Enter appends the newline; failed input stays put for a retry. */
    if (transmit(`${input.value}\n`)) input.value = "";
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
  return { setGuests, append, mark, setBanner, settle, clearAll, cutAt };
}

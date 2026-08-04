/* VM cards: one per guest in the topology snapshot, each showing what the
   bridge actually knows at M1 — identity, vCPU count, console volume and
   the last line that guest printed. No state is inferred here. */

import { clear, el, vmSlot } from "./format.mjs";

const VM_SLOTS = 4; /* accent classes v0..v3 cycle */
const ACTIVE_MS = 700;
const IDLE_TEXT = "출력 없음";

export function createCards(root) {
  const cards = new Map();
  let signature = null;

  function emptyState() {
    if (cards.size) return;
    clear(root);
    root.append(el("div", "empty", "게스트 정보 없음 — 타깃을 실행하면 표시됩니다."));
  }

  /* id is the VM slot the firmware tags console lines with. */
  function makeCard(id, name, vcpus) {
    if (!cards.size) clear(root); /* drop the empty state */
    const label = `vm${id}`;
    const node = el("article", `card v${id % VM_SLOTS}`);
    const head = el("div", "ch");
    head.append(el("span", "cvm", label));
    /* Some manifests name a guest after its slot; repeating it adds nothing. */
    if (name && name !== label) head.append(el("span", "cnm", name));
    head.append(el("span", "cvc", Number.isFinite(vcpus) ? `vCPU ${vcpus}` : "vCPU ?"));
    const last = el("div", "cl", IDLE_TEXT);
    const foot = el("div", "cf");
    const count = el("span", "cc", "0줄");
    foot.append(count);
    foot.append(el("span", "live"));
    node.append(head, last, foot);
    root.append(node);

    const card = { node, last, count, lines: 0, timer: 0 };
    cards.set(id, card);
    return card;
  }

  function ensure(id) {
    return cards.get(id) || makeCard(id, "", NaN);
  }

  /* Rebuild only on a real topology change so a reconnect replay keeps the
     counters it already accumulated. */
  function setGuests(guests) {
    const list = Array.isArray(guests) ? guests : [];
    const next = list
      .map(
        (guest, index) =>
          `${vmSlot(guest, index)}:${(guest && guest.name) || ""}:${(guest && guest.vcpus) || ""}`,
      )
      .join("|");
    if (next === signature) return;
    signature = next;
    for (const card of cards.values()) clearTimeout(card.timer);
    cards.clear();
    clear(root);
    list.forEach((guest, index) =>
      makeCard(vmSlot(guest, index), String((guest && guest.name) || ""), Number(guest && guest.vcpus)),
    );
    emptyState();
  }

  /* One console line attributed to a guest. */
  function touch(vm, text) {
    const card = ensure(vm);
    card.lines += 1;
    card.count.textContent = `${card.lines}줄`;
    const line = text === undefined || text === null ? "" : String(text);
    card.last.textContent = line.trim() ? line : IDLE_TEXT;
    card.node.classList.add("act");
    clearTimeout(card.timer);
    card.timer = setTimeout(() => card.node.classList.remove("act"), ACTIVE_MS);
  }

  /* Run boundary: the cards stay, their accumulation starts over. */
  function reset() {
    for (const card of cards.values()) {
      clearTimeout(card.timer);
      card.timer = 0;
      card.lines = 0;
      card.count.textContent = "0줄";
      card.last.textContent = IDLE_TEXT;
      card.node.classList.remove("act");
    }
  }

  function clearAll() {
    for (const card of cards.values()) clearTimeout(card.timer);
    cards.clear();
    signature = null;
    clear(root);
    emptyState();
  }

  emptyState();
  return { setGuests, touch, reset, clearAll };
}

/* VM cards: one per guest in the topology snapshot, each showing what the
   bridge actually knows at M1 — identity, vCPU count, console volume and
   the last line that guest printed. No state is inferred here. */

import { clear, el, vmAccent, vmSlot } from "./format.mjs";
import { hostsGuest } from "./world.mjs";

const ACTIVE_MS = 700;
const IDLE_TEXT = "출력 없음";

export function createCards(root) {
  const cards = new Map();
  /* Cards a line landed on since the last settle. */
  const touched = new Set();
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
    const node = el("article", `card ${vmAccent(id)}`);
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

    const card = { node, last, count, lines: 0, text: "", timer: 0 };
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
    touched.clear();
    clear(root);
    list.forEach((guest, index) =>
      makeCard(vmSlot(guest, index), String((guest && guest.name) || ""), Number(guest && guest.vcpus)),
    );
    emptyState();
  }

  /* One console line attributed to a guest. A card is a slot the board
     can host; guest text that merely looks tagged mints nothing.
     Counting is the line's and writing is the batch's — a card shows a
     total and the last line, and a burst only ever paints its last. */
  function touch(vm, text) {
    if (!hostsGuest(vm)) return;
    const card = ensure(vm);
    card.lines += 1;
    card.text = text === undefined || text === null ? "" : String(text);
    touched.add(card);
  }

  /* End of a batch: every card a line landed on says so, once. */
  function settle() {
    for (const card of touched) {
      card.count.textContent = `${card.lines}줄`;
      card.last.textContent = card.text.trim() ? card.text : IDLE_TEXT;
      card.node.classList.add("act");
      clearTimeout(card.timer);
      card.timer = setTimeout(() => card.node.classList.remove("act"), ACTIVE_MS);
    }
    touched.clear();
  }

  /* Run boundary: the cards stay, their accumulation starts over. */
  function reset() {
    touched.clear();
    for (const card of cards.values()) {
      clearTimeout(card.timer);
      card.timer = 0;
      card.lines = 0;
      card.text = "";
      card.count.textContent = "0줄";
      card.last.textContent = IDLE_TEXT;
      card.node.classList.remove("act");
    }
  }

  function clearAll() {
    for (const card of cards.values()) clearTimeout(card.timer);
    cards.clear();
    touched.clear();
    signature = null;
    clear(root);
    emptyState();
  }

  emptyState();
  return { setGuests, touch, settle, reset, clearAll };
}

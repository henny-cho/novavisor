/* Drive: asking the machine for an event instead of waiting for one.

   Every value these controls offer comes from the run — the opcodes, the
   quantum's band, the INTID range, the wait. Offering anything else
   would teach a reader what gets refused.

   Nothing here confirms a command. The bridge reports only what could
   not be sent; what could is answered by EL2 with a trace record, which
   is what puts the instruction and its consequences on one axis. */

import { clear, el } from "./format.mjs";

const usText = (us) => (us >= 1000 ? `${Math.round(us / 1000)} ms` : `${us} us`);

export function createDrive({ root, note, send }) {
  let world = null; /* what this run says it accepts, or null */
  let contract = ""; /* the wait it promised, kept beside every verdict */
  let counter = 0; /* which mark this is, so two are distinguishable */

  const issue = (op, a = 0, b = 0) => send({ op, a, b });

  function button(label, title, onClick) {
    const control = el("button", "btn sm", label);
    control.type = "button";
    control.title = title;
    control.addEventListener("click", onClick);
    return control;
  }

  /* A select rather than a number: EL2 refuses a VM that is not
     running, and being refused for typing 9 is a poor way to learn the
     machine has two. */
  function vmPicker(guests) {
    const pick = el("select", "pick sm");
    pick.setAttribute("aria-label", "SPI를 받을 VM");
    guests.forEach((guest, index) => {
      const option = el("option", "", `vm${index}${guest?.name ? ` · ${guest.name}` : ""}`);
      option.value = String(index);
      pick.append(option);
    });
    return pick;
  }

  function renderMark(into) {
    const row = el("div", "drow");
    row.append(el("span", "dlabel", "표식"));
    row.append(
      button("남기기", "시간 축에 표식을 남긴다 — 구간을 묶는 용도", () => {
        counter += 1;
        issue("mark", counter);
      }),
    );
    into.append(row);
  }

  /* The bounds are the range EL2's vGIC model declares an SPI to be. */
  function renderSpi(into, guests, [low, high]) {
    const row = el("div", "drow");
    row.append(el("span", "dlabel", "SPI"));
    const vm = vmPicker(guests);
    const intid = el("input", "dnum");
    intid.type = "number";
    intid.min = String(low);
    intid.max = String(high);
    intid.value = String(low);
    intid.setAttribute("aria-label", "가상 INTID");
    row.append(vm, intid);
    row.append(
      button("주입", "이 VM에 가상 SPI를 걸어 주입 경로를 점등시킨다", () =>
        issue("spi", Number(vm.value), Number(intid.value)),
      ),
    );
    into.append(row);
  }

  /* The ends of the band EL2 will take, and the value it booted with. */
  function renderSlice(into, choices) {
    const row = el("div", "drow");
    row.append(el("span", "dlabel", "슬라이스"));
    for (const us of choices) {
      const label = usText(us);
      row.append(button(label, `선점 슬라이스를 ${label}로 바꾼다`, () => issue("slice", us)));
    }
    into.append(row);
  }

  function render() {
    clear(root);
    if (!world) return;
    if (world.ops.includes("mark")) renderMark(root);
    if (world.ops.includes("spi") && world.intids.length === 2) {
      renderSpi(root, world.guests, world.intids);
    }
    if (world.ops.includes("slice") && world.slice.length) renderSlice(root, world.slice);
  }

  return {
    /* What this run accepts, from the topology. Absent means the
       firmware placed no ring, and the panel says so — controls that
       quietly do nothing are what this milestone exists to remove.

       Rebuilt only when that answer changes. The topology is
       republished whenever anything on it moves (the page tables land,
       an edge is regraded), and rebuilding on each would clear the
       INTID a reader had just typed. */
    setWorld(topology) {
      const command = topology?.command;
      const ops = Array.isArray(command?.ops) ? command.ops : null;
      const next = ops
        ? {
            ops,
            guests: topology.guests || [],
            slice: Array.isArray(command.slice_us) ? command.slice_us : [],
            intids: Array.isArray(command.spi_intids) ? command.spi_intids : [],
            /* Compared because it is drawn: a run that changed only its
               drain period would otherwise return early here and leave
               the previous wait on screen for the rest of the session. */
            period: command.period_us,
          }
        : null;
      if (JSON.stringify(next) === JSON.stringify(world)) return;
      world = next;
      contract = world ? `≤${usText(world.period)}` : "";
      root.hidden = !world;
      note.classList.remove("bad");
      note.textContent = contract || "이 실행은 명령을 받지 않는다";
      render();
    },

    /* EL2's own verdict, arriving as a trace record like everything
       else the machine says — so a refusal reads the way an acceptance
       does. Shown beside the wait, which is the other half of the same
       contract and would otherwise vanish at the first command.

       Trailing zeros are dropped, interior ones kept: `spi 0 33` names
       VM 0, where `mark 1 0` says nothing the tag did not. */
    answered(record) {
      if (!record || !world) return;
      const args = [record.a, record.b];
      while (args.length && !args[args.length - 1]) args.pop();
      note.classList.toggle("bad", record.result !== "ok");
      note.textContent =
        `${contract} · ${record.op}${args.length ? ` ${args.join(" ")}` : ""} → ${record.result}`;
    },
  };
}

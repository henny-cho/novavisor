/* Drive: asking the machine for an event instead of waiting for one.

   The controls are built from what the machine published: one row per
   op it carries out, saying how many arguments it reads and what each
   one means and accepts. Nothing here holds a list of opcodes, so an op
   added to the firmware arrives as a control without this file moving.

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

  /* Korean where a name is known, the machine's own word otherwise. A
     missing entry degrades to the truth, so this list going stale
     cannot make a control lie about what it sends. */
  const LABEL = { mark: "표식", spi: "SPI", slice: "슬라이스" };
  const ACTION = { mark: "남기기", spi: "주입", slice: "적용" };


  /* A select rather than a number: EL2 refuses a VM that is not
     running, and being refused for typing 9 is a poor way to learn the
     machine has two. */
  function vmPicker(arg, guests) {
    const pick = el("select", "pick sm");
    pick.setAttribute("aria-label", "대상 VM");
    guests.forEach((guest, index) => {
      if (index < arg.lo || index > arg.hi) return;
      const option = el("option", "", `vm${index}${guest?.name ? ` · ${guest.name}` : ""}`);
      option.value = String(index);
      pick.append(option);
    });
    return pick;
  }

  /* A bounded number wears its band; a free one is a tag, which is what
     a bracket around a stretch of timeline needs — so it counts up on
     its own rather than making a reader invent distinct values. */
  function numberInput(arg) {
    const input = el("input", "dnum");
    input.type = "number";
    if (!arg.free) {
      input.min = String(arg.lo);
      input.max = String(arg.hi);
    }
    input.value = String(arg.free ? (counter += 1) : arg.default || arg.lo);
    input.setAttribute("aria-label", arg.kind === "micros" ? "마이크로초" : "값");
    return input;
  }

  /* One control per row: as many inputs as the op reads, each dressed
     by what its argument means. Nothing here knows an opcode — an op
     this build added arrives as a row and gets a control for free. */
  function renderOp(into, op, guests, commandsMeta = {}) {
    const meta = commandsMeta[op.name] || {};
    const labelText = meta.label || LABEL[op.name] || op.name;
    const actionText = meta.action || ACTION[op.name] || op.name;
    const row = el("div", "drow");
    row.append(el("span", "dlabel", labelText));
    const fields = op.args.map((arg) =>
      arg.kind === "vm" ? vmPicker(arg, guests) : numberInput(arg),
    );
    row.append(...fields);
    const bounds = op.args
      .filter((arg) => !arg.free && arg.kind !== "vm")
      .map((arg) => (arg.kind === "micros" ? `${usText(arg.lo)}–${usText(arg.hi)}` : `${arg.lo}–${arg.hi}`))
      .join(" ");
    const btnTitle = meta.desc ? `${actionText} (${meta.desc})` : (bounds ? `${op.name} · ${bounds}` : op.name);
    row.append(
      button(actionText, btnTitle, () => {
        const [a = 0, b = 0] = fields.map((field) => Number(field.value));
        issue(op.name, a, b);
        /* A free tag is spent once it is sent. */
        fields.forEach((field, index) => {
          if (op.args[index].free) field.value = String((counter += 1));
        });
      }),
    );
    into.append(row);
  }

  function render() {
    clear(root);
    if (!world) return;
    for (const op of world.ops) renderOp(root, op, world.guests, world.commandsMeta);
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
      const commandsMeta = topology?.ui_metadata?.commands || {};
      const next = ops
        ? {
            ops,
            guests: topology.guests || [],
            /* Compared because it is drawn: a run that changed only its
               drain period would otherwise return early here and leave
               the previous wait on screen for the rest of the session. */
            period: command.period_us,
            commandsMeta,
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

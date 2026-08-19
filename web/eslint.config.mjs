/* What the workbench UI is not allowed to say.

   The page ships without a bundler, so nothing between an editor and a
   browser reads it. These rules are the checks that used to be regular
   expressions over the source text: a real parser tells a literal from
   a comment, and an operator from the same characters inside a string,
   so each rule below states one contract and nothing near it.

   `no-undef` is here for the same reason and is the most basic of
   them: a free identifier stays unresolved until a browser reaches the
   line. A refactor that moves a symbol and leaves a call behind ships,
   and the first thing to notice is the reader.

   Every rule is a contract about *where a fact lives*. The bridge owns
   the machine's vocabulary — badges, addresses, sample rates, the stop
   catalogue — and publishes it in the topology snapshot; a copy typed
   into a module is a second answer that drifts the day the first one
   moves, with nothing on screen to say which is stale. */

import { readFileSync } from "node:fs";

import noLayoutReadOnDrawPath from "./eslint-rules/no-layout-read-on-draw-path.js";

/* The badge vocabulary is the bridge's, so it is read from the bridge
   rather than restated here — a list copied into this file would be the
   very thing the rule below forbids the UI from doing. */
const badges = () => {
  const source = new URL(
    "../novakit/services/workbench/taxonomy.py",
    import.meta.url,
  );
  const enumerated = readFileSync(source, "utf8")
    .split(/^class /mu)
    .find((block) => block.startsWith("Badge("));
  const names = [...(enumerated ?? "").matchAll(/^ {4}\w+ = "(\w+)"$/gmu)].map(
    (hit) => hit[1],
  );
  if (names.length === 0) throw new Error(`no badge vocabulary in ${source.pathname}`);
  return names.join("|");
};

/* A number no observed value legitimately reaches, in either notation. */
const SENTINEL = String.raw`^(?:\d{16,}n?|\d(?:\.\d+)?[eE](?:1[5-9]|[2-9]\d))$`;
/* Long enough to be a hardware address rather than a colour or a mask. */
const ADDRESS = String.raw`0x[0-9a-fA-F]{6,}`;
/* Stop names the bridge reads out of the firmware's own symbols. */
const STOPS = "post_spi_tracked|drain_eois|handle_lower_sync";

const EVERY_MODULE = [
  {
    selector: `Literal[raw=/${SENTINEL}/u]`,
    message:
      "the bridge decodes the firmware's all-bits-set 'none' to null; a module testing for the sentinel itself is relying on JSON losing precision past 2^53, which is right by accident and only until a narrower sentinel appears.",
  },
  {
    selector: `Literal[value=/^(?:${badges()})$/u]`,
    message:
      "the event vocabulary arrives in the topology snapshot; a badge spelled here is a second copy of it, free to name something the bridge stopped classifying.",
  },
];

const MEMORY_VIEW = [
  {
    selector: `Literal[raw=/${ADDRESS}/u]`,
    message:
      "addresses reach this view inside the answer it asked for; one typed into the module is a claim about a machine the page has not looked at.",
  },
  {
    selector: "BinaryExpression[operator=/^(?:<<|>>|>>>|&)$/u]",
    message:
      "a descriptor's bit layout has one source, the headers the hypervisor compiles; a shift or a mask here is a second reading of it, drifting the first time a field moves.",
  },
  {
    selector: "AssignmentExpression[operator=/^(?:<<=|>>=|>>>=)$/u]",
    message:
      "a descriptor's bit layout has one source, the headers the hypervisor compiles; a shift here is a second reading of it, drifting the first time a field moves.",
  },
  {
    selector: "Identifier[name='BigInt']",
    message:
      "the bridge hands over decoded fields, so nothing in this view is wide enough to need BigInt; reaching for it means the view started decoding.",
  },
];

const BOARD_VIEW = [
  {
    selector: `Literal[raw=/${ADDRESS}/u]`,
    message:
      "addresses reach the board in topo.board, generated from the same headers the linker script reads; one typed into the module drifts with no way for the browser to notice.",
  },
  {
    selector: String.raw`Literal[value=/\d\s*Hz/u], TemplateElement[value.raw=/\d\s*Hz/u]`,
    message:
      "a badge reading 'S 20Hz' is a claim about the observation manifest; written here it becomes a lie the moment a rate is tuned, and the screen goes on asserting it. The rate rides in topo.",
  },
  {
    selector:
      "CallExpression[callee.property.name=/^(?:match|matchAll|exec|test|search)$/u]",
    message:
      "reading the firmware's log is the bridge's job, and a contract test there ties every rule to a real firmware string; a pattern applied here is a second parser outside that contract.",
  },
  {
    selector: "FunctionDeclaration[id.name='note'] Literal[regex]",
    message:
      "the console path routes a classified line to the paths it is evidence for and never asks what the line says; a regular expression here is the board starting to read it.",
  },
  {
    selector: "FunctionDeclaration[id.name='note']:not(:has(Identifier[name='byBadge']))",
    message:
      "the console path routes by the badge the bridge assigned; routing on anything else would put the board back in the business of interpreting the message.",
  },
];

const STEPPER = [
  {
    selector: `Literal[value=/${STOPS}/u], Identifier[name=/${STOPS}/u], TemplateElement[value.raw=/${STOPS}/u]`,
    message:
      "the stop catalogue lives beside the firmware symbols it names and arrives in the topology snapshot; an event spelled here is a second copy, free to offer a stop the firmware no longer has.",
  },
  {
    selector:
      "Program:not(:has(CallExpression[callee.name='setStops'][arguments.0.object.name='topo'][arguments.0.property.name='stops']))",
    message:
      "the stop choices come from the bridge: the client has to hand topo.stops to setStops, or the controls stop following what the run actually accepts.",
  },
];

const restricted = (...rules) => ({
  "no-restricted-syntax": ["error", ...rules],
});

/* What this page touches outside the language. Written out rather than
   pulled from a package: the list is short, and it says what the UI is
   allowed to assume the browser has. */
const BROWSER = Object.fromEntries(
  [
    "clearTimeout",
    "console",
    "document",
    "getComputedStyle",
    "localStorage",
    "location",
    "requestAnimationFrame",
    "ResizeObserver",
    "setTimeout",
    "URLSearchParams",
    "WebSocket",
    "window",
  ].map((name) => [name, "readonly"]),
);

export default [
  {
    name: "workbench/language",
    files: ["workbench/js/**/*.mjs"],
    languageOptions: { ecmaVersion: "latest", sourceType: "module", globals: BROWSER },
    plugins: { workbench: { rules: { "no-layout-read-on-draw-path": noLayoutReadOnDrawPath } } },
    rules: { "no-undef": "error", ...restricted(...EVERY_MODULE) },
  },
  {
    /* `no-restricted-syntax` takes one option list, so a view that adds
       its own contracts restates the shared ones rather than merging. */
    name: "workbench/memory-view",
    files: ["workbench/js/memory.mjs"],
    rules: restricted(...EVERY_MODULE, ...MEMORY_VIEW),
  },
  {
    name: "workbench/board-view",
    files: ["workbench/js/board.mjs"],
    rules: {
      ...restricted(...EVERY_MODULE, ...BOARD_VIEW),
      "workbench/no-layout-read-on-draw-path": [
        "error",
        {
          /* Sizing and drag handlers are absent from this list on
             purpose: they run on a gesture, not on a value. */
          entry: "^(?:render|paint|draw|flash|note|relink|residency|put)",
          seam: "measure",
          apis: [
            "getBoundingClientRect",
            "offsetWidth",
            "offsetHeight",
            "offsetTop",
            "offsetLeft",
            "clientWidth",
            "clientHeight",
            "clientTop",
            "clientLeft",
            "scrollWidth",
            "scrollHeight",
            "getComputedStyle",
          ],
        },
      ],
    },
  },
  {
    name: "workbench/stepper",
    files: ["workbench/js/main.mjs"],
    rules: restricted(...EVERY_MODULE, ...STEPPER),
  },
];

/* A value arriving must never make the browser reflow.

   The board separates measuring from writing: geometry is read in one
   seam and cached there, and everything a topic can reach only writes
   text. A single getBoundingClientRect on that path costs a forced
   layout per changed value — fourteen a batch before the split — and
   nothing on screen looks wrong when it happens.

   Naming the draw-path entry points is not enough on its own: a helper
   two calls down measures just as expensively. So the check is a walk
   of the module's own call graph from those entries. The walk stops at
   the seam, which is where the module is supposed to measure and which
   caches what it read. */

const holder = (functions, name, node) => {
  const found = functions.get(name);
  if (found) {
    found.node ??= node;
    return found;
  }
  const fresh = { node, calls: new Set(), reads: [] };
  functions.set(name, fresh);
  return fresh;
};

/* A function's name where one can be read: the declaration's own, or
   the binding an arrow was assigned to. Anything else is anonymous and
   its body belongs to the nearest named function around it. */
const nameOf = (node) => {
  if (node.type === "FunctionDeclaration") return node.id?.name ?? "";
  const parent = node.parent;
  if (parent?.type === "VariableDeclarator" && parent.id.type === "Identifier") {
    return parent.id.name;
  }
  return node.id?.name ?? "";
};

export default {
  meta: {
    type: "problem",
    docs: {
      description:
        "Forbid layout-forcing DOM reads in any function a snapshot reaches.",
    },
    schema: [
      {
        type: "object",
        properties: {
          entry: { type: "string" },
          seam: { type: "string" },
          apis: { type: "array", items: { type: "string" }, minItems: 1 },
        },
        required: ["entry", "seam", "apis"],
        additionalProperties: false,
      },
    ],
    messages: {
      reads:
        "{{api}} runs on the draw path ({{path}}): a snapshot must never force a layout, so geometry is read in {{seam}}() and cached there.",
      noSeam:
        "{{seam}}() is gone: the module has no single place to read geometry, so every draw path is free to measure.",
      blindSeam:
        "{{seam}}() reads no layout: the measuring seam stopped measuring, and this rule now proves nothing.",
      noEntries:
        "no draw-path function matches {{entry}}: the names moved and this rule stopped watching anything.",
    },
  },

  create(context) {
    const { entry, seam, apis } = context.options[0];
    const draws = new RegExp(entry, "u");
    const layout = new Set(apis);
    const functions = new Map();
    const scopes = [];
    const enclosing = () => {
      for (let at = scopes.length - 1; at >= 0; at -= 1) {
        if (scopes[at]) return functions.get(scopes[at]);
      }
      return null;
    };

    return {
      ":function"(node) {
        const name = nameOf(node);
        scopes.push(name);
        if (name) holder(functions, name, node);
      },
      ":function:exit"() {
        scopes.pop();
      },
      Identifier(node) {
        const inside = enclosing();
        if (!inside) return;
        if (layout.has(node.name)) inside.reads.push(node);
        else if (node.parent.type === "CallExpression" && node.parent.callee === node) {
          inside.calls.add(node.name);
        }
      },
      "Program:exit"(program) {
        const entries = [...functions.keys()].filter(
          (name) => draws.test(name) && name !== seam,
        );
        if (entries.length === 0) {
          context.report({ node: program, messageId: "noEntries", data: { entry } });
          return;
        }
        const measured = functions.get(seam);
        if (!measured) {
          context.report({ node: program, messageId: "noSeam", data: { seam } });
        } else if (measured.reads.length === 0) {
          context.report({ node: measured.node, messageId: "blindSeam", data: { seam } });
        }

        /* Reachability, breadth first: the map is its own queue, since
           an entry added while it is being walked is still visited, and
           the first way in is the one the report names. */
        const from = new Map(entries.map((name) => [name, name]));
        for (const name of from.keys()) {
          for (const callee of functions.get(name).calls) {
            if (callee !== seam && functions.has(callee) && !from.has(callee)) {
              from.set(callee, from.get(name));
            }
          }
        }
        for (const [name, origin] of from) {
          const path = origin === name ? name : `${origin} reaches ${name}`;
          for (const node of functions.get(name).reads) {
            context.report({ node, messageId: "reads", data: { api: node.name, path, seam } });
          }
        }
      },
    };
  },
};

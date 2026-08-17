/* A DOM small enough to read.

   The workbench views are plain functions over elements: they build
   nodes, set text, and read back a handful of properties. What a test
   of them needs is a tree with identity, not a browser — so this is the
   tree, and every member below is one some module under test actually
   touches. jsdom would be a large dependency answering the same
   question, and its fidelity is precisely what is not in doubt here.

   Where a browser's behaviour would otherwise hide a fault it is copied
   rather than approximated: a <select> reports its first option's
   value, because a test that had to set it by hand would pass over a
   picker the page never populated. */

class Style {
  constructor() {
    this.properties = new Map();
  }

  setProperty(name, value) {
    this.properties.set(name, value);
  }

  getPropertyValue(name) {
    return this.properties.get(name) ?? "";
  }
}

class Element {
  constructor(tagName) {
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.handlers = new Map();
    this.dataset = {};
    this.style = new Style();
    this.classes = new Set();
    this.own = ""; /* text set directly on this node */
    this.hidden = false;
    this.focused = false;
    this.scrollLeft = 0;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    /* What getBoundingClientRect answers; a test that cares sets it. */
    this.rect = { left: 0, top: 0, width: 0, height: 0 };
    /* A text field reads back as empty before anything is typed, as the
       real one does; a view that sends `${input.value}` must not be able
       to send the word "undefined" here and pass. A <select> keeps
       reporting undefined — that is what the option rule below reads. */
    if (this.tagName === "INPUT") this.value = "";
    this.classList = {
      add: (...names) => names.forEach((name) => this.classes.add(name)),
      remove: (...names) => names.forEach((name) => this.classes.delete(name)),
      contains: (name) => this.classes.has(name),
      toggle: (name, force) => {
        const on = force === undefined ? !this.classes.has(name) : Boolean(force);
        if (on) this.classes.add(name);
        else this.classes.delete(name);
        return on;
      },
    };
  }

  get className() {
    return [...this.classes].join(" ");
  }

  set className(value) {
    this.classes = new Set(String(value).split(/\s+/u).filter(Boolean));
  }

  get textContent() {
    return this.own + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this.children = [];
    this.own = String(value);
  }

  get firstChild() {
    return this.children[0] ?? null;
  }

  get firstElementChild() {
    return this.children[0] ?? null;
  }

  get childElementCount() {
    return this.children.length;
  }

  append(...nodes) {
    for (const node of nodes) {
      node.parentNode = this;
      this.children.push(node);
      /* A picker with no selection answers with its first option. */
      if (this.tagName === "SELECT" && node.tagName === "OPTION" && this.value === undefined) {
        this.value = node.value;
      }
    }
  }

  removeChild(node) {
    this.children = this.children.filter((child) => child !== node);
    node.parentNode = null;
    return node;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  addEventListener(type, handler) {
    if (!this.handlers.has(type)) this.handlers.set(type, []);
    this.handlers.get(type).push(handler);
  }

  setPointerCapture() {}

  focus() {
    this.focused = true;
  }

  getBoundingClientRect() {
    const { left, top, width, height } = this.rect;
    return { left, top, width, height, right: left + width, bottom: top + height };
  }

  /* One class selector, which is all any view asks for. Anything else
     throws rather than answering nothing, so a widened query fails here
     instead of quietly matching. */
  querySelector(selector) {
    if (!selector.startsWith(".")) throw new Error(`unsupported selector: ${selector}`);
    return find(this, selector.slice(1));
  }
}

/* A canvas is measured and drawn on; nothing reads back what was
   painted, so the context only has to exist and not throw. */
class Canvas extends Element {
  constructor() {
    super("canvas");
    this.width = 0;
    this.height = 0;
    this.tabIndex = 0;
  }

  getContext() {
    const pen = {
      canvas: this,
      clearRect() {},
      fillRect() {},
      fillText() {},
      beginPath() {},
      moveTo() {},
      lineTo() {},
      stroke() {},
      createPattern: () => ({ pattern: true }),
    };
    return pen;
  }
}

class Document {
  constructor() {
    this.body = new Element("body");
    this.pending = null;
  }

  /* Stage a fault where a view is building. No value the bridge can
     send makes a panel renderer throw — that is what the provenance kit
     is for — so this is the only honest way to ask what the drawer does
     when one does. */
  failOn(tag, error) {
    this.pending = { tag, error };
  }

  createElement(tag) {
    if (this.pending && this.pending.tag === tag) {
      const { error } = this.pending;
      this.pending = null;
      throw error;
    }
    return tag === "canvas" ? new Canvas() : new Element(tag);
  }
}

class Storage {
  constructor() {
    this.held = new Map();
  }

  getItem(key) {
    return this.held.has(key) ? this.held.get(key) : null;
  }

  setItem(key, value) {
    this.held.set(key, String(value));
  }
}

/* Frames run where they were asked for. Everything under test draws
   from records rather than from what is already painted, so a deferred
   frame would only make a test wait to observe the same thing. */
export function installDom() {
  const document = new Document();
  globalThis.document = document;
  globalThis.window = globalThis;
  globalThis.devicePixelRatio = 1;
  globalThis.localStorage = new Storage();
  globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
  globalThis.getComputedStyle = () => new Style();
  globalThis.requestAnimationFrame = (frame) => {
    frame();
    return 0;
  };
  globalThis.ResizeObserver = class {
    observe() {}

    disconnect() {}
  };
  return document;
}

export const element = (tag) => globalThis.document.createElement(tag);

/* What a listener the module registered would receive. */
export function fire(node, type, event = {}) {
  for (const handler of node.handlers.get(type) ?? []) handler(event);
}

/* A gesture that remembers whether the handler stopped the page from
   acting on it — submitting the form, typing the letter. Without this
   the call is a no-op stub and the refusal is invisible. */
export function gesture(fields = {}) {
  const event = { ...fields, prevented: false };
  event.preventDefault = () => {
    event.prevented = true;
  };
  return event;
}

export function walk(node, seen = []) {
  seen.push(node);
  for (const child of node.children) walk(child, seen);
  return seen;
}

export const findAll = (node, className) =>
  walk(node).filter((found) => found !== node && found.classes.has(className));

export const find = (node, className) => findAll(node, className)[0] ?? null;

export const byTag = (node, tagName) =>
  walk(node).filter((found) => found.tagName === tagName.toUpperCase());

/* A table's body as text, row by row, which is how a reader checks one.
   The header is `th` and drops out by itself. */
export const rowsOf = (table) =>
  byTag(table, "tr")
    .map((row) => byTag(row, "td").map((cell) => cell.textContent))
    .filter((cells) => cells.length);

export const movedIn = (table) =>
  byTag(table, "td")
    .filter((cell) => cell.classes.has("moved"))
    .map((cell) => cell.textContent);

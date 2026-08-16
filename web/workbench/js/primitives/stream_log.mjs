/* Shared scroll-pinned, capped line stream log buffer manager. */

import { atBottom, toBottom, trim } from "../format.mjs";

export class StreamLog {
  constructor({ container, lineCap = 2000, slack = 12 }) {
    this.container = container;
    this.lineCap = lineCap;
    this.slack = slack;
    this.stick = true;
    this.dirty = false;

    this.container.addEventListener("scroll", () => {
      this.stick = atBottom(this.container, this.slack);
    });
  }

  append(node) {
    this.container.append(node);
    trim(this.container, this.lineCap);
    this.dirty = true;
  }

  settle() {
    if (!this.dirty) return;
    this.dirty = false;
    if (this.stick) toBottom(this.container);
  }

  clear() {
    while (this.container.firstChild) {
      this.container.removeChild(this.container.firstChild);
    }
    this.stick = true;
    this.dirty = false;
  }
}

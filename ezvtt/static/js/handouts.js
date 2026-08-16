/* Handouts: the image the GM is holding up.
 *
 * One component, three surfaces. Everyone sees the same picture -- that is the
 * point of holding something up -- but only the GM's close button takes it
 * down for the table. A player closing it closes their own copy, and the next
 * push brings it back. See ADR-018.
 */

export class HandoutOverlay extends EventTarget {
  #root;
  #socket;
  #editable;
  #showing = null;
  #dismissed = null;

  constructor(root, socket, { editable = false } = {}) {
    super();
    this.#root = root;
    this.#socket = socket;
    this.#editable = editable;

    root.querySelector("[data-handout-close]")
      ?.addEventListener("click", () => this.close());

    // Escape closes it, because every overlay in every program does.
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !this.#root.hidden) this.close();
    });
  }

  get showing() {
    return this.#showing;
  }

  /** Apply what the server says is up. */
  apply(handout) {
    const changed = (handout?.id ?? null) !== (this.#showing?.id ?? null);
    this.#showing = handout || null;

    // A new handout reopens for someone who closed the last one: the GM held
    // up something else, and "I dismissed the previous one" is not an answer.
    if (changed) this.#dismissed = null;

    this.#render();
  }

  /** Take it down. For the GM that means for everyone. */
  close() {
    if (this.#editable) {
      this.#socket.send("handout.hide");
      return;
    }
    this.#dismissed = this.#showing?.id ?? null;
    this.#render();
  }

  #render() {
    const handout = this.#showing;
    const hidden = !handout || this.#dismissed === handout.id;
    this.#root.hidden = hidden;
    if (hidden) return;

    const image = this.#root.querySelector("[data-handout-image]");
    if (image.getAttribute("src") !== handout.url) image.src = handout.url;
    // textContent: titles come from uploaded filenames and the GM's keyboard.
    image.alt = handout.title;
    this.#root.querySelector("[data-handout-title]").textContent = handout.title;
  }
}

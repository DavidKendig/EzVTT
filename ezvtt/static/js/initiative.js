/* The initiative tracker panel.
 *
 * One component, three surfaces. The GM gets the controls; the player view and
 * the projector get the same list read-only, and only while a combat is
 * actually running -- an empty tracker sitting over the map is chrome nobody
 * asked for.
 *
 * The display window authenticates as the GM, so the server sends it concealed
 * entries -- correctly, since the GM screen shows them ghosted. The projector
 * must not, so they are dropped here, exactly as display.js already does with
 * hidden tokens.
 */

export class InitiativePanel extends EventTarget {
  #root;
  #socket;
  #variant;
  #state = { round: 0, entries: [], current_id: null };

  constructor(root, socket) {
    super();
    this.#root = root;
    this.#socket = socket;
    this.#variant = root.dataset.variant || "play";

    if (this.#variant === "gm") this.#bindControls();
  }

  get editable() {
    return this.#variant === "gm";
  }

  /** The token acting right now, or null. Used to ring it on the board. */
  get currentTokenId() {
    const entry = this.#visible().find((e) => e.id === this.#state.current_id);
    return entry ? entry.token_id : null;
  }

  #visible() {
    // The projector drops concealed entries. The GM screen keeps them, drawn
    // dimmed, so the GM can see what the table cannot.
    return this.#variant === "display"
      ? this.#state.entries.filter((e) => !e.hidden)
      : this.#state.entries;
  }

  apply(tracker) {
    this.#state = tracker || { round: 0, entries: [], current_id: null };
    this.#render();
    this.dispatchEvent(new CustomEvent("change", { detail: this.#state }));
  }

  // ------------------------------------------------------------ rendering --

  #render() {
    const entries = this.#visible();
    const running = this.#state.round > 0;

    // The GM's panel is always available -- it is where combat is started from.
    // The other two appear when there is a fight to follow.
    this.#root.hidden = !this.editable && !(running && entries.length > 0);

    const round = this.#root.querySelector("[data-init-round]");
    round.textContent = running ? `Round ${this.#state.round}` : "Initiative";

    const list = this.#root.querySelector("[data-init-list]");
    list.replaceChildren();
    for (const entry of entries) list.append(this.#row(entry));

    const empty = this.#root.querySelector("[data-init-empty]");
    if (empty) empty.hidden = entries.length > 0;

    const toggle = this.#root.querySelector("[data-init-toggle]");
    if (toggle) {
      toggle.textContent = running ? "End combat" : "Start combat";
      toggle.classList.toggle("btn--primary", !running);
    }
    const next = this.#root.querySelector("[data-init-next]");
    if (next) next.disabled = entries.length === 0;
  }

  #row(entry) {
    const item = document.createElement("li");
    item.className = "initiative__entry";
    if (entry.id === this.#state.current_id) item.classList.add("initiative__entry--current");
    if (entry.hidden) item.classList.add("initiative__entry--hidden");

    const name = document.createElement("span");
    name.className = "initiative__name";
    // textContent: labels come from token names and from the GM's keyboard.
    name.textContent = entry.label;

    if (this.editable) {
      // The GM's score *is* the input. A separate display and edit field for
      // the same number is one more place for the two to disagree.
      item.append(
        this.#number(entry, "value", 0.5, "Initiative score", "initiative__score"),
        name,
        this.#actions(entry),
      );
    } else {
      const value = document.createElement("span");
      value.className = "initiative__value";
      value.textContent = formatValue(entry.value);
      item.append(value, name);
    }

    if (this.editable) {
      // Clicking the row hands the turn to it -- "no, we skipped Anya" is the
      // most common correction at a table, and it should be one click.
      item.addEventListener("click", () =>
        this.#socket.send("initiative.jump", { entry_id: entry.id }));
    }
    return item;
  }

  #actions(entry) {
    const actions = document.createElement("span");
    actions.className = "initiative__actions";

    actions.append(
      this.#number(entry, "modifier", 1, "Initiative modifier", "initiative__mod"),
      this.#button("d20", "Roll d20 + modifier for this one",
        () => this.#socket.send("initiative.roll", { entry_id: entry.id })),
      // Labelled with the action, not the state -- the state is already legible
      // from the dimmed row.
      this.#button(entry.hidden ? "Reveal" : "Hide",
        entry.hidden
          ? "Players cannot see this entry; show it to them"
          : "Take this entry out of the players' order",
        () => this.#socket.send("initiative.update", {
          entry_id: entry.id, hidden: !entry.hidden,
        })),
      this.#button("×", "Remove from the order",
        () => this.#socket.send("initiative.remove", { entry_id: entry.id })),
    );
    return actions;
  }

  #number(entry, key, step, title, className) {
    const input = document.createElement("input");
    input.className = `input input--num ${className}`;
    input.type = "number";
    input.step = String(step);
    input.value = entry[key];
    input.title = title;
    input.setAttribute("aria-label", `${title} for ${entry.label}`);
    // Both stopped: clicking a field to type in it must not also hand that
    // creature the turn.
    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("change", () => this.#socket.send("initiative.update", {
      entry_id: entry.id, [key]: Number(input.value),
    }));
    return input;
  }

  #button(label, title, onClick) {
    const button = document.createElement("button");
    button.className = "btn btn--ghost btn--sm";
    button.type = "button";
    button.title = title;
    button.textContent = label;
    button.addEventListener("click", (event) => {
      event.stopPropagation();            // the row itself hands over the turn
      onClick();
    });
    return button;
  }

  // ------------------------------------------------------------- controls --

  #bindControls() {
    const on = (selector, handler) => {
      const element = this.#root.querySelector(selector);
      if (element) element.addEventListener("click", handler);
    };

    on("[data-init-next]", () => this.#socket.send("initiative.advance", { delta: 1 }));
    on("[data-init-prev]", () => this.#socket.send("initiative.advance", { delta: -1 }));
    on("[data-init-roll]", () => this.#socket.send("initiative.roll"));

    on("[data-init-toggle]", () => {
      this.#socket.send(this.#state.round > 0 ? "initiative.stop" : "initiative.start");
    });

    on("[data-init-add-tokens]", () => {
      this.dispatchEvent(new CustomEvent("add-tokens"));
    });

    on("[data-init-add]", () => {
      const label = prompt("Who is joining the order?");
      if (label === null || label.trim() === "") return;
      this.#socket.send("initiative.add", { label });
    });

    on("[data-init-clear]", () => {
      if (this.#state.entries.length === 0) return;
      if (confirm("Empty the initiative order?")) this.#socket.send("initiative.clear");
    });
  }
}

/** 17 rather than 17.0, but 17.5 kept -- half points break ties by hand. */
function formatValue(value) {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(1)));
}

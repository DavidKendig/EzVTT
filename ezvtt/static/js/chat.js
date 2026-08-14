/* The chat and dice panel.
 *
 * Shared by the GM screen and the player view. Deliberately absent from
 * /display: that window is the shared table view and stays chrome-free.
 *
 * Only notation is sent for a roll. The server evaluates it and broadcasts the
 * result, so nothing here can decide what a die landed on.
 */

const QUICK_ROLLS = ["d4", "d6", "d8", "d10", "d12", "d20", "d100"];

export class ChatPanel {
  #root;
  #log;
  #input;
  #privateToggle;
  #whisperSelect;
  #socket;
  #roster = [];
  #seen = new Set();

  constructor(root, socket) {
    this.#root = root;
    this.#socket = socket;
    this.#log = root.querySelector("[data-chat-log]");
    this.#input = root.querySelector("[data-chat-input]");
    this.#privateToggle = root.querySelector("[data-chat-private]");
    this.#whisperSelect = root.querySelector("[data-chat-whisper]");

    this.#buildDiceButtons();
    this.#bind();

    socket.addEventListener("chat", (e) => this.#append(e.detail.message));
    socket.addEventListener("chat.history", (e) => {
      this.#log.replaceChildren();
      this.#seen.clear();
      for (const message of e.detail.messages) this.#append(message, false);
      this.#setRoster(e.detail.roster || []);
      this.#scroll();
    });
    socket.addEventListener("error", (e) => this.#system(e.detail.message, true));
  }

  // ------------------------------------------------------------------ ui --

  #buildDiceButtons() {
    const tray = this.#root.querySelector("[data-dice-tray]");
    if (!tray) return;

    for (const notation of QUICK_ROLLS) {
      const button = document.createElement("button");
      button.className = "die-btn";
      button.type = "button";
      button.textContent = notation;
      button.title = `Roll ${notation}`;
      button.addEventListener("click", () => this.#roll(notation));
      tray.append(button);
    }

    // Advantage and disadvantage get their own buttons: a 5e table reaches for
    // them constantly and nobody wants to type "2d20kh1" mid-combat.
    for (const [label, notation, title] of [
      ["adv", "2d20kh1", "Roll with advantage"],
      ["dis", "2d20kl1", "Roll with disadvantage"],
    ]) {
      const button = document.createElement("button");
      button.className = "die-btn die-btn--wide";
      button.type = "button";
      button.textContent = label;
      button.title = title;
      button.addEventListener("click", () => this.#roll(notation));
      tray.append(button);
    }
  }

  #setRoster(roster) {
    this.#roster = roster;
    if (!this.#whisperSelect) return;

    this.#whisperSelect.replaceChildren();
    this.#whisperSelect.append(new Option("Everyone", ""));
    for (const person of roster) {
      this.#whisperSelect.append(new Option(`→ ${person.display_name}`, String(person.id)));
    }
    this.#whisperSelect.hidden = roster.length === 0;
  }

  #bind() {
    const form = this.#root.querySelector("[data-chat-form]");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.#send();
    });

    this.#root.querySelector("[data-chat-toggle]")?.addEventListener("click", () => {
      this.#root.classList.toggle("chat--collapsed");
    });
  }

  // --------------------------------------------------------------- send --

  #send() {
    const text = this.#input.value.trim();
    if (!text) return;
    this.#input.value = "";

    // "/r 2d6+3" or "/roll 2d6+3" rolls instead of speaking. Typed notation is
    // faster than the buttons once you know what you want.
    const rollMatch = /^\/(?:r|roll)\s+(.+)$/i.exec(text);
    if (rollMatch) {
      this.#roll(rollMatch[1]);
      return;
    }

    const target = this.#whisperSelect?.value;
    if (target) {
      this.#socket.send("chat.whisper", { target_user_id: Number(target), text });
    } else {
      this.#socket.send("chat.say", { text });
    }
  }

  #roll(notation) {
    this.#socket.send("chat.roll", {
      notation,
      private: Boolean(this.#privateToggle?.checked),
    });
  }

  // ------------------------------------------------------------- render --

  #append(message, scroll = true) {
    // The server can resend history after a reconnect; do not double up.
    if (message.id != null) {
      if (this.#seen.has(message.id)) return;
      this.#seen.add(message.id);
    }

    const row = document.createElement("div");
    row.className = "chat-msg";
    if (message.private) row.classList.add("chat-msg--private");
    if (message.kind === "whisper") row.classList.add("chat-msg--whisper");

    const head = document.createElement("div");
    head.className = "chat-msg__head";

    const who = document.createElement("span");
    who.className = "chat-msg__author";
    who.textContent = message.author;          // textContent: names are user input
    head.append(who);

    if (message.private) {
      const tag = document.createElement("span");
      tag.className = "chat-msg__tag";
      tag.textContent = message.kind === "whisper" ? "whisper" : "private";
      head.append(tag);
    }

    row.append(head);

    if (message.kind === "roll" && message.roll) {
      row.append(this.#rollBody(message.roll));
    } else {
      const body = document.createElement("div");
      body.className = "chat-msg__body";
      body.textContent = message.body;
      row.append(body);
    }

    this.#log.append(row);
    if (scroll) this.#scroll();
  }

  #rollBody(roll) {
    const wrap = document.createElement("div");
    wrap.className = "chat-roll";

    const total = document.createElement("span");
    total.className = "chat-roll__total";
    total.textContent = String(roll.total);

    const notation = document.createElement("span");
    notation.className = "chat-roll__notation";
    notation.textContent = roll.notation;

    const detail = document.createElement("span");
    detail.className = "chat-roll__detail";
    // Every die shown, dropped ones struck through, so advantage is legible and
    // nobody has to take the total on trust.
    for (const [index, term] of roll.terms.entries()) {
      if (index > 0 || term.sign < 0) {
        const op = document.createElement("span");
        op.textContent = term.sign < 0 ? " − " : " + ";
        detail.append(op);
      }
      if (term.flat) {
        const flat = document.createElement("span");
        flat.textContent = String(term.flat);
        detail.append(flat);
        continue;
      }
      for (const [i, die] of term.dice.entries()) {
        const chip = document.createElement("span");
        chip.className = "die" + (die.kept ? "" : " die--dropped");
        if (die.kept && die.value === term.sides) chip.classList.add("die--max");
        if (die.kept && die.value === 1) chip.classList.add("die--min");
        chip.textContent = String(die.value);
        detail.append(chip);
        if (i < term.dice.length - 1) detail.append(document.createTextNode(" "));
      }
    }

    wrap.append(total, notation, detail);
    return wrap;
  }

  #system(text, isError = false) {
    const row = document.createElement("div");
    row.className = "chat-msg chat-msg--system" + (isError ? " chat-msg--error" : "");
    row.textContent = text;
    this.#log.append(row);
    this.#scroll();
  }

  #scroll() {
    this.#log.scrollTop = this.#log.scrollHeight;
  }
}

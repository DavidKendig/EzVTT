/* "Players join here" — the address and a QR code, on the GM screen.
 *
 * The console banner prints this at launch, but by the time players actually
 * arrive the GM is looking at the browser and the terminal is behind it.
 */

export class JoinPanel {
  #root;
  #body;
  #overlay;
  #info = null;

  constructor(root) {
    this.#root = root;
    this.#body = root.querySelector("[data-join-body]");
    this.#overlay = document.getElementById("join-overlay");

    root.querySelector("[data-join-refresh]")?.addEventListener("click", () => this.load());

    this.#overlay?.addEventListener("click", () => this.#hideOverlay());
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") this.#hideOverlay();
    });
  }

  async load() {
    try {
      const response = await fetch("/api/net/join");
      if (!response.ok) throw new Error();
      this.#info = await response.json();
    } catch {
      this.#body.replaceChildren(this.#note("Could not read the join address."));
      return;
    }
    this.#render();
  }

  #render() {
    const info = this.#info;
    this.#body.replaceChildren();

    if (!info.shareable) {
      this.#body.append(this.#note(info.note));
      return;
    }

    // The QR first: pointing a phone camera at it is the fastest way in, and
    // it is the reason this panel exists at all.
    if (info.qr_svg) {
      const holder = document.createElement("button");
      holder.className = "join__qr";
      holder.type = "button";
      holder.title = "Click to enlarge for the table";
      holder.innerHTML = info.qr_svg;      // server-generated SVG, not user input
      holder.addEventListener("click", () => this.#showOverlay());
      this.#body.append(holder);
    }

    const url = document.createElement("div");
    url.className = "join__url";
    url.textContent = info.url;
    this.#body.append(url);

    const copy = document.createElement("button");
    copy.className = "btn btn--sm";
    copy.type = "button";
    copy.textContent = "Copy address";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(info.url);
        copy.textContent = "Copied";
        setTimeout(() => { copy.textContent = "Copy address"; }, 1500);
      } catch {
        // Clipboard access needs a secure context, which plain http on a LAN
        // is not. Select the text instead so it can be copied by hand.
        const range = document.createRange();
        range.selectNodeContents(url);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        copy.textContent = "Press Ctrl+C";
      }
    });

    const enlarge = document.createElement("button");
    enlarge.className = "btn btn--sm";
    enlarge.type = "button";
    enlarge.textContent = "Show to the table";
    enlarge.addEventListener("click", () => this.#showOverlay());

    const row = document.createElement("div");
    row.className = "row row--wrap";
    row.style.gap = "4px";
    row.append(copy, enlarge);
    this.#body.append(row);

    if (info.note) this.#body.append(this.#note(info.note));

    if (info.alternatives?.length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.className = "join__alt-toggle";
      summary.textContent = `${info.alternatives.length} other address${
        info.alternatives.length === 1 ? "" : "es"}`;
      details.append(summary);
      for (const alt of info.alternatives) {
        const line = document.createElement("div");
        line.className = "join__alt";
        line.textContent = alt;
        details.append(line);
      }
      this.#body.append(details);
    }
  }

  #note(text) {
    const note = document.createElement("p");
    note.className = "control__hint";
    note.textContent = text;
    return note;
  }

  /* Full-screen so the GM can turn the laptop round and let everyone scan at
     once, rather than passing it along the table. */
  #showOverlay() {
    if (!this.#info?.qr_svg || !this.#overlay) return;
    this.#overlay.querySelector("[data-overlay-qr]").innerHTML = this.#info.qr_svg;
    this.#overlay.querySelector("[data-overlay-url]").textContent = this.#info.url;
    this.#overlay.hidden = false;
  }

  #hideOverlay() {
    if (this.#overlay) this.#overlay.hidden = true;
  }
}

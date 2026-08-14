/* The asset library panel.
 *
 * The bundle alone is 800 pieces, so this pages rather than rendering
 * everything: a picker that stutters is a picker the GM stops using mid-session.
 * Tiles are dragged onto the board, or clicked to drop one in the middle.
 */

const PAGE_SIZE = 60;

export class AssetPanel extends EventTarget {
  #root;
  #searchInput;
  #categorySelect;
  #grid;
  #status;
  #moreButton;

  #query = "";
  #category = "";
  #offset = 0;
  #total = 0;
  #searchTimer = null;
  #requestToken = 0;

  constructor(root) {
    super();
    this.#root = root;
    this.#searchInput = root.querySelector("[data-asset-search]");
    this.#categorySelect = root.querySelector("[data-asset-category]");
    this.#grid = root.querySelector("[data-asset-grid]");
    this.#status = root.querySelector("[data-asset-status]");
    this.#moreButton = root.querySelector("[data-asset-more]");

    this.#searchInput.addEventListener("input", () => {
      // Debounced: typing "barrel" would otherwise fire six queries, and the
      // replies can arrive out of order.
      clearTimeout(this.#searchTimer);
      this.#searchTimer = setTimeout(() => {
        this.#query = this.#searchInput.value;
        this.reload();
      }, 180);
    });

    this.#categorySelect.addEventListener("change", () => {
      this.#category = this.#categorySelect.value;
      this.reload();
    });

    this.#moreButton.addEventListener("click", () => this.#load(false));
  }

  async init() {
    await this.#loadCategories();
    await this.reload();
  }

  async reload() {
    this.#offset = 0;
    await this.#load(true);
  }

  async #loadCategories() {
    try {
      const response = await fetch("/api/assets/categories");
      if (!response.ok) return;
      const { categories } = await response.json();

      const total = categories.reduce((sum, c) => sum + c.count, 0);
      this.#categorySelect.replaceChildren();
      this.#categorySelect.append(new Option(`All (${total})`, ""));
      for (const { category, count } of categories) {
        this.#categorySelect.append(new Option(`${category} (${count})`, category));
      }
    } catch {
      /* The panel still works without the category filter. */
    }
  }

  async #load(replace) {
    // Out-of-order replies would otherwise render results for a query the GM
    // has already typed past.
    const token = ++this.#requestToken;

    const params = new URLSearchParams({
      q: this.#query,
      category: this.#category,
      limit: String(PAGE_SIZE),
      offset: String(this.#offset),
    });

    this.#status.textContent = "Searching…";
    let data;
    try {
      const response = await fetch(`/api/assets?${params}`);
      if (!response.ok) throw new Error();
      data = await response.json();
    } catch {
      this.#status.textContent = "Could not load assets.";
      return;
    }

    if (token !== this.#requestToken) return;

    this.#total = data.total;
    if (replace) this.#grid.replaceChildren();
    for (const asset of data.assets) this.#grid.append(this.#tile(asset));

    this.#offset += data.assets.length;
    const shown = this.#grid.childElementCount;
    this.#status.textContent = this.#total === 0
      ? "Nothing matches."
      : `${shown} of ${this.#total}`;
    this.#moreButton.hidden = shown >= this.#total;
  }

  #tile(asset) {
    const tile = document.createElement("button");
    tile.className = "asset-tile";
    tile.type = "button";
    tile.draggable = true;
    tile.title = `${asset.name} — ${fmt(asset.grid_w)}×${fmt(asset.grid_h)} squares`;

    const image = document.createElement("img");
    image.className = "asset-tile__img";
    image.src = asset.thumb_url;
    image.alt = "";
    image.loading = "lazy";
    image.draggable = false;

    const name = document.createElement("span");
    name.className = "asset-tile__name";
    name.textContent = asset.name;          // uploaded filenames are untrusted

    const size = document.createElement("span");
    size.className = "asset-tile__size";
    size.textContent = `${fmt(asset.grid_w)}×${fmt(asset.grid_h)}`;

    tile.append(image, name, size);

    tile.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("application/x-ezvtt-asset", String(asset.id));
      event.dataTransfer.effectAllowed = "copy";
    });

    tile.addEventListener("click", () => {
      this.dispatchEvent(new CustomEvent("place", { detail: { asset } }));
    });

    return tile;
  }
}

function fmt(value) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

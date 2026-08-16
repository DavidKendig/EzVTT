/* The campaign wiki and player notes.
 *
 * The wiki is read-only and the server decides what this viewer may see, so
 * this file never filters anything itself -- it renders whatever the API
 * returns. What it must NOT do is re-escape the note HTML: it arrives already
 * sanitised through an allow-list server-side, and escaping it again would show
 * every note as its own markup.
 */

const SEARCH_DEBOUNCE_MS = 220;

export class Codex {
  #root;
  #wikiBody;
  #notesBody;
  #search;
  #noteFilter;
  #searchTimer = null;
  #requestToken = 0;
  #tree = null;
  #me = null;

  constructor(root, { me = null } = {}) {
    this.#root = root;
    this.#me = me;
    this.#wikiBody = root.querySelector("[data-wiki-body]");
    this.#notesBody = root.querySelector("[data-notes-body]");
    this.#search = root.querySelector("[data-wiki-search]");
    this.#noteFilter = root.querySelector("[data-note-filter]");

    this.#bind();
  }

  #bind() {
    this.#root.querySelector("[data-codex-toggle]").addEventListener("click", () => {
      const opening = this.#root.classList.contains("codex--collapsed");
      this.#root.classList.toggle("codex--collapsed");
      // Loaded on first open rather than at page load: a GM who never opens the
      // wiki should not pay for walking their vault.
      if (opening && this.#tree === null) this.loadTree();
    });

    for (const tab of this.#root.querySelectorAll("[data-codex-tab]")) {
      tab.addEventListener("click", () => this.#showTab(tab.dataset.codexTab));
    }

    this.#search.addEventListener("input", () => {
      clearTimeout(this.#searchTimer);
      this.#searchTimer = setTimeout(() => this.#runSearch(), SEARCH_DEBOUNCE_MS);
    });

    this.#noteFilter.addEventListener("change", () => this.loadNotes());
    this.#root.querySelector("[data-note-new]")
      .addEventListener("click", () => this.#newNote());
  }

  #showTab(name) {
    for (const tab of this.#root.querySelectorAll("[data-codex-tab]")) {
      tab.classList.toggle("codex__tab--active", tab.dataset.codexTab === name);
    }
    for (const pane of this.#root.querySelectorAll("[data-codex-pane]")) {
      pane.hidden = pane.dataset.codexPane !== name;
    }
    if (name === "notes") this.loadNotes();
    else if (this.#tree === null) this.loadTree();
  }

  // ------------------------------------------------------------------ wiki --

  async loadTree() {
    this.#wikiBody.replaceChildren(this.#hint("Loading…"));
    let data;
    try {
      const response = await fetch("/api/vault/tree");
      if (!response.ok) throw new Error();
      data = await response.json();
    } catch {
      this.#wikiBody.replaceChildren(this.#hint("Could not read the wiki."));
      return;
    }

    this.#tree = data.tree || [];
    this.#renderTree();
  }

  #renderTree() {
    this.#wikiBody.replaceChildren();

    if (!this.#tree.length) {
      // Deliberately says nothing about folders that exist but are not shared.
      this.#wikiBody.append(this.#hint(
        "Nothing has been shared with you yet. Your GM chooses which folders " +
        "of the campaign wiki everyone can read."));
      return;
    }

    this.#wikiBody.append(this.#branch(this.#tree, 0));
  }

  #branch(entries, depth) {
    const list = document.createElement("div");
    list.className = "wiki-list";

    for (const entry of entries) {
      if (entry.is_dir) {
        const details = document.createElement("details");
        details.className = "wiki-folder";
        if (depth === 0) details.open = true;

        const summary = document.createElement("summary");
        summary.className = "wiki-folder__name";
        summary.textContent = entry.name;
        details.append(summary);

        if (entry.children?.length) {
          details.append(this.#branch(entry.children, depth + 1));
        } else {
          details.append(this.#hint("Empty."));
        }
        list.append(details);
      } else {
        const link = document.createElement("button");
        link.className = "wiki-note";
        link.type = "button";
        link.textContent = entry.name;
        link.addEventListener("click", () => this.openNote(entry.path));
        list.append(link);
      }
    }
    return list;
  }

  async #runSearch() {
    const query = this.#search.value.trim();
    if (query.length < 2) {
      this.#renderTree();
      return;
    }

    const token = ++this.#requestToken;
    let data;
    try {
      const response = await fetch(`/api/vault/search?q=${encodeURIComponent(query)}`);
      if (!response.ok) throw new Error();
      data = await response.json();
    } catch {
      return;
    }
    if (token !== this.#requestToken) return;   // a later query already won

    this.#wikiBody.replaceChildren();
    if (!data.results.length) {
      this.#wikiBody.append(this.#hint("Nothing matches."));
      return;
    }

    for (const hit of data.results) {
      const row = document.createElement("button");
      row.className = "wiki-hit";
      row.type = "button";

      const name = document.createElement("span");
      name.className = "wiki-hit__name";
      name.textContent = hit.name;
      row.append(name);

      if (hit.excerpt) {
        const excerpt = document.createElement("span");
        excerpt.className = "wiki-hit__excerpt";
        excerpt.textContent = hit.excerpt;
        row.append(excerpt);
      }

      row.addEventListener("click", () => this.openNote(hit.path));
      this.#wikiBody.append(row);
    }
  }

  async openNote(path) {
    this.#wikiBody.replaceChildren(this.#hint("Loading…"));

    let note;
    try {
      const response = await fetch(`/api/vault/note?path=${encodeURIComponent(path)}`);
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        this.#wikiBody.replaceChildren(
          this.#backButton(),
          this.#hint(data.error || "That note is not available."));
        return;
      }
      note = await response.json();
    } catch {
      this.#wikiBody.replaceChildren(this.#hint("Could not read that note."));
      return;
    }

    const article = document.createElement("article");
    article.className = "wiki-note-view";

    const title = document.createElement("h3");
    title.className = "wiki-note-view__title";
    title.textContent = note.name;
    article.append(title);

    const frontmatter = Object.entries(note.frontmatter || {});
    if (frontmatter.length) {
      const meta = document.createElement("dl");
      meta.className = "wiki-note-view__meta";
      for (const [key, value] of frontmatter) {
        const term = document.createElement("dt");
        term.textContent = key;
        const definition = document.createElement("dd");
        definition.textContent = value;
        meta.append(term, definition);
      }
      article.append(meta);
    }

    const body = document.createElement("div");
    body.className = "wiki-note-view__body";
    // Sanitised server-side through a tag allow-list before it was sent. Setting
    // textContent here instead would show every note as raw markup.
    body.innerHTML = note.html;
    article.append(body);

    // Follow [[wikilinks]] without a page load.
    for (const link of body.querySelectorAll("[data-vault-link]")) {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        this.openNote(decodeURIComponent(link.dataset.vaultLink));
      });
    }

    this.#wikiBody.replaceChildren(this.#backButton(), article);
    this.#wikiBody.scrollTop = 0;
  }

  #backButton() {
    const back = document.createElement("button");
    back.className = "btn btn--ghost btn--sm";
    back.type = "button";
    back.textContent = "‹ Back";
    back.addEventListener("click", () => {
      this.#search.value = "";
      this.#renderTree();
    });
    return back;
  }

  // ----------------------------------------------------------------- notes --

  async loadNotes() {
    this.#notesBody.replaceChildren(this.#hint("Loading…"));

    let data;
    try {
      const response = await fetch("/api/notes");
      if (!response.ok) throw new Error();
      data = await response.json();
    } catch {
      this.#notesBody.replaceChildren(this.#hint("Could not read your notes."));
      return;
    }

    const filter = this.#noteFilter.value;
    const notes = data.notes.filter((note) => {
      if (filter === "mine") return note.user_id === this.#me;
      if (filter === "public") return note.visibility === "public";
      return true;
    });

    this.#notesBody.replaceChildren();
    if (!notes.length) {
      this.#notesBody.append(this.#hint("No notes yet."));
      return;
    }
    for (const note of notes) this.#notesBody.append(this.#noteCard(note));
  }

  #noteCard(note) {
    const mine = note.user_id === this.#me;

    const card = document.createElement("details");
    card.className = "note-card";

    const summary = document.createElement("summary");
    summary.className = "note-card__head";

    const title = document.createElement("span");
    title.className = "note-card__title";
    title.textContent = note.title;

    const tag = document.createElement("span");
    tag.className = `note-card__tag note-card__tag--${note.visibility}`;
    tag.textContent = { private: "private", gm: "GM", public: "shared" }[note.visibility];

    summary.append(title, tag);
    if (!mine) {
      const author = document.createElement("span");
      author.className = "note-card__author";
      author.textContent = note.author;
      summary.append(author);
    }
    card.append(summary);

    const body = document.createElement("div");
    body.className = "note-card__body";

    if (mine) {
      const text = document.createElement("textarea");
      text.className = "input note-card__text";
      text.rows = 6;
      text.value = note.body;

      const titleInput = document.createElement("input");
      titleInput.className = "input input--sm";
      titleInput.type = "text";
      titleInput.value = note.title;
      titleInput.maxLength = 120;

      const visibility = document.createElement("select");
      visibility.className = "input input--sm";
      for (const [value, label] of [
        ["private", "Only me"], ["gm", "Me and the GM"], ["public", "Everyone"],
      ]) {
        visibility.append(new Option(label, value, false, value === note.visibility));
      }

      const save = document.createElement("button");
      save.className = "btn btn--sm btn--primary";
      save.type = "button";
      save.textContent = "Save";
      save.addEventListener("click", async () => {
        save.disabled = true;
        await this.#save(note.id, {
          title: titleInput.value,
          body: text.value,
          visibility: visibility.value,
        });
        save.disabled = false;
        save.textContent = "Saved";
        setTimeout(() => { save.textContent = "Save"; }, 1200);
        this.loadNotes();
      });

      const remove = document.createElement("button");
      remove.className = "btn btn--sm btn--ghost btn--danger";
      remove.type = "button";
      remove.textContent = "Delete";
      remove.addEventListener("click", async () => {
        if (!confirm(`Delete "${note.title}"?`)) return;
        await fetch(`/api/notes/${note.id}`, { method: "DELETE" });
        this.loadNotes();
      });

      const controls = document.createElement("div");
      controls.className = "row row--wrap";
      controls.style.gap = "4px";
      controls.append(visibility, save, remove);

      body.append(titleInput, text, controls);
    } else {
      // Someone else's note: readable, not editable, even for a GM.
      const text = document.createElement("p");
      text.className = "note-card__read";
      text.textContent = note.body || "(empty)";
      body.append(text);
    }

    card.append(body);
    return card;
  }

  async #save(noteId, changes) {
    try {
      await fetch(`/api/notes/${noteId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      });
    } catch {
      /* the reload below will show what actually stuck */
    }
  }

  async #newNote() {
    try {
      await fetch("/api/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "New note", body: "", visibility: "private" }),
      });
    } catch {
      return;
    }
    this.#noteFilter.value = "all";
    this.loadNotes();
  }

  // ------------------------------------------------------------------ misc --

  #hint(text) {
    const hint = document.createElement("p");
    hint.className = "control__hint";
    hint.textContent = text;
    return hint;
  }
}

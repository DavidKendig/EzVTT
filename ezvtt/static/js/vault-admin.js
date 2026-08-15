/* Admin: choose a vault folder, then tick what players may read.
 *
 * The deny-by-default state has to be *legible*. A GM who points at a vault,
 * sees an empty tree, and assumes it is broken will either give up or share
 * everything to make it work -- so the status line always says plainly how many
 * folders are shared, and says zero loudly.
 */

const q = (id) => document.getElementById(id);

const pathInput = q("vault-path");
const status = q("vault-status");
const foldersBox = q("vault-folders");
const folderList = q("vault-folder-list");
const countLabel = q("vault-count");

async function load() {
  let data;
  try {
    const response = await fetch("/api/vault/folders");
    if (!response.ok) throw new Error();
    data = await response.json();
  } catch {
    status.textContent = "Could not read the vault.";
    return;
  }

  if (!data.root) {
    status.textContent = "No vault folder set. The wiki is off.";
    foldersBox.hidden = true;
    return;
  }

  pathInput.value = data.root;
  renderFolders(data.folders, new Set(data.shared));
  foldersBox.hidden = false;
  describe(data.shared.length, data.folders.length);
}

function describe(shared, total) {
  countLabel.textContent = `${shared} of ${total} shared`;
  status.textContent = shared === 0
    ? "Players can currently see nothing. Tick a folder below to share it."
    : `Players can read ${shared} folder${shared === 1 ? "" : "s"} and anything inside them.`;
  status.style.color = shared === 0 ? "var(--warning)" : "";
}

function renderFolders(folders, shared) {
  folderList.replaceChildren();

  if (!folders.length) {
    const hint = document.createElement("p");
    hint.className = "card__hint";
    hint.textContent = "That vault has no sub-folders.";
    folderList.append(hint);
    return;
  }

  for (const folder of folders) {
    const label = document.createElement("label");
    label.className = "vault-folder";

    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = folder;
    box.checked = shared.has(folder);

    // A parent already covers its children, so ticking both is noise.
    box.addEventListener("change", () => {
      const covered = [...folderList.querySelectorAll("input:checked")]
        .map((input) => input.value);
      for (const other of folderList.querySelectorAll("input")) {
        const isChild = covered.some(
          (parent) => other.value !== parent && other.value.startsWith(parent + "/"));
        other.disabled = isChild;
        other.parentElement.classList.toggle("vault-folder--covered", isChild);
      }
      describe(
        [...folderList.querySelectorAll("input:checked:not(:disabled)")].length,
        folders.length,
      );
    });

    const name = document.createElement("span");
    name.textContent = folder;

    label.append(box, name);
    folderList.append(label);
  }

  // Apply the covered-child state once for whatever is already ticked.
  folderList.querySelector("input")?.dispatchEvent(new Event("change"));
}

async function post(body) {
  const response = await fetch("/api/vault/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    status.textContent = data.error || "That did not work.";
    status.style.color = "var(--danger)";
    return false;
  }
  return true;
}

q("vault-save").addEventListener("click", async () => {
  if (await post({ path: pathInput.value })) load();
});

q("vault-clear").addEventListener("click", async () => {
  if (!confirm("Turn the campaign wiki off? Players will see nothing.")) return;
  if (await post({ path: "" })) {
    pathInput.value = "";
    load();
  }
});

q("vault-share-save").addEventListener("click", async () => {
  const shared = [...folderList.querySelectorAll("input:checked:not(:disabled)")]
    .map((input) => input.value);
  if (await post({ shared })) load();
});

load();

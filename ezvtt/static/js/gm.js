/* GM screen: map library, uploads, and the live grid controls. */

import { AssetPanel } from "./assets-panel.js";
import { Board } from "./board.js";
import { ChatPanel } from "./chat.js";
import { JoinPanel } from "./join.js";
import { TableSocket } from "./ws.js";

const board = new Board(document.getElementById("board-canvas"), { editable: true });
const socket = new TableSocket("gm");
const assetPanel = new AssetPanel(document.getElementById("asset-panel"));
const chatPanel = new ChatPanel(document.getElementById("chat-panel"), socket);
const joinPanel = new JoinPanel(document.getElementById("join-panel"));

const el = (id) => document.getElementById(id);

const ui = {
  status: el("connection-status"),
  statusDot: el("connection-dot"),
  library: el("map-library"),
  empty: el("board-empty"),
  dropzone: el("dropzone"),
  fileInput: el("file-input"),
  uploadBtn: el("upload-btn"),
  gridPanel: el("grid-panel"),
  gridSize: el("grid-size"),
  gridSizeOut: el("grid-size-out"),
  offsetX: el("grid-offset-x"),
  offsetXOut: el("grid-offset-x-out"),
  offsetY: el("grid-offset-y"),
  offsetYOut: el("grid-offset-y-out"),
  opacity: el("grid-opacity"),
  opacityOut: el("grid-opacity-out"),
  colour: el("grid-colour"),
  visible: el("grid-visible"),
  mapName: el("active-map-name"),
  mapMeta: el("active-map-meta"),
  presence: el("presence"),
  toast: el("toast"),
  snap: el("snap-to-grid"),
  toolSelect: el("tool-select"),
  toolFog: el("tool-fog"),
  fogPanel: el("fog-panel"),
  brush: el("fog-brush"),
  brushOut: el("fog-brush-out"),
  playerView: el("player-view"),
  fogStatus: el("fog-status"),
  tokenPanel: el("token-panel"),
  tokenName: el("token-name"),
  tokenW: el("token-w"),
  tokenH: el("token-h"),
  tokenRot: el("token-rot"),
  tokenLabel: el("token-label"),
  tokenLayer: el("token-layer"),
  tokenHidden: el("token-hidden"),
  tokenLocked: el("token-locked"),
  tokenDelete: el("token-delete"),
  tokenCount: el("token-count"),
};

let activeMap = null;
// Set while the GM is dragging a slider. Echoes of our own change come back
// over the socket; applying them to the input would fight the drag.
let draggingControl = false;

// --------------------------------------------------------------- feedback --

let toastTimer = null;
function toast(message, kind = "info") {
  ui.toast.textContent = message;
  ui.toast.className = `toast toast--${kind} toast--visible`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ui.toast.classList.remove("toast--visible"), 4000);
}

// ------------------------------------------------------------------ state --

function applyState(state) {
  activeMap = state.map || null;

  board.setMap(activeMap);
  board.setTokens(state.tokens || []);
  board.setFog(state.fog || null);
  ui.empty.hidden = Boolean(activeMap);
  ui.gridPanel.hidden = !activeMap;
  ui.fogPanel.hidden = !activeMap;
  updateFogStatus();

  if (activeMap) {
    ui.mapName.textContent = activeMap.name;
    ui.mapMeta.textContent = `${activeMap.width_px} x ${activeMap.height_px} px`;
    syncGridInputs(activeMap.grid);
  } else {
    ui.mapName.textContent = "No map on the table";
    ui.mapMeta.textContent = "";
  }

  if (state.library) renderLibrary(state.library, activeMap?.id);
  syncTokenPanel();
}

function syncGridInputs(grid) {
  if (draggingControl) return;
  ui.gridSize.value = grid.size_px;
  ui.gridSizeOut.textContent = `${Number(grid.size_px).toFixed(1)} px`;
  ui.offsetX.value = grid.offset_x;
  ui.offsetXOut.textContent = `${Math.round(grid.offset_x)} px`;
  ui.offsetY.value = grid.offset_y;
  ui.offsetYOut.textContent = `${Math.round(grid.offset_y)} px`;
  ui.opacity.value = grid.opacity;
  ui.opacityOut.textContent = `${Math.round(grid.opacity * 100)}%`;
  ui.colour.value = grid.color;
  ui.visible.checked = grid.visible;
}

function renderLibrary(maps, activeId) {
  ui.library.replaceChildren();

  if (maps.length === 0) {
    const hint = document.createElement("p");
    hint.className = "card__hint";
    hint.textContent = "No maps yet. Drop one above to get started.";
    ui.library.append(hint);
    return;
  }

  for (const map of maps) {
    const item = document.createElement("div");
    item.className = "map-tile" + (map.id === activeId ? " map-tile--active" : "");

    const thumb = document.createElement("img");
    thumb.className = "map-tile__thumb";
    thumb.src = map.thumb_url;
    thumb.alt = "";
    thumb.loading = "lazy";

    const name = document.createElement("span");
    name.className = "map-tile__name";
    // textContent, not innerHTML: map names come from uploaded filenames.
    name.textContent = map.name;

    const actions = document.createElement("div");
    actions.className = "map-tile__actions";

    const renameBtn = document.createElement("button");
    renameBtn.className = "btn btn--ghost btn--sm";
    renameBtn.type = "button";
    renameBtn.title = "Rename";
    renameBtn.textContent = "Rename";
    renameBtn.addEventListener("click", (e) => { e.stopPropagation(); renameMap(map); });

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn btn--ghost btn--sm btn--danger";
    deleteBtn.type = "button";
    deleteBtn.title = "Delete";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", (e) => { e.stopPropagation(); deleteMap(map); });

    actions.append(renameBtn, deleteBtn);
    item.append(thumb, name, actions);
    item.addEventListener("click", () => socket.send("scene.activate", { map_id: map.id }));
    ui.library.append(item);
  }
}

// ----------------------------------------------------------------- upload --

async function uploadFiles(files) {
  const images = [...files].filter((f) => f.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp)$/i.test(f.name));
  if (images.length === 0) {
    toast("That does not look like an image.", "error");
    return;
  }

  for (const file of images) {
    const body = new FormData();
    body.append("file", file);
    toast(`Uploading ${file.name}...`);
    try {
      const response = await fetch("/api/maps", { method: "POST", body });
      const data = await response.json();
      if (!response.ok) {
        toast(data.error || "Upload failed.", "error");
        continue;
      }
      toast(`${data.map.name} is on the table.`, "success");
    } catch {
      toast("Upload failed - is the server still running?", "error");
    }
  }
}

async function renameMap(map) {
  const name = prompt("Rename map", map.name);
  if (name === null || name.trim() === "" || name === map.name) return;

  const response = await fetch(`/api/maps/${map.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    toast(data.error || "Rename failed.", "error");
  }
}

async function deleteMap(map) {
  if (!confirm(`Delete "${map.name}"? This cannot be undone.`)) return;

  const response = await fetch(`/api/maps/${map.id}`, { method: "DELETE" });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    toast(data.error || "Delete failed.", "error");
  } else {
    toast(`${map.name} deleted.`);
  }
}

// ------------------------------------------------------------ grid controls --

/* Every change is sent immediately rather than on release. The requirement is
 * that the display window tracks the slider live, and that closing EzVTT
 * mid-adjustment and reopening restores exactly what was on screen. */
function pushGrid(changes) {
  if (!activeMap) return;
  board.setGrid(changes);                       // optimistic: no input lag
  socket.send("grid.set", { map_id: activeMap.id, ...changes });
}

function bindSlider(input, output, key, format) {
  const update = () => {
    const value = Number(input.value);
    output.textContent = format(value);
    pushGrid({ [key]: value });
  };
  input.addEventListener("pointerdown", () => { draggingControl = true; });
  input.addEventListener("input", update);
  const release = () => { draggingControl = false; };
  input.addEventListener("pointerup", release);
  input.addEventListener("pointercancel", release);
  input.addEventListener("blur", release);
  input.addEventListener("change", update);
}

bindSlider(ui.gridSize, ui.gridSizeOut, "size_px", (v) => `${v.toFixed(1)} px`);
bindSlider(ui.offsetX, ui.offsetXOut, "offset_x", (v) => `${Math.round(v)} px`);
bindSlider(ui.offsetY, ui.offsetYOut, "offset_y", (v) => `${Math.round(v)} px`);
bindSlider(ui.opacity, ui.opacityOut, "opacity", (v) => `${Math.round(v * 100)}%`);

ui.colour.addEventListener("input", () => pushGrid({ color: ui.colour.value }));
ui.visible.addEventListener("change", () => pushGrid({ visible: ui.visible.checked }));

el("grid-fit").addEventListener("click", () => board.fitToView());
el("clear-table").addEventListener("click", () => {
  if (confirm("Take the map off the table? It stays in your library.")) {
    socket.send("table.clear");
  }
});

document.addEventListener("keydown", (event) => {
  if (!activeMap || event.target.matches("input, textarea, select")) return;

  const selected = board.selected;

  // Delete removes the selected token.
  if ((event.key === "Delete" || event.key === "Backspace") && selected) {
    event.preventDefault();
    socket.send("token.delete", { token_id: selected.id });
    return;
  }

  // Escape clears the selection.
  if (event.key === "Escape" && selected) {
    event.preventDefault();
    board.select(null);
    syncTokenPanel();
    return;
  }

  // [ and ] rotate the selected token in 15 degree steps.
  if (selected && (event.key === "[" || event.key === "]")) {
    event.preventDefault();
    const delta = event.key === "]" ? 15 : -15;
    updateSelected({ rotation: (selected.rotation + delta + 360) % 360 });
    return;
  }

  const arrows = {
    ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
  };
  const arrow = arrows[event.key];
  if (!arrow) return;
  event.preventDefault();

  if (selected) {
    // With a token selected the arrows nudge it by whole squares -- Shift for
    // a tenth, for fine placement of scatter.
    const step = event.shiftKey ? 0.1 : 1;
    updateSelected({
      x: round2(selected.x + arrow[0] * step),
      y: round2(selected.y + arrow[1] * step),
    });
  } else {
    // With nothing selected they nudge the grid offset, which is fiddly with a
    // slider once you are within a pixel or two.
    const step = event.shiftKey ? 10 : 1;
    const key = arrow[0] !== 0 ? "offset_x" : "offset_y";
    const delta = arrow[0] !== 0 ? arrow[0] : arrow[1];
    pushGrid({ [key]: activeMap.grid[key] + delta * step });
  }
});

function round2(value) {
  return Math.round(value * 100) / 100;
}

// ------------------------------------------------------------------ upload UI --

ui.uploadBtn.addEventListener("click", () => ui.fileInput.click());
ui.fileInput.addEventListener("change", () => {
  uploadFiles(ui.fileInput.files);
  ui.fileInput.value = "";
});

for (const event of ["dragenter", "dragover"]) {
  ui.dropzone.addEventListener(event, (e) => {
    e.preventDefault();
    ui.dropzone.classList.add("dropzone--over");
  });
}
for (const event of ["dragleave", "drop"]) {
  ui.dropzone.addEventListener(event, (e) => {
    e.preventDefault();
    ui.dropzone.classList.remove("dropzone--over");
  });
}
ui.dropzone.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));

// Dropping anywhere on the board works too -- hunting for the drop target is
// exactly the kind of friction this program exists to remove.
const boardArea = document.getElementById("board-area");
boardArea.addEventListener("dragover", (e) => e.preventDefault());
boardArea.addEventListener("drop", (e) => {
  e.preventDefault();
  uploadFiles(e.dataTransfer.files);
});

// --------------------------------------------------------------- display --

document.getElementById("open-display").addEventListener("click", () => {
  window.open("/display", "ezvtt-display",
    "width=1280,height=800,menubar=no,toolbar=no,location=no,status=no");
});

// ----------------------------------------------------------------- tokens --

function syncTokenPanel() {
  const token = board.selected;
  ui.tokenPanel.hidden = !token;
  ui.tokenCount.textContent = board.tokens.length
    ? `${board.tokens.length} on the board`
    : "";
  if (!token) return;

  ui.tokenName.textContent = token.name || "Token";
  ui.tokenW.value = token.grid_w;
  ui.tokenH.value = token.grid_h;
  ui.tokenRot.value = token.rotation;
  ui.tokenLabel.value = token.label || "";
  ui.tokenLayer.value = token.layer;
  ui.tokenHidden.checked = token.hidden;
  ui.tokenLocked.checked = token.locked;
}

function updateSelected(changes) {
  const token = board.selected;
  if (!token) return;
  socket.send("token.update", { token_id: token.id, ...changes });
}

board.element.addEventListener("board:select", syncTokenPanel);

// Sent continuously through the drag so the display window tracks the token in
// real time, rather than jumping when the GM lets go.
board.element.addEventListener("board:tokenmove", (e) => {
  socket.send("token.update", { token_id: e.detail.id, x: e.detail.x, y: e.detail.y });
});

// Dropping an asset tile onto the board.
const boardEl = document.getElementById("board-area");
boardEl.addEventListener("dragover", (e) => {
  if (e.dataTransfer.types.includes("application/x-ezvtt-asset")) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }
});
boardEl.addEventListener("drop", (e) => {
  const raw = e.dataTransfer.getData("application/x-ezvtt-asset");
  if (!raw) return;                       // a file drop; handled as an upload
  e.preventDefault();

  const canvas = document.getElementById("board-canvas");
  const rect = canvas.getBoundingClientRect();
  const grid = board.toGrid(e.clientX - rect.left, e.clientY - rect.top);
  if (!grid) {
    toast("Put a map on the table first.", "error");
    return;
  }
  placeAsset(Number(raw), grid.x, grid.y);
});

function placeAsset(assetId, x, y) {
  if (!activeMap) {
    toast("Put a map on the table first.", "error");
    return;
  }
  // Centre the drop point on the token rather than its top-left corner, which
  // is where the GM's cursor actually is.
  const snapped = board.snap ? Math.round(x) : x;
  const snappedY = board.snap ? Math.round(y) : y;
  socket.send("token.place", { asset_id: assetId, x: snapped, y: snappedY });
}

assetPanel.addEventListener("place", (e) => {
  // Clicking a tile drops it in the middle of the current view.
  const canvas = document.getElementById("board-canvas");
  const grid = board.toGrid(canvas.clientWidth / 2, canvas.clientHeight / 2);
  if (!grid) {
    toast("Put a map on the table first.", "error");
    return;
  }
  placeAsset(e.detail.asset.id, grid.x, grid.y);
});

ui.snap.addEventListener("change", () => { board.snap = ui.snap.checked; });

for (const [input, key] of [[ui.tokenW, "grid_w"], [ui.tokenH, "grid_h"], [ui.tokenRot, "rotation"]]) {
  input.addEventListener("change", () => updateSelected({ [key]: Number(input.value) }));
}
ui.tokenLabel.addEventListener("change", () => updateSelected({ label: ui.tokenLabel.value }));
ui.tokenLayer.addEventListener("change", () => updateSelected({ layer: ui.tokenLayer.value }));
ui.tokenHidden.addEventListener("change", () => updateSelected({ hidden: ui.tokenHidden.checked }));
ui.tokenLocked.addEventListener("change", () => updateSelected({ locked: ui.tokenLocked.checked }));

ui.tokenDelete.addEventListener("click", () => {
  const token = board.selected;
  if (token) socket.send("token.delete", { token_id: token.id });
});

el("clear-tokens").addEventListener("click", () => {
  if (board.tokens.length && confirm(`Remove all ${board.tokens.length} tokens from this scene?`)) {
    socket.send("tokens.clear");
  }
});

// -------------------------------------------------------------------- fog --

function setTool(name) {
  board.tool = name;
  ui.toolSelect.classList.toggle("btn--primary", name === "select");
  ui.toolFog.classList.toggle("btn--primary", name === "fog");
  if (name === "fog") syncTokenPanel();
}

function updateFogStatus() {
  const fog = board.fog;
  if (!fog) { ui.fogStatus.textContent = ""; return; }
  const revealed = fog.cells.reduce((n, c) => n + (c ? 1 : 0), 0);
  const percent = Math.round((revealed / fog.cells.length) * 100);
  ui.fogStatus.textContent = `${percent}% revealed`;
}

ui.toolSelect.addEventListener("click", () => setTool("select"));
ui.toolFog.addEventListener("click", () => setTool("fog"));

ui.brush.addEventListener("input", () => {
  board.brushRadius = Number(ui.brush.value);
  ui.brushOut.textContent = `${ui.brush.value} sq`;
});

board.element.addEventListener("board:fogpaint", (e) => {
  socket.send("fog.paint", e.detail);
  updateFogStatus();
});

el("fog-reveal-all").addEventListener("click", () => socket.send("fog.all", { revealed: true }));
el("fog-hide-all").addEventListener("click", () => {
  if (confirm("Conceal the whole map again?")) socket.send("fog.all", { revealed: false });
});

/* "See what players see" paints fog solid instead of translucent. The GM is
 * still looking at the real image -- this is a preview, not a security
 * boundary. The players' own copy has the concealed pixels removed on the
 * server before it is ever sent. */
ui.playerView.addEventListener("change", () => {
  board.fogOpacity = ui.playerView.checked ? 1 : 0.55;
});

// ----------------------------------------------------------------- socket --

socket.addEventListener("state", (e) => applyState(e.detail.state));

socket.addEventListener("fog", (e) => {
  board.setFog({ cols: e.detail.cols, rows: e.detail.rows, cells: e.detail.cells });
  updateFogStatus();
});

socket.addEventListener("token.added", (e) => {
  board.upsertToken(e.detail.token);
  // Select what was just dropped, so its size, label, and layer controls are
  // right there. Otherwise the GM has to find and click the thing they can
  // already see under the cursor.
  board.select(e.detail.token.id);
  syncTokenPanel();
});

socket.addEventListener("token.changed", (e) => {
  const token = e.detail.token;
  // Ignore echoes of the token currently under the cursor: applying them would
  // fight the drag and make it stutter.
  const dragging = board.selected?.id === token.id;
  board.upsertToken(token);
  if (!dragging) syncTokenPanel();
});

socket.addEventListener("token.removed", (e) => {
  board.removeToken(e.detail.token_id);
  syncTokenPanel();
});

socket.addEventListener("grid", (e) => {
  const { map_id, grid } = e.detail;
  if (!activeMap || activeMap.id !== map_id) return;
  activeMap = { ...activeMap, grid };
  board.setGrid(grid);
  syncGridInputs(grid);
});

socket.addEventListener("presence", (e) => {
  const { gms, players, displays } = e.detail;
  const parts = [];
  if (players) parts.push(`${players} player${players === 1 ? "" : "s"}`);
  if (displays) parts.push(`${displays} display${displays === 1 ? "" : "s"}`);
  ui.presence.textContent = parts.join(" · ") || "no one else connected";
});

socket.addEventListener("error", (e) => toast(e.detail.message, "error"));

socket.addEventListener("status", (e) => {
  const { connected } = e.detail;
  ui.statusDot.className = `status-dot ${connected ? "status-dot--live" : "status-dot--lost"}`;
  ui.status.textContent = connected ? "Live" : "Reconnecting...";
});

socket.connect();
assetPanel.init();
joinPanel.load();

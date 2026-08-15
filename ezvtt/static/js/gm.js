/* GM screen: map library, uploads, and the live grid controls. */

import { AssetPanel } from "./assets-panel.js";
import { Board } from "./board.js";
import { ChatPanel } from "./chat.js";
import { Codex } from "./codex.js";
import { InitiativePanel } from "./initiative.js";
import { JoinPanel } from "./join.js";
import { TableSocket } from "./ws.js";

const currentUserId = Number(document.body.dataset.userId) || null;

const board = new Board(document.getElementById("board-canvas"), { editable: true });
const socket = new TableSocket("gm");
const assetPanel = new AssetPanel(document.getElementById("asset-panel"));
const chatPanel = new ChatPanel(document.getElementById("chat-panel"), socket);
const joinPanel = new JoinPanel(document.getElementById("join-panel"));
const codex = new Codex(document.getElementById("codex-panel"), { me: currentUserId });
const initiativePanel = new InitiativePanel(
  document.getElementById("initiative-panel"), socket,
);

const el = (id) => document.getElementById(id);

const ui = {
  status: el("connection-status"),
  statusDot: el("connection-dot"),
  library: el("map-library"),
  sceneList: el("scene-list"),
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
  templatePanel: el("template-panel"),
  toolCircle: el("tool-circle"),
  toolCone: el("tool-cone"),
  toolLine: el("tool-line"),
  templateWidth: el("template-width"),
  templateWidthOut: el("template-width-out"),
  templateColour: el("template-colour"),
  templateDelete: el("template-delete"),
  templateHide: el("template-hide"),
};

let activeMap = null;
let activeScene = null;
let scenes = [];
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
  activeScene = state.scene || null;

  board.setMap(activeMap);
  board.setTokens(state.tokens || []);
  board.setTemplates(state.templates || []);
  board.setFog(state.fog || null);
  ui.empty.hidden = Boolean(activeMap);
  ui.gridPanel.hidden = !activeMap;
  ui.fogPanel.hidden = !activeMap;
  ui.templatePanel.hidden = !activeMap;
  syncTemplatePanel();
  updateFogStatus();

  if (activeMap) {
    // The scene is what the GM named, so it is what the bar says. The map is
    // named alongside it only when the two differ, which is when there are
    // several scenes over one piece of artwork.
    const sceneName = activeScene?.name;
    ui.mapName.textContent = sceneName || activeMap.name;
    const size = `${activeMap.width_px} x ${activeMap.height_px} px`;
    ui.mapMeta.textContent = sceneName && sceneName !== activeMap.name
      ? `${activeMap.name} · ${size}`
      : size;
    syncGridInputs(activeMap.grid);
  } else {
    ui.mapName.textContent = "No map on the table";
    ui.mapMeta.textContent = "";
  }

  if (state.library) renderLibrary(state.library, activeMap?.id);
  if (state.scenes) renderScenes(state.scenes);
  if (state.initiative) initiativePanel.apply(state.initiative);
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

// ----------------------------------------------------------------- scenes --

function renderScenes(list) {
  scenes = list;
  ui.sceneList.replaceChildren();

  if (list.length === 0) {
    const hint = document.createElement("p");
    hint.className = "card__hint";
    hint.textContent = "Scenes appear here as soon as you add a map.";
    ui.sceneList.append(hint);
    return;
  }

  for (const scene of list) {
    const item = document.createElement("div");
    item.className =
      "map-tile map-tile--scene" + (scene.active ? " map-tile--active" : "");

    let thumb;
    if (scene.thumb_url) {
      thumb = document.createElement("img");
      thumb.src = scene.thumb_url;
      thumb.alt = "";
      thumb.loading = "lazy";
    } else {
      thumb = document.createElement("div");
    }
    thumb.className = "map-tile__thumb";

    const name = document.createElement("span");
    name.className = "map-tile__name";
    // textContent throughout: scene names are typed by the GM.
    name.textContent = scene.name;

    const meta = document.createElement("span");
    meta.className = "map-tile__meta";
    const count = scene.token_count === 1 ? "1 token" : `${scene.token_count} tokens`;
    meta.textContent = `${scene.map_name || "No map"} · ${count}`;

    const actions = document.createElement("div");
    actions.className = "map-tile__actions";
    actions.append(
      sceneButton("Rename", () => renameScene(scene)),
      sceneButton("Copy", () => duplicateScene(scene)),
      sceneButton("Delete", () => deleteScene(scene), "btn--danger"),
    );

    item.append(thumb, name, meta, actions);
    item.addEventListener("click", () => {
      if (!scene.active) socket.send("scene.activate", { scene_id: scene.id });
    });
    ui.sceneList.append(item);
  }
}

function sceneButton(label, onClick, extra = "") {
  const button = document.createElement("button");
  button.className = `btn btn--ghost btn--sm ${extra}`.trim();
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", (event) => {
    event.stopPropagation();          // the tile itself switches scenes
    onClick();
  });
  return button;
}

async function sceneRequest(url, options, failure) {
  const response = await fetch(url, options);
  if (response.ok) return response.json();

  const data = await response.json().catch(() => ({}));
  toast(data.error || failure, "error");
  return null;
}

/* A new scene is deliberately NOT activated. Adding one mid-session is prep;
 * putting it in front of the table is the click on the scene itself. */
async function newScene() {
  if (!activeMap) {
    toast("Put a map on the table first.", "error");
    return;
  }

  const existing = scenes.filter((s) => s.map_id === activeMap.id).length;
  const name = prompt("Name the new scene", `${activeMap.name} ${existing + 1}`);
  if (name === null || name.trim() === "") return;

  const created = await sceneRequest("/api/scenes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ map_id: activeMap.id, name }),
  }, "Could not create that scene.");

  if (created) toast(`${created.scene.name} is ready. Click it to switch.`, "success");
}

async function duplicateScene(scene) {
  const copy = await sceneRequest(
    `/api/scenes/${scene.id}/duplicate`, { method: "POST" },
    "Could not copy that scene.",
  );
  if (copy) toast(`${copy.scene.name} created with the same layout.`, "success");
}

async function renameScene(scene) {
  const name = prompt("Rename scene", scene.name);
  if (name === null || name.trim() === "" || name === scene.name) return;

  await sceneRequest(`/api/scenes/${scene.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  }, "Rename failed.");
}

async function deleteScene(scene) {
  const warning = scene.active
    ? `Delete "${scene.name}"? It is on the table now, and the board will go empty.`
    : `Delete "${scene.name}"? Its tokens and fog go with it. The map stays.`;
  if (!confirm(warning)) return;

  const gone = await sceneRequest(
    `/api/scenes/${scene.id}`, { method: "DELETE" }, "Delete failed.",
  );
  if (gone) toast(`${scene.name} deleted.`);
}

el("scene-new").addEventListener("click", newScene);

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
      toast(
        data.detected
          ? `${data.map.name} is on the table, grid and all.`
          : `${data.map.name} is on the table — drag the slider to match its squares.`,
        "success",
      );
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

/* "Not detected" is an ordinary answer, not a failure: plenty of good
 * battlemaps have no grid drawn on them at all. */
el("grid-detect").addEventListener("click", async () => {
  if (!activeMap) return;
  toast("Reading the grid off the artwork...");

  const response = await fetch(`/api/maps/${activeMap.id}/detect-grid`, { method: "POST" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    toast(data.error || "Could not read that map.", "error");
    return;
  }
  if (!data.detected) {
    toast("No grid drawn on this map — set the size by hand.", "error");
    return;
  }
  toast(`Grid found: ${data.map.grid.size_px} px a square.`, "success");
});
el("clear-table").addEventListener("click", () => {
  if (confirm("Take the map off the table? It stays in your library.")) {
    socket.send("table.clear");
  }
});

document.addEventListener("keydown", (event) => {
  if (!activeMap || event.target.matches("input, textarea, select")) return;

  const selected = board.selected;
  const template = board.selectedTemplate;

  if ((event.key === "Delete" || event.key === "Backspace") && !selected && template) {
    event.preventDefault();
    socket.send("template.remove", { template_id: template.id });
    return;
  }

  // Escape puts down whatever is in hand: the measurement, then the selection.
  if (event.key === "Escape" && (template || !selected)) {
    board.clearRuler();
    if (template) {
      event.preventDefault();
      board.selectTemplate(null);
      return;
    }
  }

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

// -------------------------------------------------------------- templates --

function syncTemplatePanel() {
  const template = board.selectedTemplate;
  ui.templateDelete.hidden = !template;
  ui.templateHide.hidden = !template;
  if (template) {
    // A glyph of warding can be drawn where it lies and then taken out of the
    // players' view until someone steps on it.
    ui.templateHide.textContent = template.hidden ? "Reveal" : "Hide";
    ui.templateHide.title = template.hidden
      ? "Players cannot see this template; show it to them"
      : "Keep this template on your screen only";
  }
}

board.element.addEventListener("board:templateselect", syncTemplatePanel);

/* Sent once, on release. Every frame of the drag was a local preview of a
 * decision the GM had not made yet -- see ADR-014. */
board.element.addEventListener("board:templateplace", (e) => {
  socket.send("template.place", { ...e.detail, color: ui.templateColour.value });
});

ui.toolCircle.addEventListener("click", () => setTool("circle"));
ui.toolCone.addEventListener("click", () => setTool("cone"));
ui.toolLine.addEventListener("click", () => setTool("line"));

ui.templateWidth.addEventListener("input", () => {
  board.templateWidth = Number(ui.templateWidth.value);
  ui.templateWidthOut.textContent = `${ui.templateWidth.value} sq`;
});

ui.templateColour.addEventListener("input", () => {
  board.templateColor = ui.templateColour.value;
});

ui.templateHide.addEventListener("click", () => {
  const template = board.selectedTemplate;
  if (template) {
    socket.send("template.update", {
      template_id: template.id, hidden: !template.hidden,
    });
  }
});

ui.templateDelete.addEventListener("click", () => {
  const template = board.selectedTemplate;
  if (template) socket.send("template.remove", { template_id: template.id });
});

el("templates-clear").addEventListener("click", () => {
  if (board.templates.length === 0) return;
  if (confirm(`Remove all ${board.templates.length} templates from this scene?`)) {
    socket.send("templates.clear");
  }
});

// -------------------------------------------------------------------- fog --

function setTool(name) {
  board.tool = name;
  for (const [button, tool] of [
    [ui.toolSelect, "select"], [ui.toolFog, "fog"],
    [ui.toolCircle, "circle"], [ui.toolCone, "cone"], [ui.toolLine, "line"],
  ]) {
    button.classList.toggle("btn--primary", name === tool);
  }
  syncTokenPanel();
  syncTemplatePanel();
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

// ------------------------------------------------------------ initiative --

/* "Add tokens" means every creature on the board. Objects and scenery are on
 * their own layers and have no turn; the server skips anything already in the
 * order, so clicking it twice after dropping two more goblins is safe. */
initiativePanel.addEventListener("add-tokens", () => {
  const creatures = board.tokens.filter((t) => t.layer === "token").map((t) => t.id);
  if (creatures.length === 0) {
    toast("Put some creatures on the board first.", "error");
    return;
  }
  socket.send("initiative.add", { token_ids: creatures });
});

initiativePanel.addEventListener("change", () => {
  board.setCurrentToken(initiativePanel.currentTokenId);
});

// ----------------------------------------------------------------- socket --

socket.addEventListener("state", (e) => applyState(e.detail.state));

socket.addEventListener("initiative", (e) => initiativePanel.apply(e.detail.initiative));

socket.addEventListener("templates", (e) => {
  board.setTemplates(e.detail.templates);
  syncTemplatePanel();
});

// Pings are drawn from the server's copy, including our own -- so what the GM
// sees is what the table saw, not an optimistic mark nobody else got.
socket.addEventListener("ping", (e) => board.ping(e.detail.x, e.detail.y, e.detail.by));

board.element.addEventListener("board:ping", (e) => socket.send("board.ping", e.detail));

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

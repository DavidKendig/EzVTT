/* Player view.
 *
 * Pan and zoom are allowed here -- a player on a phone needs to get closer to
 * their corner of the map. What they cannot do is change anything: this file
 * sends no intents, and the server would refuse them anyway.
 */

import { Board } from "./board.js";
import { ChatPanel } from "./chat.js";
import { Codex } from "./codex.js";
import { InitiativePanel } from "./initiative.js";
import { HandoutOverlay } from "./handouts.js";
import { TableSocket } from "./ws.js";

const board = new Board(document.getElementById("board-canvas"));
const socket = new TableSocket("play");
const chatPanel = new ChatPanel(document.getElementById("chat-panel"), socket);
const codex = new Codex(document.getElementById("codex-panel"), {
  me: Number(document.body.dataset.userId) || null,
});
const initiativePanel = new InitiativePanel(
  document.getElementById("initiative-panel"), socket,
);

// Read-only, and only while a fight is running: the panel hides itself. A
// concealed entry never arrives here at all, so there is nothing to filter.
initiativePanel.addEventListener("change", () => {
  board.setCurrentToken(initiativePanel.currentTokenId);
});

socket.addEventListener("initiative", (e) => initiativePanel.apply(e.detail.initiative));

const empty = document.getElementById("board-empty");
const name = document.getElementById("active-map-name");
const dot = document.getElementById("connection-dot");
const status = document.getElementById("connection-status");

socket.addEventListener("state", (event) => {
  const map = event.detail.state.map || null;
  board.setMap(map);
  // Hidden tokens are absent from this payload, not flagged -- the server never
  // sends a player something they are not meant to know exists. See ADR-004.
  board.setTokens(event.detail.state.tokens || []);
  board.setTemplates(event.detail.state.templates || []);
  board.setConditions(event.detail.state.conditions);
  empty.hidden = Boolean(map);
  name.textContent = map ? map.name : "Waiting for the GM";
  initiativePanel.apply(event.detail.state.initiative);
});

socket.addEventListener("grid", (event) => board.setGrid(event.detail.grid));

// Templates the GM has dropped. Concealed ones, and ones drawn over map this
// player has not revealed, never arrive here at all.
socket.addEventListener("templates", (e) => board.setTemplates(e.detail.templates));

/* Alt-click points at a spot for the whole table. This is the one thing a
 * player may put on everyone else's screen -- it changes nothing, and "no, the
 * *other* door" is said by players as often as by the GM. */
board.element.addEventListener("board:ping", (e) => socket.send("board.ping", e.detail));
socket.addEventListener("ping", (e) => board.ping(e.detail.x, e.detail.y, e.detail.by));

// Shift-drag measures, on this screen only. Escape puts the ruler away.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") board.clearRuler();
});

socket.addEventListener("token.added", (e) => board.upsertToken(e.detail.token));
socket.addEventListener("token.changed", (e) => board.upsertToken(e.detail.token));
socket.addEventListener("token.removed", (e) => board.removeToken(e.detail.token_id));

socket.addEventListener("status", (event) => {
  const { connected } = event.detail;
  dot.className = `status-dot ${connected ? "status-dot--live" : "status-dot--lost"}`;
  status.textContent = connected ? "Live" : "Reconnecting...";
});

socket.connect();

/* What the GM is holding up. Closing it here closes this copy only; the next
 * thing they push brings the overlay back. */
const handoutOverlay = new HandoutOverlay(
  document.getElementById("handout-overlay"), socket,
);
socket.addEventListener("handout", (e) => handoutOverlay.apply(e.detail.handout));
socket.addEventListener("state", (e) => handoutOverlay.apply(e.detail.state.handout));

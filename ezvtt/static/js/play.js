/* Player view.
 *
 * Pan and zoom are allowed here -- a player on a phone needs to get closer to
 * their corner of the map. What they cannot do is change anything: this file
 * sends no intents, and the server would refuse them anyway.
 */

import { Board } from "./board.js";
import { ChatPanel } from "./chat.js";
import { Codex } from "./codex.js";
import { TableSocket } from "./ws.js";

const board = new Board(document.getElementById("board-canvas"));
const socket = new TableSocket("play");
const chatPanel = new ChatPanel(document.getElementById("chat-panel"), socket);
const codex = new Codex(document.getElementById("codex-panel"), {
  me: Number(document.body.dataset.userId) || null,
});

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
  empty.hidden = Boolean(map);
  name.textContent = map ? map.name : "Waiting for the GM";
});

socket.addEventListener("grid", (event) => board.setGrid(event.detail.grid));

socket.addEventListener("token.added", (e) => board.upsertToken(e.detail.token));
socket.addEventListener("token.changed", (e) => board.upsertToken(e.detail.token));
socket.addEventListener("token.removed", (e) => board.removeToken(e.detail.token_id));

socket.addEventListener("status", (event) => {
  const { connected } = event.detail;
  dot.className = `status-dot ${connected ? "status-dot--live" : "status-dot--lost"}`;
  status.textContent = connected ? "Live" : "Reconnecting...";
});

socket.connect();

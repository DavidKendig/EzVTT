/* The second-monitor view.
 *
 * Follows the table and does nothing else. No panning, no zooming, no controls:
 * the whole group is looking at this screen and a stray scroll would move the
 * map out from under them mid-sentence.
 */

import { Board } from "./board.js";
import { TableSocket } from "./ws.js";

const canvas = document.getElementById("board-canvas");
const idle = document.getElementById("display-idle");
const offline = document.getElementById("display-offline");

/* Fog is drawn fully opaque here. This window is GM-authenticated, so it holds
 * the real full-resolution map -- which is what should be projected -- but the
 * table must not be able to see through the fog on it. */
const board = new Board(canvas, { interactive: false, fogOpacity: 1 });
const socket = new TableSocket("display");

/* This window authenticates as the GM, so the server sends it hidden tokens --
 * correctly, since the GM screen ghosts them. The projector must not. Anything
 * marked hidden is dropped here rather than drawn faintly. */
const visible = (tokens) => (tokens || []).filter((t) => !t.hidden);

socket.addEventListener("state", (event) => {
  const map = event.detail.state.map || null;
  board.setMap(map);
  board.setTokens(visible(event.detail.state.tokens));
  board.setFog(event.detail.state.fog || null);
  idle.hidden = Boolean(map);
  canvas.hidden = !map;
});

socket.addEventListener("fog", (event) => {
  board.setFog({ cols: event.detail.cols, rows: event.detail.rows,
                 cells: event.detail.cells });
});

socket.addEventListener("grid", (event) => board.setGrid(event.detail.grid));

const applyToken = (token) => {
  if (token.hidden) board.removeToken(token.id);
  else board.upsertToken(token);
};

socket.addEventListener("token.added", (e) => applyToken(e.detail.token));
socket.addEventListener("token.changed", (e) => applyToken(e.detail.token));
socket.addEventListener("token.removed", (e) => board.removeToken(e.detail.token_id));

socket.addEventListener("status", (event) => {
  // Shown rather than hidden: a frozen map that silently stopped updating is
  // worse than an obvious "reconnecting" marker, because nobody at the table
  // knows the difference until something moves.
  offline.hidden = event.detail.connected;
});

// The window can be dragged between monitors of different pixel densities;
// refit so the map still fills the projector rather than a corner of it.
window.addEventListener("resize", () => board.fitToView());

socket.connect();

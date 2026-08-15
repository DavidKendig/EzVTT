/* The board: map image, grid overlay, pan and zoom.
 *
 * Two coordinate spaces, and keeping them straight is most of this file:
 *
 *   world  -- pixels in the source map image. Everything persisted (grid size,
 *             offsets, and later token positions) is in world space, so it stays
 *             correct no matter how anyone has zoomed.
 *   screen -- pixels in the canvas. Derived; never stored.
 *
 *   screen = world * scale + origin
 *   world  = (screen - origin) / scale
 */

const MIN_SCALE = 0.02;
const MAX_SCALE = 12;
const ZOOM_RATE = 0.0015;

const LAYER_ORDER = { map: 0, object: 1, token: 2 };

/** Paint order: layer first, then z, then id so it is fully deterministic. */
function compareTokens(a, b) {
  return (LAYER_ORDER[a.layer] ?? 1) - (LAYER_ORDER[b.layer] ?? 1)
      || a.z - b.z
      || a.id - b.id;
}

export class Board {
  #canvas;
  #ctx;
  #image = null;
  #imageUrl = null;

  #scale = 1;
  #originX = 0;
  #originY = 0;

  #map = null;
  #dirty = true;
  #frame = null;

  #tokens = [];
  #selectedId = null;
  // The token whose turn it is, ringed on the board. "No, the *other* goblin"
  // is the thing this removes.
  #currentTokenId = null;
  // Asset images, keyed by URL. A scene commonly repeats the same barrel a
  // dozen times; decoding it once and reusing it is the difference between a
  // board that opens instantly and one that hitches.
  #imageCache = new Map();

  #editable = false;
  #snap = true;

  // Fog is a mask of revealed cells plus how solidly to paint the rest.
  //   GM screen      0.55 -- see through what you are concealing
  //   display window 1.00 -- the table must not see through it
  //   player view    n/a  -- their map arrives already composited
  #fog = null;
  #fogOpacity = 0.55;
  #fogCanvas = null;

  // "select" moves tokens and pans; "fog" paints. One at a time, because a
  // brush that also drags furniture is a brush nobody trusts.
  #tool = "select";
  #brushRadius = 2;
  #fogRevealing = true;
  #cursor = null;

  constructor(canvas, { interactive = true, editable = false, fogOpacity = 0.55 } = {}) {
    this.#canvas = canvas;
    this.#ctx = canvas.getContext("2d", { alpha: false });
    this.#editable = editable;
    this.#fogOpacity = fogOpacity;

    this.#observeSize();
    if (interactive) this.#bindPointer();

    this.#loop();
  }

  // ------------------------------------------------------------------ fog --

  setFog(fog) {
    this.#fog = fog && fog.cols ? fog : null;
    this.#fogCanvas = null;          // rebuilt lazily on the next draw
    this.invalidate();
  }

  get fog() {
    return this.#fog;
  }

  set fogOpacity(value) {
    this.#fogOpacity = Math.max(0, Math.min(1, value));
    this.invalidate();
  }

  get fogOpacity() {
    return this.#fogOpacity;
  }

  set tool(name) {
    this.#tool = name === "fog" ? "fog" : "select";
    if (this.#tool === "fog") this.select(null);
    this.#cursor = null;
    this.#canvas.style.cursor = this.#tool === "fog" ? "none" : "grab";
    this.invalidate();
  }

  get tool() {
    return this.#tool;
  }

  set brushRadius(value) {
    this.#brushRadius = Math.max(0.5, Math.min(60, Number(value) || 2));
    this.invalidate();
  }

  get brushRadius() {
    return this.#brushRadius;
  }

  #emitFog(x, y) {
    // Painted locally first so the stroke keeps up with the cursor; the server
    // is authoritative and its reply overwrites this.
    this.paintFogLocal(x, y, this.#brushRadius, this.#fogRevealing);
    this.#canvas.dispatchEvent(new CustomEvent("board:fogpaint", {
      detail: { x, y, radius: this.#brushRadius, revealed: this.#fogRevealing },
    }));
  }

  /** Locally mark cells revealed or hidden, for instant brush feedback. */
  paintFogLocal(x, y, radius, revealed) {
    if (!this.#fog) return false;
    const { cols, rows, cells } = this.#fog;

    let changed = false;
    const left = Math.max(0, Math.floor(x - radius));
    const right = Math.min(cols - 1, Math.ceil(x + radius));
    const top = Math.max(0, Math.floor(y - radius));
    const bottom = Math.min(rows - 1, Math.ceil(y + radius));

    for (let cy = top; cy <= bottom; cy++) {
      for (let cx = left; cx <= right; cx++) {
        const dx = cx + 0.5 - x;
        const dy = cy + 0.5 - y;
        if (dx * dx + dy * dy <= radius * radius) {
          const i = cy * cols + cx;
          if (cells[i] !== revealed) { cells[i] = revealed; changed = true; }
        }
      }
    }

    if (changed) { this.#fogCanvas = null; this.invalidate(); }
    return changed;
  }

  /** The canvas element, for listening to the board:* events. */
  get element() {
    return this.#canvas;
  }

  get snap() {
    return this.#snap;
  }

  set snap(value) {
    this.#snap = Boolean(value);
  }

  // --------------------------------------------------------------- state --

  /** Swap the displayed map. Re-fits the view only when the image changes. */
  setMap(map) {
    const previousUrl = this.#imageUrl;
    this.#map = map;

    if (!map) {
      this.#image = null;
      this.#imageUrl = null;
      this.invalidate();
      return;
    }

    if (map.url !== previousUrl) {
      this.#imageUrl = map.url;
      const image = new Image();
      // Decoding a large battlemap on the main thread stalls the first paint;
      // async decode lets the idle screen stay responsive until it is ready.
      image.decoding = "async";
      image.addEventListener("load", () => {
        if (this.#imageUrl !== map.url) return;   // a newer map won the race
        this.#image = image;
        this.fitToView();
        this.invalidate();
        this.#canvas.dispatchEvent(new CustomEvent("board:loaded", { detail: map }));
      });
      image.addEventListener("error", () => {
        if (this.#imageUrl !== map.url) return;
        this.#image = null;
        this.invalidate();
        this.#canvas.dispatchEvent(new CustomEvent("board:error", { detail: map }));
      });
      image.src = map.url;
    }

    this.invalidate();
  }

  /** Apply grid changes without disturbing the view. */
  setGrid(grid) {
    if (!this.#map) return;
    this.#map = { ...this.#map, grid: { ...this.#map.grid, ...grid } };
    this.invalidate();
  }

  get map() {
    return this.#map;
  }

  get hasImage() {
    return this.#image !== null;
  }

  // --------------------------------------------------------------- tokens --

  setTokens(tokens) {
    this.#tokens = [...tokens].sort(compareTokens);
    for (const token of this.#tokens) this.#imageFor(token.url);
    this.invalidate();
  }

  upsertToken(token) {
    const index = this.#tokens.findIndex((t) => t.id === token.id);
    if (index === -1) this.#tokens.push(token);
    else this.#tokens[index] = token;
    this.#tokens.sort(compareTokens);
    this.#imageFor(token.url);
    this.invalidate();
  }

  removeToken(tokenId) {
    this.#tokens = this.#tokens.filter((t) => t.id !== tokenId);
    if (this.#selectedId === tokenId) this.#selectedId = null;
    this.invalidate();
  }

  get tokens() {
    return this.#tokens;
  }

  get selectedId() {
    return this.#selectedId;
  }

  /** Ring the token whose turn it is. Pass null for "no combat running". */
  setCurrentToken(tokenId) {
    if (this.#currentTokenId === tokenId) return;
    this.#currentTokenId = tokenId;
    this.invalidate();
  }

  get selected() {
    return this.#tokens.find((t) => t.id === this.#selectedId) || null;
  }

  select(tokenId) {
    if (this.#selectedId === tokenId) return;
    this.#selectedId = tokenId;
    this.invalidate();
    this.#canvas.dispatchEvent(new CustomEvent("board:select", { detail: this.selected }));
  }

  /** Topmost token containing a point in grid units, or null. */
  tokenAt(gridX, gridY) {
    for (let i = this.#tokens.length - 1; i >= 0; i--) {
      const t = this.#tokens[i];
      if (t.locked) continue;
      if (gridX >= t.x && gridX <= t.x + t.grid_w &&
          gridY >= t.y && gridY <= t.y + t.grid_h) {
        return t;
      }
    }
    return null;
  }

  /** Convert a screen point to grid units. Null when there is no map. */
  toGrid(screenX, screenY) {
    if (!this.#map) return null;
    const { x, y } = this.toWorld(screenX, screenY);
    const grid = this.#map.grid;
    return {
      x: (x - grid.offset_x) / grid.size_px,
      y: (y - grid.offset_y) / grid.size_px,
    };
  }

  #imageFor(url) {
    if (!url) return null;
    const cached = this.#imageCache.get(url);
    if (cached) return cached.complete ? cached : null;

    const image = new Image();
    image.decoding = "async";
    image.addEventListener("load", () => this.invalidate());
    // Cache the failure too, so a missing file is not re-requested every frame.
    image.addEventListener("error", () => this.#imageCache.set(url, image));
    image.src = url;
    this.#imageCache.set(url, image);
    return null;
  }

  invalidate() {
    this.#dirty = true;
  }

  // ---------------------------------------------------------------- view --

  fitToView() {
    if (!this.#image) return;
    const { width, height } = this.#cssSize();
    // A small margin so the map edge is visibly an edge rather than flush to
    // the window, which reads as "there is more off-screen".
    const margin = 0.96;
    this.#scale = Math.min(width / this.#image.width, height / this.#image.height) * margin;
    this.#scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, this.#scale));
    this.#originX = (width - this.#image.width * this.#scale) / 2;
    this.#originY = (height - this.#image.height * this.#scale) / 2;
    this.invalidate();
  }

  zoomAt(screenX, screenY, deltaScale) {
    const next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, this.#scale * deltaScale));
    if (next === this.#scale) return;
    // Keep the world point under the cursor pinned there, which is what makes
    // wheel-zoom feel like it is zooming the map rather than the window.
    const worldX = (screenX - this.#originX) / this.#scale;
    const worldY = (screenY - this.#originY) / this.#scale;
    this.#scale = next;
    this.#originX = screenX - worldX * this.#scale;
    this.#originY = screenY - worldY * this.#scale;
    this.invalidate();
  }

  panBy(dx, dy) {
    this.#originX += dx;
    this.#originY += dy;
    this.invalidate();
  }

  toWorld(screenX, screenY) {
    return {
      x: (screenX - this.#originX) / this.#scale,
      y: (screenY - this.#originY) / this.#scale,
    };
  }

  // -------------------------------------------------------------- render --

  #loop() {
    const tick = () => {
      if (this.#dirty) {
        this.#dirty = false;
        this.#draw();
      }
      this.#frame = requestAnimationFrame(tick);
    };
    this.#frame = requestAnimationFrame(tick);

    // requestAnimationFrame is suspended entirely while a window is hidden --
    // a backgrounded tab, or the display window on a monitor that is asleep.
    // That is the right default (no CPU burned on an invisible board), but the
    // canvas must repaint the moment it comes back rather than waiting for the
    // next state change, which may be minutes away in a quiet scene.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) this.redraw();
    });
  }

  /** Paint immediately, without waiting for the next animation frame. */
  redraw() {
    this.#dirty = false;
    this.#draw();
  }

  destroy() {
    if (this.#frame) cancelAnimationFrame(this.#frame);
    this.#resizeObserver?.disconnect();
  }

  #draw() {
    const ctx = this.#ctx;
    const { width, height } = this.#cssSize();

    ctx.save();
    ctx.setTransform(this.#dpr, 0, 0, this.#dpr, 0, 0);

    ctx.fillStyle = getComputedStyle(this.#canvas).getPropertyValue("--surface-0") || "#0d1014";
    ctx.fillRect(0, 0, width, height);

    if (this.#image) {
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(
        this.#image,
        this.#originX, this.#originY,
        this.#image.width * this.#scale, this.#image.height * this.#scale,
      );
      this.#drawGrid(ctx, width, height);
      this.#drawTokens(ctx, width, height);
      this.#drawFog(ctx);
      this.#drawBrush(ctx);
    }

    ctx.restore();
  }

  #drawBrush(ctx) {
    if (this.#tool !== "fog" || !this.#cursor || !this.#map) return;

    const grid = this.#map.grid;
    const cell = grid.size_px * this.#scale;
    const cx = this.#originX + (grid.offset_x + this.#cursor.x * grid.size_px) * this.#scale;
    const cy = this.#originY + (grid.offset_y + this.#cursor.y * grid.size_px) * this.#scale;

    ctx.save();
    ctx.strokeStyle = this.#fogRevealing ? "#57b46b" : "#e05c5c";
    ctx.lineWidth = 2 / this.#dpr;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.arc(cx, cy, this.#brushRadius * cell, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  #drawFog(ctx) {
    if (!this.#fog || this.#fogOpacity <= 0) return;
    const grid = this.#map?.grid;
    if (!grid) return;

    const { cols, rows, cells } = this.#fog;

    // One pixel per cell on an offscreen canvas, then scaled up with smoothing
    // off. Filling thousands of rectangles individually is what makes a fog
    // layer stutter while the brush is moving.
    if (!this.#fogCanvas) {
      const off = document.createElement("canvas");
      off.width = cols;
      off.height = rows;
      const octx = off.getContext("2d");
      const img = octx.createImageData(cols, rows);
      for (let i = 0; i < cells.length; i++) {
        // Opaque black where unrevealed, fully transparent where revealed.
        img.data[i * 4 + 3] = cells[i] ? 0 : 255;
      }
      octx.putImageData(img, 0, 0);
      this.#fogCanvas = off;
    }

    const cell = grid.size_px * this.#scale;
    ctx.save();
    ctx.globalAlpha = this.#fogOpacity;
    ctx.imageSmoothingEnabled = false;

    // Clip to the map so fog does not spill onto the surrounding surface.
    ctx.beginPath();
    ctx.rect(this.#originX, this.#originY,
             this.#image.width * this.#scale, this.#image.height * this.#scale);
    ctx.clip();

    ctx.drawImage(
      this.#fogCanvas,
      this.#originX + grid.offset_x * this.#scale,
      this.#originY + grid.offset_y * this.#scale,
      cols * cell,
      rows * cell,
    );
    ctx.restore();
  }

  #drawTokens(ctx, viewWidth, viewHeight) {
    const grid = this.#map?.grid;
    if (!grid) return;

    const cell = grid.size_px * this.#scale;

    for (const token of this.#tokens) {
      const screenX = this.#originX + (grid.offset_x + token.x * grid.size_px) * this.#scale;
      const screenY = this.#originY + (grid.offset_y + token.y * grid.size_px) * this.#scale;
      const width = token.grid_w * cell;
      const height = token.grid_h * cell;

      // Skip anything entirely off screen. A prepped scene can carry hundreds
      // of props while only a corner of it is in view.
      if (screenX + width < 0 || screenY + height < 0 ||
          screenX > viewWidth || screenY > viewHeight) {
        continue;
      }

      const image = this.#imageCache.get(token.url);
      const ready = image && image.complete && image.naturalWidth > 0;

      ctx.save();

      // A hidden token is GM-only and drawn ghosted, so the GM can see what
      // the players cannot. Players never receive it at all.
      if (token.hidden) ctx.globalAlpha = 0.4;

      if (token.rotation) {
        ctx.translate(screenX + width / 2, screenY + height / 2);
        ctx.rotate((token.rotation * Math.PI) / 180);
        ctx.translate(-(screenX + width / 2), -(screenY + height / 2));
      }

      if (ready) {
        ctx.drawImage(image, screenX, screenY, width, height);
      } else {
        // Placeholder while the art decodes, so a dropped token appears
        // immediately rather than after a network round trip.
        ctx.fillStyle = "rgba(217,164,65,.25)";
        ctx.fillRect(screenX, screenY, width, height);
      }

      ctx.restore();

      if (token.id === this.#currentTokenId) {
        this.#drawTurnMarker(ctx, screenX, screenY, width, height);
      }

      if (token.id === this.#selectedId) {
        this.#drawSelection(ctx, screenX, screenY, width, height);
      }

      if (token.label && cell > 24) {
        this.#drawLabel(ctx, token.label, screenX + width / 2, screenY + height);
      }
    }
  }

  /* A solid ring under the selection's dashed one, so a GM can have a creature
   * selected and see whose turn it is at the same time without the two markers
   * being mistaken for each other. */
  #drawTurnMarker(ctx, x, y, width, height) {
    ctx.save();
    ctx.strokeStyle = "#57b46b";
    ctx.lineWidth = 3 / this.#dpr;
    ctx.strokeRect(x - 2, y - 2, width + 4, height + 4);
    ctx.restore();
  }

  #drawSelection(ctx, x, y, width, height) {
    ctx.save();
    ctx.strokeStyle = "#d9a441";
    ctx.lineWidth = 2 / this.#dpr;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(x, y, width, height);
    ctx.restore();
  }

  #drawLabel(ctx, text, centreX, bottomY) {
    ctx.save();
    ctx.font = `${Math.max(11, Math.min(18, this.#scale * 14))}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const metrics = ctx.measureText(text);
    const padding = 4;
    const boxHeight = 16;
    ctx.fillStyle = "rgba(13,16,20,.82)";
    ctx.fillRect(
      centreX - metrics.width / 2 - padding, bottomY + 2,
      metrics.width + padding * 2, boxHeight,
    );
    ctx.fillStyle = "#f2f5f8";
    ctx.fillText(text, centreX, bottomY + 4);
    ctx.restore();
  }

  #drawGrid(ctx, viewWidth, viewHeight) {
    const grid = this.#map?.grid;
    if (!grid || !grid.visible || grid.opacity <= 0) return;

    const step = grid.size_px * this.#scale;
    // Below a couple of screen pixels the grid is a solid wash that hides the
    // artwork rather than helping anyone read distances. Drop it instead.
    if (step < 3) return;

    const mapWidth = this.#image.width * this.#scale;
    const mapHeight = this.#image.height * this.#scale;

    // Clip to the map so the grid does not bleed onto the surrounding surface.
    ctx.save();
    ctx.beginPath();
    ctx.rect(this.#originX, this.#originY, mapWidth, mapHeight);
    ctx.clip();

    ctx.strokeStyle = grid.color;
    ctx.globalAlpha = grid.opacity;
    // Hairline at any zoom: scaling the line width with the map turns a zoomed
    // grid into thick bars.
    ctx.lineWidth = 1 / this.#dpr;
    ctx.beginPath();

    const startX = this.#originX + grid.offset_x * this.#scale;
    const startY = this.#originY + grid.offset_y * this.#scale;

    // Only iterate lines that can actually appear on screen. A 20000px map at a
    // 10px grid is 2000 lines; walking all of them every frame while the slider
    // moves is what makes a grid feel sluggish.
    const firstCol = Math.floor((Math.max(0, this.#originX) - startX) / step);
    const lastCol = Math.ceil((Math.min(viewWidth, this.#originX + mapWidth) - startX) / step);
    for (let i = firstCol; i <= lastCol; i++) {
      // Half-pixel offset keeps a 1px line on a pixel boundary instead of
      // straddling two and rendering as a 2px blur.
      const x = Math.round(startX + i * step) + 0.5;
      ctx.moveTo(x, this.#originY);
      ctx.lineTo(x, this.#originY + mapHeight);
    }

    const firstRow = Math.floor((Math.max(0, this.#originY) - startY) / step);
    const lastRow = Math.ceil((Math.min(viewHeight, this.#originY + mapHeight) - startY) / step);
    for (let i = firstRow; i <= lastRow; i++) {
      const y = Math.round(startY + i * step) + 0.5;
      ctx.moveTo(this.#originX, y);
      ctx.lineTo(this.#originX + mapWidth, y);
    }

    ctx.stroke();
    ctx.restore();
  }

  // --------------------------------------------------------------- input --

  #bindPointer() {
    const canvas = this.#canvas;
    let mode = null;              // "pan" | "token"
    let lastX = 0;
    let lastY = 0;
    let pointerId = null;
    let dragToken = null;
    let grabOffset = { x: 0, y: 0 };

    const localPoint = (event) => {
      const rect = canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };

    canvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      pointerId = event.pointerId;
      lastX = event.clientX;
      lastY = event.clientY;
      // Capture keeps a drag alive when the cursor leaves the canvas, but it
      // throws if the pointer is no longer active -- which must not abort the
      // interaction itself.
      try { canvas.setPointerCapture(pointerId); } catch { /* not capturable */ }

      const point = localPoint(event);
      const grid = this.#editable ? this.toGrid(point.x, point.y) : null;

      // Fog mode takes the whole canvas: while the brush is selected, dragging
      // paints rather than moving whatever happens to be underneath.
      if (this.#tool === "fog" && grid) {
        mode = "fog";
        // Shift inverts the brush, so hiding a slip of the hand does not mean
        // reaching for the toolbar.
        this.#fogRevealing = !event.shiftKey;
        this.#emitFog(grid.x, grid.y);
        return;
      }

      const hit = grid ? this.tokenAt(grid.x, grid.y) : null;

      if (hit) {
        mode = "token";
        dragToken = hit;
        // Remember where within the token it was grabbed, so it does not
        // snap its corner to the cursor on the first pixel of movement.
        grabOffset = { x: grid.x - hit.x, y: grid.y - hit.y };
        this.select(hit.id);
        canvas.style.cursor = "grabbing";
      } else {
        mode = "pan";
        if (this.#editable) this.select(null);
        canvas.style.cursor = "grabbing";
      }
    });

    canvas.addEventListener("pointermove", (event) => {
      // Track the cursor so the brush outline follows it even between strokes.
      if (this.#tool === "fog") {
        const p = localPoint(event);
        this.#cursor = this.toGrid(p.x, p.y);
        this.invalidate();
      }

      if (!mode || event.pointerId !== pointerId) return;

      if (mode === "fog") {
        const p = localPoint(event);
        const g = this.toGrid(p.x, p.y);
        if (g) this.#emitFog(g.x, g.y);
        return;
      }

      if (mode === "pan") {
        this.panBy(event.clientX - lastX, event.clientY - lastY);
        lastX = event.clientX;
        lastY = event.clientY;
        return;
      }

      const point = localPoint(event);
      const grid = this.toGrid(point.x, point.y);
      if (!grid || !dragToken) return;

      let x = grid.x - grabOffset.x;
      let y = grid.y - grabOffset.y;
      // Alt overrides snapping for scatter and clutter that should not sit on
      // a lattice; holding it is faster than reaching for the toggle.
      if (this.#snap && !event.altKey) {
        x = Math.round(x);
        y = Math.round(y);
      }
      if (x === dragToken.x && y === dragToken.y) return;

      dragToken.x = x;
      dragToken.y = y;
      this.invalidate();
      canvas.dispatchEvent(new CustomEvent("board:tokenmove", {
        detail: { id: dragToken.id, x, y },
      }));
    });

    const endDrag = (event) => {
      if (event.pointerId !== pointerId) return;
      if (mode === "token" && dragToken) {
        canvas.dispatchEvent(new CustomEvent("board:tokendrop", {
          detail: { id: dragToken.id, x: dragToken.x, y: dragToken.y },
        }));
      }
      mode = null;
      dragToken = null;
      pointerId = null;
      canvas.style.cursor = "grab";
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);

    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      // deltaMode 1 is lines rather than pixels (Firefox); scale it up so one
      // notch of the wheel zooms comparably across browsers.
      const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
      this.zoomAt(
        event.clientX - rect.left,
        event.clientY - rect.top,
        Math.exp(-delta * ZOOM_RATE),
      );
    }, { passive: false });

    canvas.addEventListener("dblclick", () => this.fitToView());
    canvas.style.cursor = "grab";
  }

  // ---------------------------------------------------------------- size --

  #resizeObserver = null;
  #dpr = 1;

  #cssSize() {
    return { width: this.#canvas.clientWidth, height: this.#canvas.clientHeight };
  }

  #observeSize() {
    const apply = () => {
      const { width, height } = this.#cssSize();
      if (width === 0 || height === 0) return;

      // Back the canvas with real device pixels. Without this the grid is a
      // blurry grey smear on every HiDPI laptop, which is most of them.
      this.#dpr = window.devicePixelRatio || 1;
      const backingWidth = Math.round(width * this.#dpr);
      const backingHeight = Math.round(height * this.#dpr);

      if (this.#canvas.width !== backingWidth || this.#canvas.height !== backingHeight) {
        this.#canvas.width = backingWidth;
        this.#canvas.height = backingHeight;
        this.invalidate();
      }
    };

    apply();
    this.#resizeObserver = new ResizeObserver(apply);
    this.#resizeObserver.observe(this.#canvas);
    // devicePixelRatio changes when a window moves between monitors of
    // different densities -- exactly what happens when the GM drags the display
    // window to the projector.
    window.addEventListener("resize", apply);
  }
}

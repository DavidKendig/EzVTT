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

const TOOLS = ["select", "fog", "circle", "cone", "line"];
const TEMPLATE_TOOLS = ["circle", "cone", "line"];

/* One square is five feet. The ruler shows squares first, because that is the
 * figure that stays true on a map drawn to some other scale. */
const FEET_PER_SQUARE = 5;

/* A 5e cone is as wide at its far end as it is long, which puts its edges at
 * atan(0.5) either side of the direction it is pointed. Everything else about
 * a cone follows from that one number. */
const CONE_HALF_ANGLE = Math.atan(0.5);

/* Brass, matching the accent. The GM can pick another; this is where a drag
 * starts from. */
const TEMPLATE_COLOR = "#d9a441";

/* Long enough for someone looking at the other screen to catch it, short
 * enough that it is gone before it becomes clutter. */
const PING_MS = 2400;
const PING_COLOR = "#e05c5c";

/* A click and a drag start identically. This is how far the pointer may travel
 * and still count as pointing at something. */
const CLICK_SLOP_PX = 4;

/** Chebyshev: a diagonal step costs the same as a straight one, as in 5e. */
function squaresBetween(a, b) {
  return Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y));
}

/* Exact hit points when the payload carries them, otherwise the coarse bar a
 * player is sent instead. Null when this token tracks no health at all. */
function healthFraction(token) {
  if (token.hp !== null && token.hp !== undefined
      && token.hp_max !== null && token.hp_max !== undefined && token.hp_max > 0) {
    return Math.max(0, Math.min(1, token.hp / token.hp_max));
  }
  if (token.hp_bar !== null && token.hp_bar !== undefined) {
    return Math.max(0, Math.min(1, token.hp_bar / 4));
  }
  return null;
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

/** Ray casting. Used for cones and lines, which are triangles and rectangles. */
function pointInPolygon(x, y, points) {
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const { x: xi, y: yi } = points[i];
    const { x: xj, y: yj } = points[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/* Templates are filled translucent and stroked solid, from one hex colour the
 * server has already validated as #rgb or #rrggbb. */
function withAlpha(hex, alpha) {
  const value = String(hex || "#d9a441").replace("#", "");
  const full = value.length === 3 ? value.split("").map((c) => c + c).join("") : value;
  const int = parseInt(full, 16);
  if (Number.isNaN(int)) return `rgba(217,164,65,${alpha})`;
  return `rgba(${(int >> 16) & 255},${(int >> 8) & 255},${int & 255},${alpha})`;
}

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
  // Who is looking. A player may drag the token they own and nothing else, so
  // the board has to know which of them is theirs. See ADR-021.
  #viewerId = null;

  // Fog is a mask of revealed cells plus how solidly to paint the rest.
  //   GM screen      0.55 -- see through what you are concealing
  //   display window 1.00 -- the table must not see through it
  //   player view    n/a  -- their map arrives already composited
  #fog = null;
  #fogOpacity = 0.55;
  #fogCanvas = null;

  // "select" moves tokens and pans; "fog" paints; "circle" / "cone" / "line"
  // drag out a template. One at a time, because a brush that also drags
  // furniture is a brush nobody trusts.
  #tool = "select";
  #brushRadius = 2;
  #fogRevealing = true;
  #cursor = null;

  // Area-of-effect templates: shared table state, drawn under the tokens so a
  // creature standing in a fireball is still the thing you can see.
  #templates = [];
  #selectedTemplateId = null;
  #templateWidth = 1;
  #templateColor = TEMPLATE_COLOR;
  // The shape being dragged out right now, and the measurement someone is
  // taking. Both local: neither has been settled on yet, and a measurement
  // never becomes table state at all. See ADR-014.
  #draft = null;
  #ruler = null;
  // id -> {label, short}, sent with the snapshot so the vocabulary lives in
  // one place rather than being spelled out again here.
  #conditions = {};
  // Live pings, dropped as they expire. Never stored anywhere: a ping is a
  // gesture at a shared screen, and it has done its job before anyone could
  // ask what happened to it.
  #pings = [];

  constructor(canvas, {
    interactive = true, editable = false, fogOpacity = 0.55, viewerId = null,
  } = {}) {
    this.#canvas = canvas;
    this.#ctx = canvas.getContext("2d", { alpha: false });
    this.#editable = editable;
    this.#fogOpacity = fogOpacity;
    this.#viewerId = viewerId;

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
    this.#tool = TOOLS.includes(name) ? name : "select";
    if (this.#tool !== "select") {
      this.select(null);
      this.selectTemplate(null);
    }
    this.#cursor = null;
    this.#canvas.style.cursor =
      this.#tool === "fog" ? "none" : (this.#tool === "select" ? "grab" : "crosshair");
    this.invalidate();
  }

  get tool() {
    return this.#tool;
  }

  set templateWidth(value) {
    this.#templateWidth = Math.max(0.1, Math.min(20, Number(value) || 1));
  }

  get templateWidth() {
    return this.#templateWidth;
  }

  set templateColor(value) {
    this.#templateColor = value || TEMPLATE_COLOR;
  }

  get templateColor() {
    return this.#templateColor;
  }

  /* Half-square steps. Spell radii land on square edges and a cone's apex
   * usually sits on a corner or a cell centre, so halves cover both. Alt
   * overrides, exactly as it does when placing a token. */
  #snapValue(value, altKey) {
    if (!this.#snap || altKey) return value;
    return Math.round(value * 2) / 2;
  }

  /** The condition vocabulary the server sent with the table. */
  setConditions(vocabulary) {
    this.#conditions = vocabulary || {};
    this.invalidate();
  }

  /** Mark a spot for a couple of seconds. */
  ping(x, y, by = "") {
    this.#pings.push({ x, y, by, at: performance.now() });
    this.invalidate();
  }

  /** Take the measurement off the screen. */
  clearRuler() {
    if (!this.#ruler) return;
    this.#ruler = null;
    this.invalidate();
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

  // ---------------------------------------------------------- templates --

  setTemplates(templates) {
    this.#templates = [...(templates || [])];
    if (!this.#templates.some((t) => t.id === this.#selectedTemplateId)) {
      this.#selectedTemplateId = null;
    }
    this.invalidate();
  }

  get templates() {
    return this.#templates;
  }

  get selectedTemplate() {
    return this.#templates.find((t) => t.id === this.#selectedTemplateId) || null;
  }

  selectTemplate(templateId) {
    if (this.#selectedTemplateId === templateId) return;
    this.#selectedTemplateId = templateId;
    this.invalidate();
    this.#canvas.dispatchEvent(new CustomEvent("board:templateselect", {
      detail: this.selectedTemplate,
    }));
  }

  /* Corners in grid units, for a cone or a line. Drawing and hit-testing both
   * read this, so a shape can never be tested against an outline other than
   * the one on screen. */
  #outline(template) {
    const { x, y, size, angle, kind } = template;
    const heading = (angle * Math.PI) / 180;
    const at = (distance, bearing) => ({
      x: x + Math.cos(bearing) * distance,
      y: y + Math.sin(bearing) * distance,
    });

    if (kind === "cone") {
      // The far corners sit at the cone's half-angle either side, far enough
      // along that the *axis* is `size` long rather than the edges.
      const edge = size / Math.cos(CONE_HALF_ANGLE);
      return [
        { x, y },
        at(edge, heading - CONE_HALF_ANGLE),
        at(edge, heading + CONE_HALF_ANGLE),
      ];
    }

    const half = (template.width || 1) / 2;
    const across = heading + Math.PI / 2;
    const tip = at(size, heading);
    return [
      { x: x + Math.cos(across) * half, y: y + Math.sin(across) * half },
      { x: x - Math.cos(across) * half, y: y - Math.sin(across) * half },
      { x: tip.x - Math.cos(across) * half, y: tip.y - Math.sin(across) * half },
      { x: tip.x + Math.cos(across) * half, y: tip.y + Math.sin(across) * half },
    ];
  }

  /** Topmost template containing a point in grid units, or null. */
  templateAt(gridX, gridY) {
    for (let i = this.#templates.length - 1; i >= 0; i--) {
      const template = this.#templates[i];
      if (template.kind === "circle") {
        const dx = gridX - template.x;
        const dy = gridY - template.y;
        if (dx * dx + dy * dy <= template.size * template.size) return template;
        continue;
      }
      if (pointInPolygon(gridX, gridY, this.#outline(template))) return template;
    }
    return null;
  }

  /** Whether the person at this screen may drag that token.

   * The server decides this too, and its answer is the one that counts; this
   * is so the cursor does not offer a drag that will be refused. */
  canMove(token) {
    if (!token || token.locked) return false;
    if (this.#editable) return true;
    // Hidden is refused for a player here as well as on the server. A player is
    // never sent a hidden token, so this cannot come up from a real payload --
    // but the two rules disagreeing means the board offers a drag the server
    // will refuse, and the token snaps back as if EzVTT were broken.
    return this.#viewerId !== null
      && token.owner_user_id === this.#viewerId
      && !token.hidden;
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
      // Under the tokens: a creature standing in a fireball is still the thing
      // you need to be able to see.
      this.#drawTemplates(ctx);
      this.#drawTokens(ctx, width, height);
      this.#drawFog(ctx);
      this.#drawBrush(ctx);
      this.#drawRuler(ctx);
      // Over the fog, deliberately: the GM pinging into a concealed corridor is
      // pointing at something they can see and the table cannot. A player is
      // never sent that ping in the first place.
      this.#drawPings(ctx);
    }

    ctx.restore();
  }

  #drawPings(ctx) {
    if (this.#pings.length === 0) return;

    const grid = this.#map.grid;
    const now = performance.now();
    this.#pings = this.#pings.filter((p) => now - p.at < PING_MS);

    for (const ping of this.#pings) {
      const progress = (now - ping.at) / PING_MS;
      const x = this.#originX + (grid.offset_x + ping.x * grid.size_px) * this.#scale;
      const y = this.#originY + (grid.offset_y + ping.y * grid.size_px) * this.#scale;
      // Two rings a beat apart, each expanding and fading: one circle appearing
      // is easy to miss on a screen six people are looking at.
      for (const offset of [0, 0.35]) {
        const phase = progress - offset;
        if (phase < 0 || phase > 1) continue;
        ctx.save();
        ctx.globalAlpha = 1 - phase;
        ctx.strokeStyle = PING_COLOR;
        ctx.lineWidth = 3 / this.#dpr;
        ctx.beginPath();
        ctx.arc(x, y, (0.3 + phase * 1.4) * grid.size_px * this.#scale, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
      if (ping.by) this.#drawLabel(ctx, ping.by, x, y);
    }

    // Animated, so the board has to keep painting while any are alive. Nothing
    // else here needs a frame it did not ask for.
    if (this.#pings.length > 0) this.invalidate();
  }

  #drawTemplates(ctx) {
    const grid = this.#map?.grid;
    if (!grid) return;

    for (const template of this.#templates) {
      this.#drawTemplate(ctx, template, template.id === this.#selectedTemplateId);
    }
    // The shape being dragged out, drawn the same way so what is released is
    // what was seen.
    if (this.#draft) this.#drawTemplate(ctx, this.#draft, false, true);
  }

  #drawTemplate(ctx, template, selected, draft = false) {
    const grid = this.#map.grid;
    const toScreen = (point) => ({
      x: this.#originX + (grid.offset_x + point.x * grid.size_px) * this.#scale,
      y: this.#originY + (grid.offset_y + point.y * grid.size_px) * this.#scale,
    });

    ctx.save();
    ctx.fillStyle = withAlpha(template.color, template.hidden ? 0.12 : 0.22);
    ctx.strokeStyle = template.color;
    ctx.lineWidth = 2 / this.#dpr;
    if (draft || template.hidden) ctx.setLineDash([6, 4]);

    ctx.beginPath();
    if (template.kind === "circle") {
      const centre = toScreen(template);
      ctx.arc(centre.x, centre.y, template.size * grid.size_px * this.#scale,
              0, Math.PI * 2);
    } else {
      const points = this.#outline(template).map(toScreen);
      ctx.moveTo(points[0].x, points[0].y);
      for (const point of points.slice(1)) ctx.lineTo(point.x, point.y);
      ctx.closePath();
    }
    ctx.fill();
    ctx.stroke();

    if (selected) {
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = "#f2f5f8";
      ctx.stroke();
    }
    ctx.restore();

    if (template.label) {
      const origin = toScreen(template);
      this.#drawLabel(ctx, template.label, origin.x, origin.y);
    }
  }

  #drawRuler(ctx) {
    if (!this.#ruler || !this.#map) return;

    const grid = this.#map.grid;
    const toScreen = (point) => ({
      x: this.#originX + (grid.offset_x + point.x * grid.size_px) * this.#scale,
      y: this.#originY + (grid.offset_y + point.y * grid.size_px) * this.#scale,
    });
    const from = toScreen(this.#ruler.from);
    const to = toScreen(this.#ruler.to);

    ctx.save();
    ctx.strokeStyle = "#5b9dd9";
    ctx.lineWidth = 2 / this.#dpr;
    ctx.setLineDash([7, 5]);
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);
    ctx.stroke();

    ctx.setLineDash([]);
    for (const end of [from, to]) {
      ctx.beginPath();
      ctx.arc(end.x, end.y, 3 / this.#dpr, 0, Math.PI * 2);
      ctx.fillStyle = "#5b9dd9";
      ctx.fill();
    }
    ctx.restore();

    // Feet are derived from the figure actually shown, not from the raw
    // distance behind it -- "38.2 sq · 190.8 ft" invites a GM to check the
    // arithmetic and find it wrong.
    const squares = round1(squaresBetween(this.#ruler.from, this.#ruler.to));
    this.#drawLabel(
      ctx,
      `${squares} sq · ${round1(squares * FEET_PER_SQUARE)} ft`,
      to.x, to.y,
    );
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

      // Health under the token, conditions over it: a bar at the feet reads as
      // part of the creature, and badges over the head do not sit under the
      // next token along.
      const barHeight = this.#drawHealth(ctx, token, screenX, screenY, width, height);
      this.#drawConditions(ctx, token, screenX, screenY, width, cell);

      if (token.label && cell > 24) {
        this.#drawLabel(ctx, token.label, screenX + width / 2,
                        screenY + height + barHeight);
      }
    }
  }

  /* Green, amber, red. The bar is drawn from whichever of the two the payload
   * carries: a GM and the token's owner get exact hit points, everyone else
   * gets the same bar in quarters -- see ADR-017. Returns the height it used,
   * so the label below knows where it is. */
  #drawHealth(ctx, token, x, y, width, height) {
    const fraction = healthFraction(token);
    if (fraction === null || width < 12) return 0;

    const barHeight = Math.max(3, Math.min(7, height * 0.08));
    const top = y + height + 1;

    ctx.save();
    ctx.fillStyle = "rgba(13,16,20,.75)";
    ctx.fillRect(x, top, width, barHeight);
    ctx.fillStyle = fraction > 0.5 ? "#57b46b" : (fraction > 0.25 ? "#e0a13c" : "#e05c5c");
    ctx.fillRect(x, top, width * fraction, barHeight);
    ctx.strokeStyle = "rgba(0,0,0,.55)";
    ctx.lineWidth = 1 / this.#dpr;
    ctx.strokeRect(x + 0.5, top + 0.5, width - 1, barHeight - 1);
    ctx.restore();

    return barHeight + 2;
  }

  #drawConditions(ctx, token, x, y, width, cell) {
    const conditions = token.conditions || [];
    if (conditions.length === 0 || cell < 18) return;

    const size = Math.max(10, Math.min(20, cell * 0.28));
    const gap = size * 0.15;
    let left = x;
    const top = y - size - 2;

    ctx.save();
    ctx.font = `600 ${Math.round(size * 0.55)}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    for (const id of conditions) {
      // Wrap rather than run off the token when a creature is having a bad
      // round: four badges a row, stacked upwards.
      if (left + size > x + Math.max(width, size * 4)) break;

      ctx.fillStyle = id === "dead" ? "#e05c5c" : "rgba(13,16,20,.88)";
      ctx.strokeStyle = "#d9a441";
      ctx.lineWidth = 1 / this.#dpr;
      ctx.beginPath();
      // roundRect is recent enough to be worth a fallback, and `?.` would not
      // do here: it returns undefined either way, so a `??` chain would draw
      // both shapes.
      if (ctx.roundRect) ctx.roundRect(left, top, size, size, 3);
      else ctx.rect(left, top, size, size);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = "#f2f5f8";
      ctx.fillText(this.#conditions[id]?.short ?? id.slice(0, 2).toUpperCase(),
                   left + size / 2, top + size / 2 + 0.5);
      left += size + gap;
    }
    ctx.restore();
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
    let pingCandidate = null;

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
      // Measured by anyone, including a player: the ruler changes nothing, so
      // it does not need the rights that moving something does.
      const anyGrid = this.toGrid(point.x, point.y);
      // Everyone gets grid coordinates now: a player needs them to drag their
      // own token, and the ruler and ping have always needed them.
      const grid = anyGrid;

      // Alt-click points at a spot; alt-*drag* still moves a token off the
      // grid, as it always has. Which one it was is only knowable on release,
      // so the decision is deferred to endDrag. Not while the fog brush is up:
      // Alt is not part of that gesture, and a stray ping mid-conceal would be
      // pointing at the very thing being hidden.
      pingCandidate = event.altKey && this.#tool !== "fog" && anyGrid
        ? { x: event.clientX, y: event.clientY, grid: anyGrid }
        : null;

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

      // Shift-drag measures, on every screen and with every tool but the fog
      // brush, where Shift already means "conceal".
      if (event.shiftKey && anyGrid) {
        mode = "ruler";
        this.#ruler = { from: anyGrid, to: anyGrid };
        this.invalidate();
        return;
      }

      if (TEMPLATE_TOOLS.includes(this.#tool) && grid) {
        mode = "template";
        this.#draft = {
          kind: this.#tool,
          x: this.#snapValue(grid.x, event.altKey),
          y: this.#snapValue(grid.y, event.altKey),
          size: 0, angle: 0, width: this.#templateWidth,
          color: this.#templateColor, label: null, hidden: false,
        };
        this.invalidate();
        return;
      }

      const template = grid && this.#editable ? this.templateAt(grid.x, grid.y) : null;
      const candidate = grid ? this.tokenAt(grid.x, grid.y) : null;
      // Someone else's token is scenery: the drag falls through to a pan, which
      // is what a player expects from dragging the map.
      const hit = this.canMove(candidate) ? candidate : null;

      // A token wins a contested click: the template under it is scenery for
      // the moment, and the creature is what the GM reached for.
      if (!hit && template) {
        mode = "pan";
        this.select(null);
        this.selectTemplate(template.id);
        canvas.style.cursor = "grabbing";
        return;
      }
      if (this.#editable) this.selectTemplate(null);

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

      if (mode === "ruler") {
        const p = localPoint(event);
        const g = this.toGrid(p.x, p.y);
        if (g) { this.#ruler.to = g; this.invalidate(); }
        return;
      }

      if (mode === "template") {
        const p = localPoint(event);
        const g = this.toGrid(p.x, p.y);
        if (!g) return;
        const reach = squaresBetween(this.#draft, g);
        this.#draft.size = Math.max(0.1, this.#snapValue(reach, event.altKey));
        this.#draft.angle =
          (Math.atan2(g.y - this.#draft.y, g.x - this.#draft.x) * 180) / Math.PI;
        this.#draft.width = this.#templateWidth;
        this.invalidate();
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

      if (pingCandidate
          && Math.abs(event.clientX - pingCandidate.x) <= CLICK_SLOP_PX
          && Math.abs(event.clientY - pingCandidate.y) <= CLICK_SLOP_PX) {
        canvas.dispatchEvent(new CustomEvent("board:ping", {
          detail: { x: pingCandidate.grid.x, y: pingCandidate.grid.y },
        }));
      }
      pingCandidate = null;

      if (mode === "token" && dragToken) {
        canvas.dispatchEvent(new CustomEvent("board:tokendrop", {
          detail: { id: dragToken.id, x: dragToken.x, y: dragToken.y },
        }));
      }

      // Only the shape settled on is sent, once. Every frame of the drag was a
      // local preview of a decision not yet made.
      if (mode === "template" && this.#draft) {
        const draft = this.#draft;
        this.#draft = null;
        this.invalidate();
        if (draft.size >= 0.1) {
          canvas.dispatchEvent(new CustomEvent("board:templateplace", {
            detail: {
              kind: draft.kind, x: draft.x, y: draft.y,
              size: draft.size, angle: draft.angle, width: draft.width,
            },
          }));
        }
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

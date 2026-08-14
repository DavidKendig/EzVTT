/* Live connection to the table.
 *
 * Reconnects on its own. A GM's laptop lid closing mid-session, a player's phone
 * sleeping, or a Wi-Fi hiccup must not require anyone to reload a page -- so
 * this retries with backoff and asks for a full resync each time it comes back,
 * rather than assuming it can resume from where it left off.
 */

const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 15000;
const HEARTBEAT_MS = 25000;

export class TableSocket extends EventTarget {
  #url;
  #socket = null;
  #retryMs = RECONNECT_MIN_MS;
  #heartbeat = null;
  #closedByUs = false;

  constructor(surface = "play") {
    super();
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    this.#url = `${scheme}//${location.host}/ws?surface=${encodeURIComponent(surface)}`;
  }

  get connected() {
    return this.#socket?.readyState === WebSocket.OPEN;
  }

  connect() {
    this.#closedByUs = false;

    let socket;
    try {
      socket = new WebSocket(this.#url);
    } catch {
      this.#scheduleReconnect();
      return;
    }
    this.#socket = socket;

    socket.addEventListener("open", () => {
      this.#retryMs = RECONNECT_MIN_MS;
      this.#startHeartbeat();
      this.dispatchEvent(new CustomEvent("status", { detail: { connected: true } }));
    });

    socket.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.type === "pong") return;
      this.dispatchEvent(new CustomEvent(message.type, { detail: message }));
      this.dispatchEvent(new CustomEvent("*", { detail: message }));
    });

    socket.addEventListener("close", () => {
      this.#stopHeartbeat();
      this.dispatchEvent(new CustomEvent("status", { detail: { connected: false } }));
      if (!this.#closedByUs) this.#scheduleReconnect();
    });

    // An error is always followed by a close, which is where reconnect is
    // handled. Swallow it so it does not reach the console as an uncaught event.
    socket.addEventListener("error", () => {});
  }

  send(type, payload = {}) {
    if (!this.connected) return false;
    this.#socket.send(JSON.stringify({ type, payload }));
    return true;
  }

  close() {
    this.#closedByUs = true;
    this.#stopHeartbeat();
    this.#socket?.close();
  }

  #scheduleReconnect() {
    const delay = this.#retryMs;
    // Exponential backoff, jittered: without jitter every client on the table
    // would reconnect in lockstep after a server restart.
    this.#retryMs = Math.min(this.#retryMs * 2, RECONNECT_MAX_MS);
    setTimeout(() => this.connect(), delay + Math.random() * 250);
  }

  #startHeartbeat() {
    this.#stopHeartbeat();
    // Proxies and phone radios drop idle sockets silently. A periodic ping keeps
    // the connection alive and surfaces a dead one quickly.
    this.#heartbeat = setInterval(() => this.send("ping"), HEARTBEAT_MS);
  }

  #stopHeartbeat() {
    if (this.#heartbeat) clearInterval(this.#heartbeat);
    this.#heartbeat = null;
  }
}

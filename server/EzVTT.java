import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.spec.KeySpec;
import java.util.*;
import java.util.concurrent.*;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

/**
 * EzVTT edge gateway + game hub (DEMO).
 *
 * This single process is the ONLY thing exposed to the network. It:
 *   1. Reverse-proxies HTTP page requests to a localhost-only Django UI app,
 *      injecting the identity (X-EzVTT-Role / X-EzVTT-User) it determines.
 *   2. Owns a WebSocket hub for the live shared play space and enforces the
 *      GM-vs-player authorization rule server-side.
 *
 * *** AUTHENTICATION IS CURRENTLY DISABLED FOR DEBUGGING ***
 * Instead of real login/sessions, identity comes from a simple GM/Player toggle
 * backed by the EzVTT_DEBUG_ROLE cookie (default: gm). The full auth machinery
 * (accounts, sessions, PBKDF2 hashing, /auth/* routes) is left intact below but
 * dormant -- search for "AUTH DISABLED" to re-enable it.
 *
 * Still omitted for the demo: TLS, HMAC-signed identity header, CSRF tokens,
 * persistent account storage. Those are the next layer.
 */
public class EzVTT {
    // Ports are overridable via env vars so the demo can coexist with other
    // services (EzVTT_EDGE_PORT / EzVTT_DJANGO_PORT).
    static final int    EDGE_PORT   = envInt("EzVTT_EDGE_PORT", 8080);   // the one public port
    static final String DJANGO_HOST = "127.0.0.1";                       // private UI app
    static final int    DJANGO_PORT = envInt("EzVTT_DJANGO_PORT", 8000);
    static final String WS_GUID     = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
    static final String SESSION_COOKIE = "EzVTT_SESSION";

    static int envInt(String name, int fallback) {
        String v = System.getenv(name);
        try { return v == null ? fallback : Integer.parseInt(v.trim()); }
        catch (NumberFormatException bad) { return fallback; }
    }

    // Shared play-space state (demo: one token on a grid). All of this is the
    // live state the hub broadcasts; the GM mutates it, players observe it.
    static volatile int tokenX = 5, tokenY = 5;
    static volatile String mapUrl = "";        // active battlemap shown to all ("" = none)
    static volatile int cols = 10, rows = 10;   // grid the GM fitted to the map
    static volatile String tokenUrl = "";       // active token image ("" = default marker)
    static final Set<Client> clients = ConcurrentHashMap.newKeySet();

    // ---- Accounts & sessions (DEMO: in-memory, lost on restart) -------------
    static final Map<String, Account> accounts = new ConcurrentHashMap<>(); // key: lowercased username
    static final Map<String, String>  sessions = new ConcurrentHashMap<>(); // sessionId -> username
    static final SecureRandom RNG = new SecureRandom();

    static final class Account {
        final String username;   // display name
        final String passHash;   // PBKDF2 "saltB64$hashB64"
        final String role;       // "gm" (admin) or "player"
        Account(String username, String passHash, String role) {
            this.username = username; this.passHash = passHash; this.role = role;
        }
    }

    public static void main(String[] args) throws IOException {
        // ---------------------------------------------------------------------
        // HARD-CODED ADMIN ACCOUNT (demo): username "Gary", password "Gygax".
        //
        // TODO: do NOT ship a known default credential in production. Replace
        // this seed with a first-run setup flow that FORCES the superuser to
        // choose a new admin username + password on first login (e.g. mark the
        // account "must_change=true" and redirect every request to a setup page
        // until they set their own credentials), then persist accounts to a
        // store instead of holding them in memory.
        //
        // AUTH DISABLED: dormant while debugging. Re-enable with the /auth/*
        // routes in handle() and the session identity in proxyToDjango/doWebSocket.
        // ---------------------------------------------------------------------
        accounts.put("gary", new Account("Gary", hashPassword("Gygax"), "gm"));

        ServerSocket server = new ServerSocket(EDGE_PORT);
        System.out.println("EzVTT edge gateway listening on http://localhost:" + EDGE_PORT);
        System.out.println("  proxying pages -> Django at " + DJANGO_HOST + ":" + DJANGO_PORT);
        System.out.println("  *** AUTH DISABLED (debug) *** identity via GM/Player toggle, default GM");
        ExecutorService pool = Executors.newCachedThreadPool();
        while (true) {
            Socket s = server.accept();
            pool.submit(() -> handle(s));
        }
    }

    static void handle(Socket socket) {
        try {
            InputStream  in  = new BufferedInputStream(socket.getInputStream());
            OutputStream out = new BufferedOutputStream(socket.getOutputStream());
            String head = readHead(in);
            if (head == null || head.isEmpty()) { socket.close(); return; }

            Map<String,String> headers = parseHeaders(head);
            String[] requestLine = head.substring(0, head.indexOf("\r\n")).split(" ");
            String method = requestLine.length > 0 ? requestLine[0] : "GET";
            String target = requestLine.length > 1 ? requestLine[1] : "/";
            String path = target.split("\\?", 2)[0];

            if ("websocket".equalsIgnoreCase(headers.getOrDefault("upgrade", ""))) {
                doWebSocket(socket, in, out, headers, target);
            } else if (path.equals("/stats")) {
                serveStats(socket, out);            // live runtime state, owned by Java
            } else if (path.equals("/debug/role")) {
                handleDebugRole(socket, out, target);   // AUTH DISABLED: GM/Player toggle
            // --- AUTH DISABLED FOR DEBUGGING (re-enable these routes to restore login) ---
            // } else if (path.equals("/auth/login")) {
            //     handleLogin(socket, in, out, headers);
            // } else if (path.equals("/auth/register")) {
            //     handleRegister(socket, in, out, headers);
            // } else if (path.equals("/auth/logout")) {
            //     handleLogout(socket, out, headers);
            } else {
                proxyToDjango(socket, in, out, method, target, headers);
            }
        } catch (Exception e) {
            try { socket.close(); } catch (IOException ignore) {}
        }
    }

    // ---- Authentication ----------------------------------------------------

    static void handleLogin(Socket client, InputStream in, OutputStream out,
                            Map<String,String> headers) throws IOException {
        Map<String,String> form = readForm(in, headers);
        String username = form.getOrDefault("username", "").trim();
        String password = form.getOrDefault("password", "");
        Account acc = accounts.get(username.toLowerCase());
        if (acc != null && verifyPassword(password, acc.passHash)) {
            redirect(client, out, "/", newSessionCookie(acc.username));
        } else {
            redirect(client, out, "/login?error=bad", null);   // generic: don't reveal which field
        }
    }

    static void handleRegister(Socket client, InputStream in, OutputStream out,
                               Map<String,String> headers) throws IOException {
        Map<String,String> form = readForm(in, headers);
        String username = form.getOrDefault("username", "").trim();
        String password = form.getOrDefault("password", "");
        if (username.isEmpty() || username.length() > 32 || password.length() < 4) {
            redirect(client, out, "/register?error=invalid", null);
        } else if (accounts.containsKey(username.toLowerCase())) {
            redirect(client, out, "/register?error=taken", null);
        } else {
            // New self-registered users are players. Only the seeded admin is GM.
            accounts.put(username.toLowerCase(), new Account(username, hashPassword(password), "player"));
            redirect(client, out, "/", newSessionCookie(username));
        }
    }

    static void handleLogout(Socket client, OutputStream out, Map<String,String> headers) throws IOException {
        String sid = cookie(headers, SESSION_COOKIE);
        if (sid != null) sessions.remove(sid);
        redirect(client, out, "/", SESSION_COOKIE + "=; Max-Age=0");   // clear cookie
    }

    static String newSessionCookie(String username) {
        byte[] b = new byte[24];
        RNG.nextBytes(b);
        String sid = Base64.getUrlEncoder().withoutPadding().encodeToString(b);
        sessions.put(sid, username);
        return SESSION_COOKIE + "=" + sid;
    }

    /** Account for the request's session cookie, or null if not logged in. */
    static Account currentAccount(Map<String,String> headers) {
        String sid = cookie(headers, SESSION_COOKIE);
        if (sid == null) return null;
        String username = sessions.get(sid);
        return username == null ? null : accounts.get(username.toLowerCase());
    }

    // PBKDF2-HMAC-SHA256 password hashing. (bcrypt/argon2 are also fine; the
    // point is a salted, stretched hash rather than a bare digest.)
    static final int PBKDF2_ITERS = 120_000;

    static String hashPassword(String pw) {
        byte[] salt = new byte[16];
        RNG.nextBytes(salt);
        return Base64.getEncoder().encodeToString(salt) + "$"
             + Base64.getEncoder().encodeToString(pbkdf2(pw, salt));
    }

    static boolean verifyPassword(String pw, String stored) {
        String[] parts = stored.split("\\$", 2);
        if (parts.length != 2) return false;
        byte[] salt = Base64.getDecoder().decode(parts[0]);
        byte[] expected = Base64.getDecoder().decode(parts[1]);
        return MessageDigest.isEqual(pbkdf2(pw, salt), expected);  // constant-time compare
    }

    static byte[] pbkdf2(String pw, byte[] salt) {
        try {
            KeySpec spec = new PBEKeySpec(pw.toCharArray(), salt, PBKDF2_ITERS, 256);
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();
        } catch (Exception e) {
            throw new RuntimeException("password hashing failed", e);
        }
    }

    // ---- HTTP reverse proxy to the private Django app ----------------------

    static void proxyToDjango(Socket client, InputStream cin, OutputStream cout,
                              String method, String target, Map<String,String> headers) throws IOException {
        // AUTH DISABLED FOR DEBUGGING: identity comes from the GM/Player toggle,
        // not a validated session. Re-enable the session lookup below to restore it.
        //   Account acc = currentAccount(headers);
        //   String role = acc != null ? acc.role : "anonymous";
        //   String user = acc != null ? acc.username : "anonymous";
        String role = debugRole(headers);
        String user = displayName(role);

        int contentLength = 0;
        try { contentLength = Integer.parseInt(headers.getOrDefault("content-length", "0")); } catch (Exception ignore) {}
        byte[] body = new byte[contentLength];
        if (contentLength > 0) readFully(cin, body);

        try (Socket up = new Socket(DJANGO_HOST, DJANGO_PORT)) {
            StringBuilder req = new StringBuilder();
            req.append(method).append(' ').append(target).append(" HTTP/1.1\r\n");
            req.append("Host: ").append(DJANGO_HOST).append(':').append(DJANGO_PORT).append("\r\n");
            req.append("Connection: close\r\n");
            for (Map.Entry<String,String> h : headers.entrySet()) {
                String k = h.getKey();
                if (k.equals("host") || k.equals("connection") || k.equals("content-length")) continue;
                req.append(k).append(": ").append(h.getValue()).append("\r\n");
            }
            if (contentLength > 0) req.append("Content-Length: ").append(contentLength).append("\r\n");
            // Identity injected at the security boundary, derived from the
            // validated session. DEMO: cleartext over localhost. REAL: HMAC-signed.
            req.append("X-EzVTT-Role: ").append(role).append("\r\n");
            req.append("X-EzVTT-User: ").append(user).append("\r\n");
            req.append("\r\n");

            OutputStream uo = up.getOutputStream();
            uo.write(req.toString().getBytes(StandardCharsets.ISO_8859_1));
            if (contentLength > 0) uo.write(body);
            uo.flush();

            up.getInputStream().transferTo(cout); // relay Django's response verbatim
            cout.flush();
        } finally {
            client.close();
        }
    }

    // ---- Live stats (runtime state Django doesn't have) --------------------

    static void serveStats(Socket client, OutputStream out) throws IOException {
        int gm = 0, players = 0;
        for (Client c : clients) {
            if (c.role.equals("gm")) gm++; else players++;
        }
        String json = "{\"online\":" + (gm + players)
                    + ",\"gm_online\":" + gm
                    + ",\"players_online\":" + players
                    + ",\"tokenX\":" + tokenX
                    + ",\"tokenY\":" + tokenY + "}";
        byte[] body = json.getBytes(StandardCharsets.UTF_8);
        String head = "HTTP/1.1 200 OK\r\n" +
                      "Content-Type: application/json\r\n" +
                      "Content-Length: " + body.length + "\r\n" +
                      "Connection: close\r\n\r\n";
        out.write(head.getBytes(StandardCharsets.ISO_8859_1));
        out.write(body);
        out.flush();
        client.close();
    }

    // ---- WebSocket hub (live shared play space) ----------------------------

    static void doWebSocket(Socket socket, InputStream in, OutputStream out,
                            Map<String,String> headers, String target) throws Exception {
        String key = headers.get("sec-websocket-key");
        if (key == null) { socket.close(); return; }
        String accept = Base64.getEncoder().encodeToString(
            MessageDigest.getInstance("SHA-1").digest((key + WS_GUID).getBytes(StandardCharsets.UTF_8)));
        out.write(("HTTP/1.1 101 Switching Protocols\r\n" +
                   "Upgrade: websocket\r\n" +
                   "Connection: Upgrade\r\n" +
                   "Sec-WebSocket-Accept: " + accept + "\r\n\r\n").getBytes(StandardCharsets.ISO_8859_1));
        out.flush();

        // AUTH DISABLED FOR DEBUGGING: role comes from the GM/Player toggle cookie.
        //   Account acc = currentAccount(headers);
        //   String role = acc != null ? acc.role : "anonymous";
        //   String user = acc != null ? acc.username : "anonymous";
        String role = debugRole(headers);
        String user = displayName(role);

        Client client = new Client(out, role, user);
        clients.add(client);
        System.out.println("WS connect: " + user + " (role=" + role + ", " + clients.size() + " online)");
        client.send("role " + role);
        client.send("map " + cols + " " + rows + " " + mapMsg());
        client.send("token " + tokenMsg());
        client.send("state " + tokenX + " " + tokenY);

        DataInputStream dis = new DataInputStream(in);
        try {
            String msg;
            while ((msg = readFrame(dis)) != null) handleMessage(client, msg);
        } catch (IOException disconnected) {
            // fall through to cleanup
        } finally {
            clients.remove(client);
            try { socket.close(); } catch (IOException ignore) {}
            System.out.println("WS close:   " + user + " (" + clients.size() + " online)");
        }
    }

    static void handleMessage(Client c, String msg) {
        if (msg.isEmpty()) return;
        String[] p = msg.split(" ");

        // ---- Party chat: any logged-in user may talk; everyone sees it. ----
        if (p[0].equals("chat")) {
            if (c.role.equals("anonymous")) { c.send("denied please log in to chat"); return; }
            String text = sanitizeChat(msg.length() > 5 ? msg.substring(5) : "");
            if (!text.isEmpty()) broadcast("chat\t" + c.user + "\t" + text);
            return;
        }
        // ---- Dice rolls: rolled SERVER-side so everyone sees one result. ----
        if (p[0].equals("roll")) {
            if (c.role.equals("anonymous")) { c.send("denied please log in to roll"); return; }
            if (p.length >= 2) rollDice(c, p[1]);
            return;
        }

        // Authorization is enforced at the server for every mutation, never
        // trusting the client UI.
        if (p[0].equals("move") && p.length >= 3) {
            if (!c.role.equals("gm")) { c.send("denied only the GM may move tokens"); return; }
            int x, y;
            try { x = Integer.parseInt(p[1]); y = Integer.parseInt(p[2]); }
            catch (NumberFormatException bad) { return; }
            if (x < 0 || y < 0 || x >= cols || y >= rows) return;   // stay on the board
            tokenX = x; tokenY = y;
            broadcast("state " + tokenX + " " + tokenY);

        } else if (p[0].equals("setmap") && p.length >= 4) {
            // GM picks the displayed battlemap and the grid fitted to it.
            if (!c.role.equals("gm")) { c.send("denied only the GM may change the map"); return; }
            int nc, nr;
            try { nc = Integer.parseInt(p[1]); nr = Integer.parseInt(p[2]); }
            catch (NumberFormatException bad) { return; }
            if (nc < 1 || nc > 100 || nr < 1 || nr > 100) return;
            String url = p[3].equals("-") ? "" : p[3];
            if (!url.isEmpty() && !validMediaUrl(url)) { c.send("denied invalid map url"); return; }
            cols = nc; rows = nr; mapUrl = url;
            if (tokenX >= cols) tokenX = cols - 1;   // keep token within the new grid
            if (tokenY >= rows) tokenY = rows - 1;
            broadcast("map " + cols + " " + rows + " " + mapMsg());
            broadcast("state " + tokenX + " " + tokenY);

        } else if (p[0].equals("settoken") && p.length >= 2) {
            // GM picks the token image shown on the board.
            if (!c.role.equals("gm")) { c.send("denied only the GM may change the token"); return; }
            String url = p[1].equals("-") ? "" : p[1];
            if (!url.isEmpty() && !validMediaUrl(url)) { c.send("denied invalid token url"); return; }
            tokenUrl = url;
            broadcast("token " + tokenMsg());
        }
    }

    /** Active map/token as a single WS token ("-" means none, so the field is never empty). */
    static String mapMsg()   { return mapUrl.isEmpty()   ? "-" : mapUrl; }
    static String tokenMsg() { return tokenUrl.isEmpty() ? "-" : tokenUrl; }

    /** Strip control chars (incl. the \t we use as a wire delimiter) and cap length. */
    static String sanitizeChat(String s) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < s.length() && b.length() < 500; i++) {
            char ch = s.charAt(i);
            if (ch == '\t' || ch == '\r' || ch == '\n') ch = ' ';
            if (ch < 0x20) continue;            // drop remaining control chars
            b.append(ch);
        }
        return b.toString().trim();
    }

    // Standard dice notation: [N]dM[+/-K], e.g. "d20", "2d6", "1d8+3".
    static final java.util.regex.Pattern DICE =
        java.util.regex.Pattern.compile("(\\d{0,3})d(\\d{1,4})([+-]\\d{1,4})?");

    /** Roll dice server-side and broadcast the result as a chat-style line. */
    static void rollDice(Client c, String notation) {
        java.util.regex.Matcher m = DICE.matcher(notation);
        if (!m.matches()) { c.send("denied bad dice notation"); return; }
        int n     = m.group(1).isEmpty() ? 1 : Integer.parseInt(m.group(1));
        int sides = Integer.parseInt(m.group(2));
        int mod   = m.group(3) == null ? 0 : Integer.parseInt(m.group(3));
        if (n < 1 || n > 100 || sides < 2 || sides > 1000) { c.send("denied dice out of range"); return; }

        int total = mod;
        StringBuilder detail = new StringBuilder();
        for (int i = 0; i < n; i++) {
            int r = RNG.nextInt(sides) + 1;
            total += r;
            if (i > 0) detail.append(',');
            detail.append(r);
        }
        if (mod != 0) detail.append(mod > 0 ? " +" + mod : " " + mod);
        broadcast("roll\t" + c.user + "\t" + notation + "\t" + total + "\t" + detail);
    }

    /** A media URL safe to broadcast: must point at our own /media/ space, no traversal. */
    static boolean validMediaUrl(String u) {
        if (u == null || u.isEmpty() || u.length() > 512) return false;
        if (!u.startsWith("/media/") || u.contains("..")) return false;
        for (int i = 0; i < u.length(); i++) {
            char ch = u.charAt(i);
            if (!(Character.isLetterOrDigit(ch) || "/._%-".indexOf(ch) >= 0)) return false;
        }
        return true;
    }

    static void broadcast(String msg) {
        for (Client c : clients) c.send(msg);
    }

    /** Reads one client frame; returns text payload, or null on close/EOF. */
    static String readFrame(DataInputStream in) throws IOException {
        int b1 = in.readUnsignedByte();
        int opcode = b1 & 0x0F;
        int b2 = in.readUnsignedByte();
        boolean masked = (b2 & 0x80) != 0;
        long len = b2 & 0x7F;
        if (len == 126)      len = in.readUnsignedShort();
        else if (len == 127) len = in.readLong();
        byte[] mask = new byte[4];
        if (masked) in.readFully(mask);
        byte[] payload = new byte[(int) len];
        in.readFully(payload);
        if (masked) for (int i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
        if (opcode == 0x8) return null;                 // close
        if (opcode == 0x1) return new String(payload, StandardCharsets.UTF_8); // text
        return "";                                       // ping/pong/etc -> ignore
    }

    /** One connected browser. send() writes an unmasked server text frame. */
    static class Client {
        final OutputStream out;
        final String role;
        final String user;
        Client(OutputStream out, String role, String user) { this.out = out; this.role = role; this.user = user; }
        synchronized void send(String msg) {
            try {
                byte[] p = msg.getBytes(StandardCharsets.UTF_8);
                out.write(0x81); // FIN + text
                if (p.length < 126) {
                    out.write(p.length);
                } else if (p.length < 65536) {
                    out.write(126);
                    out.write((p.length >>> 8) & 0xFF);
                    out.write(p.length & 0xFF);
                } else {
                    out.write(127);
                    for (int i = 7; i >= 0; i--) out.write((int) (((long) p.length >>> (8 * i)) & 0xFF));
                }
                out.write(p);
                out.flush();
            } catch (IOException dropped) {
                // read loop will detect and clean up
            }
        }
    }

    // ---- small helpers -----------------------------------------------------

    static String readHead(InputStream in) throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        int b;
        while ((b = in.read()) != -1) {
            buf.write(b);
            int n = buf.size();
            if (n >= 4) {
                byte[] a = buf.toByteArray();
                if (a[n-4]=='\r' && a[n-3]=='\n' && a[n-2]=='\r' && a[n-1]=='\n') break;
            }
        }
        return buf.toString(StandardCharsets.ISO_8859_1);
    }

    static Map<String,String> parseHeaders(String head) {
        Map<String,String> m = new LinkedHashMap<>();
        String[] lines = head.split("\r\n");
        for (int i = 1; i < lines.length; i++) {
            int c = lines[i].indexOf(':');
            if (c > 0) m.put(lines[i].substring(0, c).trim().toLowerCase(), lines[i].substring(c + 1).trim());
        }
        return m;
    }

    /** Value of a named cookie from the request's Cookie header, or null. */
    static String cookie(Map<String,String> headers, String name) {
        String header = headers.get("cookie");
        if (header == null) return null;
        for (String part : header.split(";")) {
            String s = part.trim();
            if (s.startsWith(name + "=")) return s.substring(name.length() + 1);
        }
        return null;
    }

    // ---- Debug role toggle (stand-in while AUTH is DISABLED) ---------------

    /** Current debug role from the EzVTT_DEBUG_ROLE cookie; defaults to gm. */
    static String debugRole(Map<String,String> headers) {
        String r = cookie(headers, "EzVTT_DEBUG_ROLE");
        if ("player".equals(r))    return "player";
        if ("anonymous".equals(r)) return "anonymous";   // "not logged into anyone"
        return "gm";   // default
    }

    /** Display name shown for a debug role. */
    static String displayName(String role) {
        return switch (role) {
            case "gm"     -> "Gary";
            case "player" -> "Player";
            default        -> "anonymous";
        };
    }

    /** /debug/role?set=gm|player|anonymous&next=/path -> set the toggle cookie and redirect back. */
    static void handleDebugRole(Socket client, OutputStream out, String target) throws IOException {
        Map<String,String> q = queryParams(target);
        String set = q.getOrDefault("set", "gm");
        if (!set.equals("player") && !set.equals("anonymous")) set = "gm";
        String next = q.getOrDefault("next", "/");
        if (!next.startsWith("/")) next = "/";          // only allow local redirects
        redirect(client, out, next, "EzVTT_DEBUG_ROLE=" + set);
    }

    static Map<String,String> queryParams(String target) {
        Map<String,String> m = new HashMap<>();
        int q = target.indexOf('?');
        if (q < 0) return m;
        for (String kv : target.substring(q + 1).split("&")) {
            int e = kv.indexOf('=');
            if (e >= 0) m.put(kv.substring(0, e), URLDecoder.decode(kv.substring(e + 1), StandardCharsets.UTF_8));
        }
        return m;
    }

    /** Parse an application/x-www-form-urlencoded request body. */
    static Map<String,String> readForm(InputStream in, Map<String,String> headers) throws IOException {
        int len = 0;
        try { len = Integer.parseInt(headers.getOrDefault("content-length", "0")); } catch (Exception ignore) {}
        byte[] body = new byte[len];
        if (len > 0) readFully(in, body);
        Map<String,String> form = new HashMap<>();
        for (String kv : new String(body, StandardCharsets.UTF_8).split("&")) {
            if (kv.isEmpty()) continue;
            int e = kv.indexOf('=');
            String k = e >= 0 ? kv.substring(0, e) : kv;
            String v = e >= 0 ? kv.substring(e + 1) : "";
            form.put(URLDecoder.decode(k, StandardCharsets.UTF_8),
                     URLDecoder.decode(v, StandardCharsets.UTF_8));
        }
        return form;
    }

    /** 302 redirect, optionally setting a session cookie (HttpOnly, SameSite=Lax). */
    static void redirect(Socket client, OutputStream out, String location, String cookie) throws IOException {
        StringBuilder sb = new StringBuilder();
        sb.append("HTTP/1.1 302 Found\r\n");
        sb.append("Location: ").append(location).append("\r\n");
        if (cookie != null) {
            // NOTE: add "; Secure" once served over TLS so the cookie is HTTPS-only.
            sb.append("Set-Cookie: ").append(cookie).append("; Path=/; HttpOnly; SameSite=Lax\r\n");
        }
        sb.append("Content-Length: 0\r\nConnection: close\r\n\r\n");
        out.write(sb.toString().getBytes(StandardCharsets.ISO_8859_1));
        out.flush();
        client.close();
    }

    static void readFully(InputStream in, byte[] b) throws IOException {
        int off = 0;
        while (off < b.length) {
            int r = in.read(b, off, b.length - off);
            if (r < 0) throw new EOFException();
            off += r;
        }
    }
}

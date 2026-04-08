#!/usr/bin/env python3
"""Dev server: serves frontend/ locally and proxies data paths to upstream.

Serves index.html / app.js / style.css / manifest.json overrides from the
local frontend/ directory. Transparently proxies /manifest.json, /videos/*,
/thumbs/*, /covers/*, and /healthz to a configured upstream, injecting
HTTP Basic Auth.

Config file (KEY=VALUE, one per line — keeps the upstream URL and credentials
out of the repo) is looked up in this order:
  1. $FAMILY_VIDEOS_AUTH_FILE
  2. ~/.config/family-videos/dev-auth
  3. <repo>/.claude/dev-auth (fallback for repo-local setups)

Required keys: UPSTREAM (e.g. https://example.com), AUTH (user:password).
Lines starting with # and blank lines are ignored.

Path-independent: always serves the frontend/ directory next to this script's
repo root, so it works from any worktree regardless of CWD.
"""

import base64
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Exact-match paths (full path must equal, or equal-then-query).
PROXIED_EXACT = ("/manifest.json", "/healthz")
# Prefix-match paths (must start with, trailing slash enforces the boundary).
PROXIED_PREFIXES = ("/videos/", "/thumbs/", "/covers/")
# Hop-by-hop headers we strip on both directions (RFC 7230 §6.1).
# Set-Cookie is also stripped — the upstream should not be setting cookies
# on 127.0.0.1:8765, and leaking them here can collide with other dev tools.
# Date and Server are also stripped — BaseHTTPRequestHandler injects its own,
# and relaying upstream's produces duplicate headers.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
    "set-cookie", "set-cookie2",
    "date", "server",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"


def _parse_config(text: str) -> dict:
    """Parse a simple KEY=VALUE config file. Ignores blanks and # comments."""
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


def load_config() -> tuple[str, str]:
    """Return (upstream_url, authorization_header) from the config file."""
    candidates = []
    env_path = os.environ.get("FAMILY_VIDEOS_AUTH_FILE")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path.home() / ".config" / "family-videos" / "dev-auth")
    candidates.append(REPO_ROOT / ".claude" / "dev-auth")

    usage = (
        "Expected format (KEY=VALUE, one per line):\n"
        "  UPSTREAM=https://your-host.example.com\n"
        "  AUTH=username:password\n"
    )

    for path in candidates:
        try:
            # utf-8-sig tolerates a BOM from GUI editors (Notepad, TextEdit).
            raw = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            continue
        except OSError as e:
            print(f"[dev-proxy] could not read {path}: {e}", file=sys.stderr)
            continue
        cfg = _parse_config(raw)
        upstream = cfg.get("UPSTREAM", "").rstrip("/")
        auth = cfg.get("AUTH", "")
        if not upstream or not auth or ":" not in auth:
            print(
                f"[dev-proxy] {path} is missing UPSTREAM or AUTH.\n{usage}",
                file=sys.stderr,
            )
            continue
        if not (upstream.startswith("http://") or upstream.startswith("https://")):
            print(
                f"[dev-proxy] {path}: UPSTREAM must start with http:// or https://",
                file=sys.stderr,
            )
            continue
        token = base64.b64encode(auth.encode("utf-8")).decode("ascii")
        print(f"[dev-proxy] loaded config from {path}")
        return upstream, "Basic " + token

    tried = "\n  ".join(str(p) for p in candidates)
    sys.exit(
        "[dev-proxy] no config file found. Create one with:\n"
        "  mkdir -p ~/.config/family-videos\n"
        "  cat > ~/.config/family-videos/dev-auth <<'EOF'\n"
        "  UPSTREAM=https://your-host.example.com\n"
        "  AUTH=username:password\n"
        "  EOF\n"
        f"Searched:\n  {tried}"
    )


UPSTREAM, AUTH_HEADER = load_config()

# Warn (don't block) if UPSTREAM is http:// — the Basic credential would
# then travel in cleartext. Operator may intentionally want this for a
# local test server, so we don't hard-fail.
if UPSTREAM.startswith("http://"):
    print(
        "[dev-proxy] WARNING: UPSTREAM is http:// — credentials will be "
        "sent in cleartext over the network.",
        file=sys.stderr,
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Don't follow redirects — pass 30x responses through to the client.

    urllib's default redirect handler re-uses the original request headers,
    including the injected Authorization. A misconfigured or compromised
    upstream could 302 to an attacker-controlled host and receive our Basic
    credential. Returning None from these methods makes urllib raise the
    original HTTPError, which _proxy() relays through to the client.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Build an opener that:
#  (a) ignores http_proxy / https_proxy env vars — we don't want the
#      operator's corporate proxy to silently intercept upstream traffic
#      and see the injected credential.
#  (b) refuses to follow redirects — see _NoRedirectHandler above.
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirectHandler(),
)


class DevProxyHandler(SimpleHTTPRequestHandler):
    # Serve frontend/ regardless of CWD.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def log_message(self, fmt, *args):
        sys.stderr.write("[dev-proxy] %s - %s\n" % (self.address_string(), fmt % args))

    def _is_proxied(self) -> bool:
        # Strip the query string for path matching, then require either an
        # exact hit on a bare path or a prefix hit on a directory prefix.
        # Path traversal check is performed on the URL-decoded path so
        # %2e%2e encodings can't smuggle '..' past the check.
        raw = self.path.split("?", 1)[0]
        decoded = urllib.parse.unquote(raw)
        if ".." in decoded.split("/"):
            return False
        if raw in PROXIED_EXACT:
            return True
        return raw.startswith(PROXIED_PREFIXES)

    def do_GET(self):
        if self._is_proxied():
            self._proxy("GET")
        else:
            super().do_GET()

    def do_HEAD(self):
        if self._is_proxied():
            self._proxy("HEAD")
        else:
            super().do_HEAD()

    def _proxy(self, method: str):
        url = UPSTREAM + self.path
        req_headers = {"Authorization": AUTH_HEADER}
        # Forward Range so video seeking works.
        for name in ("Range", "If-None-Match", "If-Modified-Since", "Accept"):
            val = self.headers.get(name)
            if val:
                req_headers[name] = val
        try:
            req = urllib.request.Request(url, method=method, headers=req_headers)
            with _OPENER.open(req, timeout=60) as resp:
                self._relay(resp, method)
        except urllib.error.HTTPError as e:
            # Upstream non-2xx (includes 30x — we refuse redirects). Relay
            # status + body so the frontend sees it verbatim.
            self._relay(e, method)
        except urllib.error.URLError as e:
            # Reason may reference the upstream host — scrub before sending.
            self.send_error(502, "Upstream error")
            sys.stderr.write(f"[dev-proxy] upstream URLError: {e.reason}\n")
        except (TimeoutError, socket.timeout):
            # socket.timeout is an alias for TimeoutError on 3.10+, but a
            # distinct class on 3.9. Catch both for the lifetime of 3.9.
            self.send_error(504, "Upstream timeout")
        except Exception as e:
            # Anything else (ValueError from malformed URL, OSError from
            # socket issues, etc.) — don't let the traceback leak UPSTREAM
            # through the default BaseHTTPRequestHandler error handler.
            self.send_error(502, "Upstream error")
            sys.stderr.write(f"[dev-proxy] upstream error: {type(e).__name__}\n")

    def _relay(self, resp, method: str):
        self.send_response(resp.status if hasattr(resp, "status") else resp.code)
        for key, val in resp.headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            self.send_header(key, val)
        self.end_headers()
        if method != "HEAD":
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return


def main():
    host = "127.0.0.1"
    port = int(os.environ.get("DEV_PROXY_PORT", "8765"))
    if not FRONTEND_DIR.is_dir():
        sys.exit(f"[dev-proxy] frontend directory not found: {FRONTEND_DIR}")
    try:
        server = ThreadingHTTPServer((host, port), DevProxyHandler)
    except OSError as e:
        sys.exit(
            f"[dev-proxy] could not bind {host}:{port}: {e.strerror or e}. "
            "Is another dev server already running on this port?"
        )
    proxied = ", ".join(PROXIED_EXACT + PROXIED_PREFIXES)
    print(f"[dev-proxy] serving {FRONTEND_DIR} on http://{host}:{port}")
    print(f"[dev-proxy] proxying {proxied} -> {UPSTREAM}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dev-proxy] shutting down", file=sys.stderr)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

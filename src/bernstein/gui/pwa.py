"""PWA (Progressive Web App) helpers for the Bernstein operator GUI.

This module ships the four assets needed to turn the Vite SPA into an
installable PWA:

* a web app manifest (``manifest.webmanifest``)
* a service worker (``sw.js``) using a stale-while-revalidate strategy
* an offline fallback page (``offline.html``)
* programmatically-rendered app icons (PNG, served from the registered
  icon endpoints)

It also exposes:

* :func:`new_auth_token` - generate a short-lived, URL-safe bearer token
  for the QR onboarding flow
* :func:`new_passphrase` - generate a 6-word diceware passphrase from a
  small embedded wordlist
* :func:`build_manifest` - pure function returning the manifest dict (so
  it is easy to snapshot-test)

The helpers stay pure: no FastAPI imports, no network. The mount-time
wiring lives in :mod:`bernstein.gui`.
"""

from __future__ import annotations

import secrets
import struct
import zlib
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default PWA application name (shown under the home-screen icon).
APP_NAME = "Bernstein"

#: Default short name (used when the home-screen has limited width).
APP_SHORT_NAME = "Bernstein"

#: Default theme colour (matches the SPA's dark chrome).
THEME_COLOR = "#111111"

#: Default background colour for the splash screen.
BACKGROUND_COLOR = "#111111"

#: Manifest start URL - opens directly into the operator dashboard.
START_URL = "/ui/"

#: Manifest scope - every page below ``/ui/`` is part of the app.
SCOPE = "/ui/"

#: Default auth token byte length. 32 random bytes -> ~43-char URL-safe
#: token (per :func:`secrets.token_urlsafe`).
AUTH_TOKEN_BYTES = 32

#: Default passphrase word count. Six words from a 256-word list yields
#: 48 bits of entropy - comparable to a strong human-readable password
#: and trivially typeable on a phone keyboard.
PASSPHRASE_WORDS = 6

#: Embedded mini-diceware wordlist. Kept short and lowercase ASCII so
#: the passphrase types cleanly on any mobile keyboard without shifting
#: or autocorrect surprises. 256 entries (8 bits per word).
DICEWARE_WORDS: tuple[str, ...] = (
    "amber",
    "anchor",
    "apple",
    "arrow",
    "atlas",
    "aurora",
    "autumn",
    "azure",
    "bacon",
    "badge",
    "bagel",
    "bamboo",
    "banjo",
    "basil",
    "beacon",
    "bear",
    "berry",
    "birch",
    "bison",
    "blaze",
    "bloom",
    "blossom",
    "bolt",
    "bonus",
    "boots",
    "border",
    "bossa",
    "boulder",
    "bowtie",
    "branch",
    "brass",
    "bravo",
    "breeze",
    "bridge",
    "bright",
    "bronze",
    "brook",
    "buffer",
    "bugle",
    "bumper",
    "bundle",
    "butter",
    "cabin",
    "cable",
    "cactus",
    "candle",
    "canoe",
    "canyon",
    "carbon",
    "carrot",
    "castle",
    "catnip",
    "cedar",
    "cello",
    "champ",
    "chant",
    "cherry",
    "chess",
    "chime",
    "circle",
    "citrus",
    "clamp",
    "clay",
    "clever",
    "cliff",
    "clover",
    "cobalt",
    "cocoa",
    "comet",
    "coral",
    "cosmic",
    "cotton",
    "coyote",
    "cozy",
    "crane",
    "creek",
    "crisp",
    "cronos",
    "crown",
    "crystal",
    "cubic",
    "cumin",
    "cypress",
    "daisy",
    "dawn",
    "delta",
    "diamond",
    "dolphin",
    "donut",
    "drift",
    "dunes",
    "dynamo",
    "eagle",
    "earth",
    "ebony",
    "echo",
    "eclipse",
    "edge",
    "elder",
    "elf",
    "ember",
    "emerald",
    "ember2",
    "epoch",
    "ether",
    "etna",
    "evening",
    "fable",
    "falcon",
    "felt",
    "fern",
    "fiber",
    "fiddle",
    "field",
    "finch",
    "firefly",
    "fjord",
    "flame",
    "flax",
    "fleece",
    "flint",
    "flora",
    "foam",
    "fog",
    "forest",
    "forge",
    "fox",
    "frost",
    "galaxy",
    "garnet",
    "geyser",
    "ginger",
    "glacier",
    "glade",
    "glass",
    "glide",
    "glow",
    "gnome",
    "golden",
    "granite",
    "grape",
    "graphite",
    "grasp",
    "grasshop",
    "gravel",
    "grizzly",
    "groove",
    "harbor",
    "harvest",
    "haven",
    "hawk",
    "hazel",
    "helix",
    "heron",
    "hibis",
    "hickory",
    "hill",
    "honey",
    "horizon",
    "hum",
    "iceberg",
    "indigo",
    "iris",
    "ivy",
    "jade",
    "jasmine",
    "jazz",
    "jewel",
    "juniper",
    "kayak",
    "kelp",
    "kestrel",
    "kettle",
    "knight",
    "koi",
    "lake",
    "lantern",
    "lattice",
    "lava",
    "lemon",
    "leopard",
    "lily",
    "linden",
    "lion",
    "lizard",
    "lobster",
    "locust",
    "loop",
    "lotus",
    "lumen",
    "lumber",
    "lupine",
    "lyric",
    "macaw",
    "mango",
    "maple",
    "marble",
    "mark",
    "marlin",
    "marmot",
    "meadow",
    "melody",
    "merlin",
    "mesa",
    "metric",
    "mica",
    "midnight",
    "millet",
    "mint",
    "mist",
    "moon",
    "moose",
    "mosaic",
    "mountain",
    "mulberry",
    "muse",
    "nebula",
    "needle",
    "nest",
    "nettle",
    "newt",
    "nimbus",
    "noble",
    "nomad",
    "north",
    "nova",
    "nutmeg",
    "oak",
    "oasis",
    "ocean",
    "ochre",
    "ocelot",
    "olive",
    "onyx",
    "opal",
    "orca",
    "orchid",
    "orion",
    "otter",
    "owl",
    "oxide",
    "paddle",
    "panda",
    "pansy",
    "papaya",
    "parchment",
    "parrot",
    "pearl",
    "pebble",
    "penguin",
    "petunia",
    "pewter",
    "phoenix",
    "pine",
    "pioneer",
    "plover",
    "plum",
    "polar",
    "poplar",
    "poppy",
    "prairie",
    "prism",
    "puffin",
    "quartz",
)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(
    *,
    name: str = APP_NAME,
    short_name: str = APP_SHORT_NAME,
    theme_color: str = THEME_COLOR,
    background_color: str = BACKGROUND_COLOR,
    start_url: str = START_URL,
    scope: str = SCOPE,
) -> dict[str, Any]:
    """Return a deterministic web app manifest dict.

    The dict is stable across calls with the same arguments so it can be
    snapshot-tested with :mod:`syrupy`.

    Args:
        name: Application name.
        short_name: Short name shown under the home-screen icon.
        theme_color: Browser-chrome theme colour.
        background_color: Splash-screen background colour.
        start_url: URL the app opens on launch.
        scope: URL prefix that belongs to the app.

    Returns:
        A dict suitable for :func:`json.dumps`.
    """
    return {
        "name": name,
        "short_name": short_name,
        "description": "Bernstein operator dashboard - installable PWA.",
        "start_url": start_url,
        "scope": scope,
        "display": "standalone",
        "orientation": "any",
        "background_color": background_color,
        "theme_color": theme_color,
        "categories": ["developer", "productivity"],
        "lang": "en",
        "icons": [
            {
                "src": "/ui/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/ui/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Service worker source
# ---------------------------------------------------------------------------

# The service worker is deliberately small and dependency-free. It pre-caches
# the SPA shell on install, falls back to network for everything else, and
# serves the offline page when the network is unreachable. ``/api/projects``
# and ``/api/cost`` are cached stale-while-revalidate so the last fleet
# snapshot is available on a flaky connection.

SERVICE_WORKER_JS = """\
/* Bernstein PWA service worker.
 *
 * Cache strategy:
 *   - SHELL_CACHE       : pre-cached on install (index.html + offline.html
 *                         + manifest + icons). Cleared on activate when the
 *                         cache version bumps.
 *   - RUNTIME_CACHE     : populated lazily for /api/projects and /api/cost
 *                         on every successful network response (stale-while-
 *                         revalidate); served from cache on offline failure.
 *
 * The version string is bumped by build pipelines (or hand-edited) when the
 * shell changes. Bumping forces a full re-cache on next activation.
 */
const VERSION = 'bernstein-pwa-v1';
const SHELL_CACHE = VERSION + '-shell';
const RUNTIME_CACHE = VERSION + '-runtime';

const SHELL_ASSETS = [
  '/ui/',
  '/ui/index.html',
  '/ui/offline.html',
  '/ui/manifest.webmanifest',
  '/ui/icon-192.png',
  '/ui/icon-512.png',
];

const RUNTIME_PATHS = ['/api/projects', '/api/cost', '/api/v1/projects', '/api/v1/cost'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS)).then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))),
    ).then(() => self.clients.claim()),
  );
});

function isRuntimePath(url) {
  for (const p of RUNTIME_PATHS) {
    if (url.pathname === p || url.pathname.startsWith(p + '/')) return true;
  }
  return false;
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // Navigation requests: try network first, fall back to cached shell, then
  // to the offline page. This keeps the SPA fresh when online without
  // breaking it when the link is down.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() =>
        caches.match('/ui/index.html').then((cached) => cached || caches.match('/ui/offline.html')),
      ),
    );
    return;
  }

  // Stale-while-revalidate for the small set of read-only API endpoints
  // the operator wants to glance at offline.
  if (isRuntimePath(url)) {
    event.respondWith(
      caches.open(RUNTIME_CACHE).then((cache) =>
        cache.match(req).then((cached) => {
          const network = fetch(req).then((resp) => {
            if (resp && resp.status === 200) cache.put(req, resp.clone());
            return resp;
          }).catch(() => cached);
          return cached || network;
        }),
      ),
    );
    return;
  }

  // Default: cache-first for shell assets, network for everything else.
  event.respondWith(
    caches.match(req).then((cached) => cached || fetch(req).catch(() => caches.match('/ui/offline.html'))),
  );
});
"""


OFFLINE_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Offline - Bernstein</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; padding: 0; height: 100%; background: #111; color: #eaeaea;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  body { display: grid; place-items: center; padding: 1.5rem; }
  .card { max-width: 28rem; border: 1px solid #2a2a2a; border-radius: 12px;
    background: #161616; padding: 1.5rem 1.75rem; }
  h1 { font-size: 1.25rem; margin: 0 0 .5rem 0; }
  p { margin: .5rem 0 0 0; color: #b8b8b8; line-height: 1.45; }
  .meta { margin-top: 1.25rem; font-size: .8rem; color: #888;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  button { margin-top: 1.25rem; appearance: none; border: 1px solid #3a3a3a;
    background: #1f1f1f; color: #eaeaea; padding: .55rem .9rem; border-radius: 8px;
    font: inherit; cursor: pointer; }
  button:hover { background: #262626; }
</style>
</head>
<body>
  <main class="card" role="alert">
    <h1>Offline</h1>
    <p>Bernstein could not reach the orchestrator. Your last cached fleet snapshot is still available.
       Open the app to view it.</p>
    <div class="meta">bernstein-pwa-v1</div>
    <button onclick="location.reload()">Retry</button>
  </main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Icon generation
# ---------------------------------------------------------------------------


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    """Build a single PNG chunk (length + tag + data + CRC32)."""
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def render_icon_png(
    size: int,
    *,
    bg: tuple[int, int, int] = (17, 17, 17),
    fg: tuple[int, int, int] = (234, 234, 234),
) -> bytes:
    """Render a flat square PNG icon of ``size`` x ``size`` pixels.

    The icon is a solid background square with a centered "B" mark drawn
    as a simple geometric glyph (two stacked half-bowls). Output is a
    valid uncompressed-friendly PNG using zlib deflate.

    Args:
        size: Side length in pixels. Must be a positive integer.
        bg: Background RGB triple.
        fg: Foreground (glyph) RGB triple.

    Returns:
        Raw PNG file bytes.

    Raises:
        ValueError: If ``size`` is not positive.
    """
    if size <= 0:
        raise ValueError("Icon size must be a positive integer")

    # Pixel matrix: row-major, each row prefixed with a filter byte (0).
    # We draw a simple B-mark composed of a vertical stem and two half-
    # bowls. The geometry is approximated in pixel space so it scales
    # cleanly to both 192 and 512.
    stem_x0 = int(size * 0.32)
    stem_x1 = int(size * 0.42)
    bowl_x1 = int(size * 0.66)
    top_y0 = int(size * 0.22)
    mid_y = int(size * 0.50)
    bot_y1 = int(size * 0.78)
    bowl_thickness = max(1, int(size * 0.10))

    half_band = bowl_thickness // 2
    mid_band_lo = mid_y - half_band
    mid_band_hi = mid_y + half_band
    top_band_hi = top_y0 + bowl_thickness
    bot_band_lo = bot_y1 - bowl_thickness

    rows: list[bytes] = []
    for y in range(size):
        row = bytearray()
        row.append(0)  # PNG filter byte: None
        for x in range(size):
            in_stem = stem_x0 <= x < stem_x1 and top_y0 <= y < bot_y1
            in_top_band = top_y0 <= y < top_band_hi or mid_band_lo <= y < mid_band_hi
            top_outer = stem_x1 <= x < bowl_x1 and in_top_band
            top_side = bowl_x1 - bowl_thickness <= x < bowl_x1 and top_y0 <= y < mid_band_hi
            in_bot_band = mid_band_lo <= y < mid_band_hi or bot_band_lo <= y < bot_y1
            bot_outer = stem_x1 <= x < bowl_x1 and in_bot_band
            bot_side = bowl_x1 - bowl_thickness <= x < bowl_x1 and mid_band_lo <= y < bot_y1
            on_glyph = in_stem or top_outer or top_side or bot_outer or bot_side
            r, g, b = fg if on_glyph else bg
            row.extend((r, g, b))
        rows.append(bytes(row))

    raw = b"".join(rows)
    compressed = zlib.compress(raw, level=6)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", compressed) + _png_chunk(b"IEND", b"")


# ---------------------------------------------------------------------------
# Auth token + passphrase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthIssue:
    """A freshly-minted onboarding credential pair.

    Attributes:
        token: Long URL-safe bearer token (placed in the QR URL fragment).
        passphrase: Short, human-typeable diceware passphrase.
    """

    token: str
    passphrase: str


def new_auth_token(*, nbytes: int = AUTH_TOKEN_BYTES) -> str:
    """Generate a URL-safe bearer token.

    Args:
        nbytes: Number of random bytes to draw. Must be positive.

    Returns:
        A URL-safe token of approximately ``ceil(nbytes * 4/3)`` chars.

    Raises:
        ValueError: If ``nbytes`` is not positive.
    """
    if nbytes <= 0:
        raise ValueError("nbytes must be a positive integer")
    return secrets.token_urlsafe(nbytes)


def new_passphrase(*, words: int = PASSPHRASE_WORDS, wordlist: tuple[str, ...] = DICEWARE_WORDS) -> str:
    """Generate a diceware-style passphrase.

    Args:
        words: Number of words. Must be positive.
        wordlist: Pool to draw from. Must be non-empty.

    Returns:
        Hyphen-joined lowercase passphrase (e.g. ``"amber-bridge-cedar-..."``).

    Raises:
        ValueError: If ``words`` is not positive or ``wordlist`` is empty.
    """
    if words <= 0:
        raise ValueError("words must be a positive integer")
    if not wordlist:
        raise ValueError("wordlist must be non-empty")
    picks = [secrets.choice(wordlist) for _ in range(words)]
    return "-".join(picks)


def new_auth_issue(*, nbytes: int = AUTH_TOKEN_BYTES, words: int = PASSPHRASE_WORDS) -> AuthIssue:
    """Generate a paired auth token + passphrase.

    Args:
        nbytes: Token random byte length.
        words: Passphrase word count.

    Returns:
        An :class:`AuthIssue` with both credentials.
    """
    return AuthIssue(token=new_auth_token(nbytes=nbytes), passphrase=new_passphrase(words=words))


# ---------------------------------------------------------------------------
# URL composition
# ---------------------------------------------------------------------------


def compose_onboarding_url(base_url: str, token: str) -> str:
    """Compose the operator-facing QR URL.

    The token is placed in the URL fragment (``#t=<token>``) so it never
    leaves the client and never appears in webserver access logs.

    Args:
        base_url: Tunnel-published base URL (e.g.
            ``https://abc.trycloudflare.com``).
        token: Auth token from :func:`new_auth_token`.

    Returns:
        Full onboarding URL with the token in the fragment.
    """
    base = base_url.rstrip("/")
    return f"{base}/ui/#t={token}"

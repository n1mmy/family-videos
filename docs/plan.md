# Family Videos Timeline App — Implementation Plan

## Context

~50-100 family DVDs (VHS origin) have been digitized to .iso files and extracted to .mkv via MakeMKV. Jellyfin was tried as a viewer but rejected — too complex for grandparents. This plan implements a dead-simple timeline web app: open a link, drag to a year, press play. Hosted on an existing home k8s cluster with Ceph storage and TLS already configured.

Design doc: `~/.gstack/projects/n1mmy-family-videos/nim-claude/adoring-haibt-design-20260407-095923.md`

## Eng Review Decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Auth | nginx `auth_basic` only (no custom page) |
| 2 | Storage | Ceph PVCs (existing) |
| 3 | Pipeline execution | k8s Job (matches existing iso→mkv pattern) |
| 4 | Metadata strategy | Auto-parse filenames + `overrides.json` for manual fixes |
| 5 | Timeline UX | Draggable scrubber + clickable year labels (dual input) |
| 6 | Transcode strategy | Always re-encode: h264 CRF 23, 480p, yadif deinterlace, AAC 128kbps, `-movflags +faststart`, normalize SAR to 1:1 (square pixels). No remux fast-path (simplifies code, ensures iPad Safari compatibility) |
| 7 | Thumbnails | Smart frame selection (sample multiple, pick highest variance) + DVD cover JPGs shown in UI |
| 8 | Sprites | Deferred to V2 |
| 9 | Pipeline resilience | Idempotent (skip existing) + error-resilient (log failures, continue batch) |
| 10 | Atomic publish | Pipeline writes to staging dir, swaps to served dir on completion |
| 11 | Cache headers | `immutable` for videos/thumbs, `no-cache` for manifest.json |
| 12 | Dates in schema | Nullable — unparseable filenames get `null` dates, shown in "Unknown Date" group |
| 13 | Tests | pytest for pipeline, manual frontend testing |
| 14 | TLS | Already handled on cluster |

## CEO Review Decisions

| # | Decision | Choice |
|---|----------|--------|
| 15 | Atomic publish mechanism | Symlink swap: staging is a real directory, served is a symlink. `mv` atomically renames the symlink. Copy existing served to staging first, then transcode new on top (fixes idempotency + atomicity conflict) |
| 16 | URL deep-linking | `#YYYY` for years, `#video-id` for individual videos. 4-digit = year, else video ID lookup. Auto-open player on video deep-link |
| 17 | DVD grouping UI | Group videos by `dvd` field with cover art header. Single-title DVDs show cover as secondary element |
| 18 | Keyboard navigation | Left/Right arrows for timeline years, Enter/Space to play, Escape to close. Focus returns to card after player close |
| 19 | Video density indicators | Proportional bars below year labels showing video count per year |
| 20 | Lazy thumbnails | IntersectionObserver with eager-load fallback for old Safari. Preconnect hints on hover for video URLs |
| 21 | Scrubber behavior | Discrete year snapping (not continuous). Debounce grid re-render by ~150ms |
| 22 | Pipeline parallelism | ProcessPoolExecutor with configurable worker count (default: CPU count, env var override). 48-core machine available |
| 23 | Per-title overrides | Extend overrides.json to support `"dvd-name/titleNN"` keys. Add `skip: true` field. Auto-filter titles < 60 seconds |
| 24 | Config externalization | overrides.json as k8s ConfigMap, .htpasswd as k8s Secret. No config baked into images |
| 25 | Manifest schema contract | manifest.schema.json (JSON Schema) file. Pipeline validates output against it |
| 26 | Cache invalidation | Cache-busting query params in manifest URLs (`?v=<hash>`) to handle re-transcoded assets with immutable headers |
| 27 | Rate limiting | nginx `limit_req` at 5 req/s per IP to prevent auth brute-force |
| 28 | Pipeline dry-run | `--dry-run` flag: parse filenames, report what would be transcoded, print manifest preview, no ffmpeg calls |
| 29 | Error resilience | Pre-flight disk space check, k8s Job resource limits, try/catch on JSON.parse in frontend, img onerror placeholder, hashchange listener for back button |
| 30 | Player overlay | Clean player: video only + close button. No DVD cover in player (cover visible in grid card) |
| 31 | Frontend organization | Single flat app.js with comment sections. No ES modules, no build step |
| 32 | Local dev server | `python3 -m http.server` or equivalent for frontend testing (file:// doesn't support fetch) |

## Actual Data Structure (from existing pipeline)

```
/data/archive/                          ← Source PVC
  ├── 197902-198201.iso
  ├── 197902-198201.jpg                 ← DVD cover
  ├── 19830811-19831212.iso
  ├── 1997-trip-cross-country-pt2-plusplus.iso
  ├── 1997-trip-cross-country-pt2-plusplus.jpg
  ├── christmas-04-05-06.iso
  └── ...

        ↓ existing k8s Job (makemkvcon)

/data/output/                           ← MKV PVC
  ├── 197902-198201/
  │   ├── title00.mkv                   ← one or more titles per DVD
  │   ├── title01.mkv
  │   └── 197902-198201.jpg             ← cover copied in
  ├── christmas-04-05-06/
  │   ├── title00.mkv
  │   └── christmas-04-05-06.jpg
  └── ...

        ↓ NEW k8s Job (this plan)

/data/served/                           ← Output PVC (served by nginx)
  ├── videos/
  │   ├── 197902-198201-title00.mp4
  │   ├── 197902-198201-title01.mp4
  │   └── ...
  ├── thumbs/
  │   ├── 197902-198201-title00.jpg     ← best frame from video
  │   └── ...
  ├── covers/
  │   ├── 197902-198201.jpg             ← DVD cover
  │   └── ...
  ├── manifest.json
  ├── index.html
  ├── app.js
  └── style.css
```

## Filename Parsing Rules

Real filename patterns observed:

| Pattern | Example | Parse |
|---------|---------|-------|
| `YYYYMMDD-YYYYMMDD` | `19830811-19831212` | dateStart=1983-08-11, dateEnd=1983-12-12 |
| `YYYYMM-YYYYMM` | `197902-198201` | dateStart=1979-02, dateEnd=1982-01 |
| `YYYY-label` | `1997-trip-cross-country-pt2-plusplus` | dateStart=1997, title="trip cross country pt2 plusplus" |
| `label-YY-YY-YY` | `christmas-04-05-06` | title="christmas", years=[2004,2005,2006] |
| Unparseable | anything else | dateStart=null, title=filename stem |

Parser logic:
1. Try `(\d{8})-(\d{8})` → YYYYMMDD range
2. Try `(\d{6})-(\d{6})` → YYYYMM range
3. Try `(\d{4})-(.+)` → year + label
4. Try text + short year patterns → label + years
5. Fallback → null dates, stem as title

**Override file** (`overrides.json`): keyed by directory name OR `directory/titleNN` for per-title overrides. Mounted as k8s ConfigMap (not baked into image).
```json
{
  "christmas-04-05-06": {
    "title": "Christmas 2004-2006",
    "dateStart": "2004-12",
    "dateEnd": "2006-12"
  },
  "197902-198201/title01": {
    "title": "Birthday Party",
    "skip": false
  },
  "197902-198201/title02": {
    "skip": true
  }
}
```
Per-title keys override the DVD-level metadata for that specific title. `"skip": true` excludes a title entirely (for junk MakeMKV titles like menus, tiny clips). Titles < 60 seconds are auto-skipped by default (configurable via `--min-duration`).

## manifest.json Schema (V1)

```json
{
  "title": "Family Videos",
  "dateRange": { "start": "1979", "end": "2006" },
  "videos": [
    {
      "id": "197902-198201-title00",
      "title": "Feb 1979 – Jan 1982",
      "dateStart": "1979-02",
      "dateEnd": "1982-01",
      "duration": 6135,
      "file": "videos/197902-198201-title00.mp4",
      "thumbnail": "thumbs/197902-198201-title00.jpg",
      "cover": "covers/197902-198201.jpg",
      "dvd": "197902-198201",
      "sourceFile": "197902-198201/title00.mkv"
    }
  ]
}
```

- `dateStart`/`dateEnd`: nullable for unparseable filenames
- `cover`: DVD cover JPG (shared across titles from same DVD)
- `dvd`: groups multiple titles from the same disc

## Implementation Steps

### Step 1: Pipeline script (`pipeline/transcode.py` + `pipeline/parse.py`)

Python script running as k8s Job. Reads from MKV PVC, writes to output PVC.

0. **Pre-flight checks**: verify available disk space (need ~2x served directory size for staging). Supports `--dry-run` flag to parse filenames, report what would be transcoded, and print manifest preview without running ffmpeg.
1. **Copy existing served to staging**: if a served directory exists, copy it to staging so skipped files are included in the final swap.
2. **Walk directories** in `/data/output/` (one per DVD)
3. **Parse directory name** for dates/title using rules above
4. **Load overrides.json** (mounted as ConfigMap), merge with parsed metadata. Per-title overrides (`"dvd/titleNN"`) override DVD-level metadata.
5. **Filter titles**: skip titles marked `"skip": true` in overrides. Auto-skip titles < 60 seconds (`--min-duration` flag, default 60).
6. **For each .mkv** in the directory (parallel via ProcessPoolExecutor, configurable workers via `WORKERS` env var, default: CPU count):
   - **Always re-encode**: h264 CRF 23, 480p, yadif deinterlace, AAC 128kbps, `-movflags +faststart`, normalize SAR to 1:1 (square pixels)
   - Skip if output .mp4 already exists and source .mkv is not newer (idempotency)
   - On ffmpeg failure → log error, continue batch
7. **Smart thumbnail**: extract 5 frames at 10%, 25%, 40%, 60%, 80% of duration. Pick the frame with highest color variance (avoids black frames, color bars, static)
8. **Copy DVD cover** JPG to `covers/` directory
9. **Add cache-busting**: compute file hash (first 4KB + size) for each asset, append as `?v=<hash>` in manifest URLs
10. **Validate manifest** against `manifest.schema.json` before writing
11. **Write manifest.json** atomically (temp file + rename) to staging
12. **Symlink swap**: atomically rename staging symlink to replace served
13. **Print summary**: N videos processed, M skipped (existing), K skipped (junk), E errors

**Files:**
- `pipeline/transcode.py` — main script
- `pipeline/parse.py` — filename parsing + override merging
- `pipeline/Dockerfile` — Alpine + ffmpeg + Python + jsonschema
- `pipeline/job.yaml` — k8s Job manifest with resource limits
- `manifest.schema.json` — JSON Schema for manifest validation
- `k8s/overrides-configmap.yaml` — ConfigMap for overrides.json

### Step 2: Frontend SPA (`frontend/index.html`, `frontend/app.js`, `frontend/style.css`)

Vanilla JS, no build step. Single flat `app.js` with comment sections (`// === TIMELINE ===`, `// === GRID ===`, etc.).

1. **Fetch manifest.json** on load. Try/catch on JSON.parse. **Loading state**: while fetching, show skeleton screen — the "Family Videos" title + empty timeline bar placeholder + 6 card-shaped warm gradient (`#FAF7F2` to `#F0EBE3`) placeholders in the grid area, pulsing gently (opacity 0.6 to 1.0, 1.5s ease-in-out infinite). This is rendered in the initial HTML (no JS dependency). **Error state**: on fetch failure or malformed JSON, show centered error message "Couldn't load videos" (DM Sans 18px, muted text) + amber "Try Again" button. No raw error text.
2. **App header**: product title "Family Videos" (Instrument Serif 32px, primary text color) and subtitle "Drag to a year to browse" (DM Sans 14px, muted text). Positioned above the timeline, part of the sticky header. No logo, no nav, no settings. Title disappears on scroll (or shrinks to 18px inline with scrubber) to maximize viewport for content.
3. **Build timeline**: compute year range from manifest `dateRange`. Render:
   - Draggable scrubber with **discrete year snapping** (desktop/touch). **Two feedback speeds**: year display (72px number) updates instantly as scrubber moves. Grid re-render debounces by ~150ms after drag pauses. This gives immediate feedback ("I'm at 1994") while the video grid catches up.
   - Clickable year labels below scrubber (tap-friendly for grandparents, DM Sans 14px, 44px min tap area)
   - **Video density indicators**: proportional bars below each year label, max height 12px, min 2px, amber at 30% opacity. Proportional to video count (most-videos year = 12px, others scale linearly).
   - Big year display above scrubber (72px Instrument Serif, centered)
   - **Scrubber track**: 4px height, border color, full width of content area. **Handle**: 20px diameter amber circle, `border-radius: 9999px`, 44px invisible touch target. **Total timeline area**: ~140px on desktop (title 32px + subtitle 14px + 8px gap + year display 72px + scrubber + labels + density = ~140px sticky header).
4. **Filter videos by year**: video appears for all years in its dateStart–dateEnd range (span model). Videos with null dates shown in "Undated" section (with Instrument Serif "Undated" heading at 32px). **Empty year state**: centered "No videos from [year]" message (DM Sans 18px, muted color, in the grid area).
5. **DVD-grouped video grid**: group videos by `dvd` field. Render **utility heading** "Videos from [year]" (Instrument Serif 32px) above the grid. Each DVD group has: **group header** = DVD cover image (60px tall, 4:3 aspect, 4px border-radius) on the left + group title (DM Sans 16px, 600 weight) to the right of the cover, vertically centered. 16px gap between cover and title. 12px vertical padding on the header. Below the header: video cards in the grid layout with 16px gap. 32px gap between DVD groups. Single-title DVDs use the same section shell as multi-title DVDs (one playable item inside the group frame, same cover header pattern). **Mobile (<640px)**: stacked sections, no collapse, cover image full-width above group title (stacked vertically instead of side-by-side), cover scales to 100% width max 200px centered.
6. **Lazy thumbnails**: IntersectionObserver loads thumbnails when scrolled into view. Fallback: if `IntersectionObserver` is undefined on page load, load all thumbnails eagerly (no timeout, no error detection — pure feature check). **Thumbnail loading state**: warm gradient placeholder (same as skeleton) until image loads, then 200ms fade-in. `img onerror` shows a muted video camera icon (CSS-only, no external asset) centered on the warm gradient background. **DVD cover fallback**: if cover JPG 404s, show the first letter of the DVD title in Instrument Serif 48px on the warm gradient background (monogram placeholder). **Preconnect hints** on video card hover (desktop) / tap-hold (mobile).
7. **Player overlay**: dark background (#1A1612 at 92% opacity), native `<video>` element at 4:3 aspect ratio, max-width 90vw, max-height 80vh, centered. Set `poster` attribute to the video's thumbnail URL so the video shows the thumbnail before play and after ending (not a black void). Clean player: video only + close button (top-right, white X, 32px touch target). No cover in player. Close via X button / ESC key only (NOT click-outside — accidental taps on the dark background should not dismiss the player, especially for elderly users on touch devices). **Buffering**: use native `<video>` loading indicator (browser default). **Video 404**: replace player area with centered message "This video isn't available" (DM Sans 18px, white text on the dark overlay) + "Close" button. **Focus management**: return focus to the card that was playing after close.
8. **URL deep-linking**: read `window.location.hash` on load. If exactly 4 digits → set timeline to that year. Otherwise → look up as video ID, auto-open player. **Invalid hash fallback**: if hash doesn't match a year or video ID, ignore it and show the first year in the collection (earliest dateStart). Update hash on year change. Listen to `hashchange` event for back button sync.
9. **Keyboard navigation**: Left/Right arrows move timeline to prev/next year (only when player closed). Enter/Space on focused card opens player. Escape closes player.
10. **Error handling**: video 404 → message in overlay. manifest fail → error page with retry.
11. **Dark mode**: automatic via `prefers-color-scheme: dark` media query. No manual toggle (zero chrome). All colors defined as CSS custom properties (`:root` for light, `@media (prefers-color-scheme: dark)` override for dark). Variable names: `--bg`, `--surface`, `--text`, `--text-muted`, `--accent`, `--accent-hover`, `--border`, `--overlay`, `--shadow-card`, `--shadow-elevated`. Values from DESIGN.md light/dark mode sections. **Note:** `--text-muted` uses the WCAG-safe value `#6B5E54` (light) / `#A89B8F` (dark) at all sizes, not the warmer #8B7E74. Single value, always accessible.
12. **Focus indicators**: all focusable elements (video cards, year labels, close button) get a visible focus ring: `2px solid var(--accent)` with `2px offset`, `100ms ease-out` transition. Only visible on `:focus-visible` (keyboard nav), not on click/tap.
13. **Responsive layout**:
   - **Desktop (≥1024px)**: multi-column video grid (`repeat(auto-fill, minmax(220px, 1fr))`), full timeline scrubber + year labels + density bars. Max content width 1120px, centered.
   - **Tablet (640–1023px)**: same layout, grid adapts naturally (fewer columns). Timeline scrubber still usable at this width.
   - **Mobile (<640px)**: single-column video grid. Timeline header transforms: year labels become large horizontal scrollable strip (44px min height per label, DM Sans 16px), scrubber becomes a thin 4px visual track indicator below the labels. Density bars hidden (too small to read). Year display stays at 48px (reduced from 72px). DVD group cover images scale to full-width above their card. "Family Videos" title at 24px. Subtitle hidden.
   - **Touch targets**: all interactive elements minimum 44×44px tap area (WCAG 2.5.5). Year labels: 44px height with 8px horizontal padding. Close button: 44×44px. Scrubber handle: 32px visible, 44px touch area (invisible expanded hit zone).
14. **Accessibility**:
   - **ARIA landmarks**: `<main>` wraps content area, `<nav>` wraps timeline, `role="dialog" aria-modal="true"` on player overlay with `aria-label="Video player"`.
   - **Screen reader**: year changes announced via a dedicated `aria-live="polite"` region (`#sr-announcements`) with rich context ("Showing 3 videos from 1994"). The visual 72px year display is marked `aria-hidden="true"` so screen readers get the single authoritative announcement instead of hearing the year twice. Player overlay toggles `.app.inert` on open/close so focus cannot escape into the dimmed background content.
   - **Reduced motion**: respect `prefers-reduced-motion` — CSS `scroll-behavior: smooth` wrapped in `@media (prefers-reduced-motion: no-preference)`, JS `scrollIntoView` checks `matchMedia` at point of use. Disable card hover lift, skeleton pulse, thumbnail fade-in, player scale animation. Keep functional transitions (scrubber snap, overlay show/hide) but at 0ms duration.
   - **Contrast**: primary text `#2C2420` on `#FAF7F2` = 14.24:1 (AAA). Muted text `#6B5E54` on `#FAF7F2` = 5.86:1 (AA). Amber buttons: `#2C2420` on `#C68B3F` = 5.19:1 light, `#2C2420` on `#D4A04A` = 6.47:1 dark — both pass AA via the `--on-accent` token fixed at warm-dark in both modes. Dark mode: `#F0EBE3` on `#2D2824` = 12.29:1 (AAA). `#A89B8F` on `#2D2824` = 5.38:1 (AA).

**Files:**
- `frontend/index.html`
- `frontend/app.js`
- `frontend/style.css`

**Local dev**: `python3 -m http.server 8000` in `frontend/` directory for testing (file:// doesn't support fetch).

### Step 3: Container + nginx (`nginx/Dockerfile`, `nginx/nginx.conf`, `nginx/.htpasswd`)

Slim Docker image: just the SPA files + nginx config.

1. **nginx.conf**:
   - `auth_basic "Family Videos"` with `.htpasswd` (**mounted as k8s Secret**, not baked into image). The realm string "Family Videos" makes the browser auth dialog say "Sign in to Family Videos" instead of displaying the hostname.
   - `limit_req zone=auth burst=5` — rate limit 5 req/s per IP (brute-force protection)
   - `location /videos/` + `/thumbs/` + `/covers/` → `Cache-Control: public, max-age=31536000, immutable` (cache-busting via `?v=<hash>` query params in manifest URLs handles re-transcoded assets)
   - `location /manifest.json` → `Cache-Control: no-cache`
   - Correct MIME types (video/mp4, image/jpeg, application/json)
   - `sendfile on`, `tcp_nopush on` for efficient large file serving
2. **Dockerfile**: `FROM nginx:alpine`, COPY frontend files + nginx.conf only (no secrets in image)
3. **Video/thumb/cover files served from PVC mount** (not baked into image)

**Files:**
- `nginx/Dockerfile`
- `nginx/nginx.conf`
- `k8s/htpasswd-secret.yaml` — Secret for .htpasswd (generated via `htpasswd -c`)

### Step 4: k8s deployment (`k8s/deployment.yaml`, `k8s/service.yaml`, `k8s/ingress.yaml`)

1. Deployment: nginx container mounting output PVC read-only at `/data/served`
2. Service: ClusterIP exposing port 80
3. Ingress: pointing to existing TLS-enabled ingress controller

**Files:**
- `k8s/deployment.yaml`
- `k8s/service.yaml`
- `k8s/ingress.yaml`

### Step 5: Pipeline tests (`tests/test_parse.py`, `tests/test_transcode.py`)

pytest suite for the pipeline.

- **test_parse.py**: All filename patterns (YYYYMMDD-YYYYMMDD, YYYYMM-YYYYMM, YYYY-label, label-YY-YY-YY, unparseable). Override merging. Slug generation.
- **test_transcode.py**: Idempotency (skip existing). Error handling (mock ffmpeg failure → continues). Smart thumbnail selection (mock ffprobe). Atomic manifest write.

**Files:**
- `tests/test_parse.py`
- `tests/test_transcode.py`
- `tests/conftest.py` (fixtures)

## NOT in scope (V1)

- Sprite sheet generation / hover-to-preview scrub
- Scene detection / splitting long DVDs into moments
- Face recognition / per-person timeline filtering
- Custom auth page (using browser native auth_basic dialog)
- Mobile app, admin UI, search, upload functionality
- TLS/domain setup (already handled)
- Play-all sequential mode (deferred to TODOS.md)
- Remux fast-path (always re-encode for simplicity + iPad compatibility)

## TODOS.md

- **Scene detection + DVD cover OCR matching**: The DVD covers have scene *labels* (names like "Christmas Morning", "First Steps") but NOT timestamps. The full pipeline: (1) scene detection splits the video into segments with timestamps, (2) OCR/vision model extracts scene names from the cover JPG, (3) match detected scenes to cover labels. The cover is the key that names the scenes, video analysis provides the timestamps. *Why: covers already exist in each output directory with printed scene lists. Both scene splitting AND OCR are needed — neither alone is sufficient.*
- **Show DVD cover JPGs prominently in the app UI**: Covers are valuable for the experience (grandma recognizes the cover art). V1 includes covers but exact UX (detail panel, card overlay, player sidebar) to be refined during build. *Why: user explicitly said these are very valuable for the experience.*

## Worktree Parallelization

| Lane | Steps | Modules |
|------|-------|---------|
| A | Pipeline script + tests | `pipeline/`, `tests/` |
| B | Frontend SPA | `frontend/` |
| C (after A+B) | Container + k8s | `nginx/`, `k8s/` |

Launch A + B in parallel. Merge both. Then C.

Lanes A and B share the manifest.json schema as a contract. B can build against a mock manifest while A produces the real one.

## Failure Modes

| Failure | Covered? | User sees |
|---------|----------|-----------|
| Corrupt .mkv (ffmpeg fails) | ✓ pipeline logs + continues | Video missing, rest work |
| Unparseable filename | ✓ null dates + override file | Video in "Undated" section |
| Pipeline killed mid-write | ✓ symlink swap (atomic) | Old version stays live |
| Pipeline disk full | ✓ pre-flight space check | Pipeline aborts with message |
| Pipeline OOM | ✓ k8s resource limits | Pod restart, re-run Job |
| Junk MakeMKV titles | ✓ duration filter + skip override | Excluded from manifest |
| manifest.json fetch fails | ✓ error page + retry button | "Could not load videos" |
| manifest.json malformed | ✓ try/catch JSON.parse | "Could not load videos" |
| Thumbnail 404 | ✓ img onerror placeholder | Generic video icon |
| Video file 404 | ✓ error message in overlay | "Video unavailable" |
| Slow connection / buffering | ✓ nginx range requests | Browser buffering indicator |
| Stale cached assets | ✓ cache-busting query params | Fresh content after re-transcode |
| auth brute-force | ✓ nginx limit_req | Rate limited |
| Deep-link to invalid hash | ✓ fallback to default view | Normal page, no jump |
| IntersectionObserver missing | ✓ eager thumbnail loading | Slightly slower initial load |

**0 critical gaps.** All failure modes are handled.

## Verification

1. **Pipeline**: `pytest tests/` — all filename parsing patterns, per-title overrides, junk filtering, error handling
2. **Pipeline dry-run**: `python3 pipeline/transcode.py --dry-run /data/output/` — verify filename parsing against real data before committing to transcode
3. **Frontend**: `cd frontend && python3 -m http.server 8000` with a mock `manifest.json` — verify timeline renders, year labels clickable, DVD grouping displays, player overlay works, keyboard nav, deep-linking, density indicators, lazy loading
4. **Integration**: Deploy to k8s, hit the ingress URL, verify auth_basic prompts, timeline loads, videos play on iPad Safari + Chrome + desktop
5. **Edge cases**: null dates (undated section), DVD with multiple titles (grouping), `christmas-04-05-06` edge case, empty year (no videos message), deep-link to nonexistent video (graceful fallback), back button after deep-link, rapid scrubber drag (debounce)

## Completion Summary (Eng Review)

- **Step 0: Scope Challenge** — scope accepted, 8 files (minimum viable set for greenfield)
- **Architecture Review**: 6 issues found, all resolved (auth, storage, pipeline location, filenames, sprites, covers)
- **Code Quality Review**: 1 issue found, resolved (pipeline error resilience + idempotency)
- **Test Review**: diagram produced, 26 gaps identified (13 pipeline → pytest, 13 frontend → manual)
- **Performance Review**: 1 issue found, resolved (nginx cache headers)
- **Outside Voice**: Codex ran. 16 findings. 5 incorporated (smart transcode, atomic publish, smart thumbnails, nullable dates, year labels). k8s complexity rejected (user has existing cluster).
- **NOT in scope**: written
- **What already exists**: written
- **TODOS.md updates**: 2 items added (DVD cover OCR, cover UX refinement)
- **Failure modes**: 2 critical gaps flagged (frontend error handling), included in Step 2
- **Parallelization**: 3 lanes (A+B parallel, then C)
- **Lake Score**: 9/9 recommendations chose complete option

## Completion Summary (CEO Review)

- **Mode**: SELECTIVE EXPANSION — 6 proposals, 5 accepted, 1 skipped
- **Accepted cherry-picks**: URL deep-linking, DVD grouping UI, keyboard navigation, video density indicators, lazy thumbnails + preconnect
- **Outside Voice (Codex gpt-5.4)**: 14 findings. 6 incorporated: always re-encode (drop remux), staging copies served first (fix idempotency+atomicity), per-title overrides + duration filter, ConfigMap/Secret for config, manifest.schema.json, cache-busting query params
- **Additional fixes from review**: symlink swap for atomic publish, rate limiting, pre-flight disk check, k8s resource limits, try/catch JSON.parse, img onerror placeholder, hashchange listener, empty year state, scrubber debounce, local dev server
- **TODOS.md updates**: 1 item added (play-all sequential mode). 1 existing TODO updated (DVD cover UX, now partially in V1 scope via grouping)
- **Failure modes**: 0 critical gaps (was 2, both fixed)
- **Lake Score**: 14/15 recommendations chose complete option

## Completion Summary (Design Review)

- **Initial score**: 5/10 — plan described features but not what users see
- **Outside voices**: Codex (gpt-5.4) flagged 1 hard rejection (card-based layout) + 5 findings. Claude subagent found 11 issues (1 critical: contrast, 4 high, 6 medium).
- **Pass 1 (Info Arch)**: 4→8. Added product title "Family Videos", instruction subtitle, utility headings, consistent DVD group rendering.
- **Pass 2 (States)**: 3→9. Added skeleton loading, thumbnail/cover fallbacks, monogram placeholder, font-display swap, player error state.
- **Pass 3 (Journey)**: 6→8. Set auth_basic realm to "Family Videos", video poster thumbnail (no black void on end), removed click-outside dismiss.
- **Pass 4 (AI Slop)**: 7→8. Clean on all 10 blacklist patterns. Date range titles accepted.
- **Pass 5 (Design Sys)**: 6→9. Added CSS custom properties spec, dark mode via prefers-color-scheme, focus ring styling.
- **Pass 6 (Responsive)**: 3→9. Added 3 breakpoints, mobile timeline transformation, 44px touch targets, ARIA landmarks, screen reader announcements, reduced motion, contrast fix.
- **Pass 7 (Decisions)**: 4 resolved (scrubber feedback, DVD header layout, handle dimensions, timeline height). 0 deferred.
- **TODOS.md**: 1 item added (WCAG contrast audit P3).
- **Final score**: 9/10.

## Post-V1 Landing Refresh — Approved Mockup (Design Shotgun)

Generated by `/design-shotgun` on 2026-04-08 against the shipped v0.1.2.0 landing page. Two rounds, 8 initial variants + 3 synthesis remixes. This is a **direction reference for a future iteration**, not a v1 requirement.

**Approved direction:** "Anchored Spine"

- **Mockup:** `~/.gstack/projects/n1mmy-family-videos/designs/landing-shotgun-20260408/remix-2.png`
- **Full feedback + decisions:** `~/.gstack/projects/n1mmy-family-videos/designs/landing-shotgun-20260408/approved.json`

**Layout:**

- **Left rail:** Massive Instrument Serif year number (~200px), warm near-black (`#2C2420`), acts as a drop-cap visual anchor. The year is the hero.
- **Top bar:** Horizontal timeline scrubber spanning full width, amber (`#C68B3F`) slider handle, year tick marks from the first to last year in the collection.
- **Right column (~70% width):** Editorial multi-section grid showing 2-4 DVD group sections simultaneously — current year plus adjacent years. Each section = DVD cover + 2-4 video thumbnails + small DM Sans captions. Hairline dividers between sections.

**Why this layout:** Round 1 feedback on D (Magazine Editorial) called out a real product bug — "the first year only has one video, so the current page looks sparse at first." The current one-year-at-a-time model fails on sparse years. This layout solves it by always showing adjacent-year content, so even an empty year still fills the viewport with context.

**Required affordances (grandparent discoverability):**

- **Scroll-arrow chevrons at LEFT and RIGHT edges of the top timeline bar** — double duty as scroll indicators and tap targets. Drag is an invisible affordance; explicit arrow buttons are discoverable.
- **Scroll-arrow chevrons above/below the left-rail year spine** — same reasoning, vertical direction. User explicitly noted: "a touch target" for grandparent taps, not just a scroll indicator.
- These are not decorative — they are required for the primary user (grandparents) who prefer tap targets over drag gestures.

**Round-1 taste signals (informed round 2 and future iterations):**

- **High:** D (Editorial 4/5), E (Timeline Hero 4/5), H (Archive Catalog 4/5). User liked D's multi-section organization, E's huge serif year anchor, H's brass slider bar.
- **Mid:** G (Film Strip 3/5).
- **Low:** A (Photo Album 2), B (Minimalism 2), C (VHS Shelf 2), F (Sketchbook 1). Polaroid / album / skeuomorph / hand-drawn aesthetics were rejected.

**Scope note:** This is *not* in v1 scope. v1 is already shipped (v0.1.2.0, CEO+ENG+DESIGN CLEARED). Treat this as an approved direction for a "landing page refresh" work item whenever that gets prioritized. The current `frontend/` code does not need to change until then.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | 6 proposals, 5 accepted, 0 deferred. Mode: SELECTIVE EXPANSION |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | CLEAR (PLAN) | Run 1: 9 issues, 0 critical gaps. Run 2 (post-design review): 4 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | CLEAR (FULL) | score: 5/10 → 9/10, 8 decisions |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |
| Outside Voice | Codex via CEO + eng + design review | Independent plan challenge | 3 | issues_found | 14 (CEO), 16 (eng), 6 (design). 19 incorporated total |

- **CROSS-MODEL:** Design review outside voices: Codex flagged card-based layout + missing brand/headlines/CSS vars. Claude subagent found contrast failure + missing states + tap targets. Both agreed on no-brand-in-first-screen. All findings addressed.
- **ENG RE-RUN:** Unified `--text-muted` to #6B5E54 (WCAG-safe single value). Fixed stale click-outside reference in product-spec. Updated test plan to 53 frontend paths. Updated WCAG TODO with partial resolution.
- **UNRESOLVED:** 0
- **VERDICT:** CEO + ENG + DESIGN CLEARED — ready to implement. Run `/ship` when done.

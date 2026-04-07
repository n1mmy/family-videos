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
| 6 | Transcode strategy | Smart: ffprobe detect codec/interlacing, remux if already h264, deinterlace VHS, re-encode only when needed |
| 7 | Thumbnails | Smart frame selection (sample multiple, pick highest variance) + DVD cover JPGs shown in UI |
| 8 | Sprites | Deferred to V2 |
| 9 | Pipeline resilience | Idempotent (skip existing) + error-resilient (log failures, continue batch) |
| 10 | Atomic publish | Pipeline writes to staging dir, swaps to served dir on completion |
| 11 | Cache headers | `immutable` for videos/thumbs, `no-cache` for manifest.json |
| 12 | Dates in schema | Nullable — unparseable filenames get `null` dates, shown in "Unknown Date" group |
| 13 | Tests | pytest for pipeline, manual frontend testing |
| 14 | TLS | Already handled on cluster |

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

**Override file** (`overrides.json`): keyed by directory name, any field can be overridden.
```json
{
  "christmas-04-05-06": {
    "title": "Christmas 2004-2006",
    "dateStart": "2004-12",
    "dateEnd": "2006-12"
  }
}
```

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

1. **Walk directories** in `/data/output/` (one per DVD)
2. **Parse directory name** for dates/title using rules above
3. **Load overrides.json** if present, merge with parsed metadata
4. **For each .mkv** in the directory:
   - `ffprobe` → check codec (h264 already?), check interlacing
   - If h264 + progressive → **remux** to .mp4 (instant, lossless)
   - If not h264 or interlaced → **transcode**: h264 CRF 23, 480p, yadif deinterlace, AAC 128kbps
   - Skip if output .mp4 already exists (idempotency)
   - On ffmpeg failure → log error, continue batch
5. **Smart thumbnail**: extract 5 frames at 10%, 25%, 40%, 60%, 80% of duration. Pick the frame with highest color variance (avoids black frames, color bars, static)
6. **Copy DVD cover** JPG to `covers/` directory
7. **Write manifest.json** atomically (temp file + rename)
8. **Print summary**: N videos processed, M skipped, K errors

All output goes to a staging directory. On success, swap staging → served.

**Files:**
- `pipeline/transcode.py` — main script
- `pipeline/parse.py` — filename parsing + override merging
- `pipeline/Dockerfile` — Alpine + ffmpeg + Python
- `pipeline/job.yaml` — k8s Job manifest
- `pipeline/overrides.json` — manual metadata overrides (starts empty)

### Step 2: Frontend SPA (`frontend/index.html`, `frontend/app.js`, `frontend/style.css`)

Vanilla JS, no build step.

1. **Fetch manifest.json** on load. Show error message if fetch fails.
2. **Build timeline**: compute year range from manifest `dateRange`. Render:
   - Draggable scrubber (desktop/touch)
   - Clickable year labels below scrubber (tap-friendly for grandparents)
   - Tick marks on scrubber where videos exist
   - Big year display above scrubber
3. **Filter videos by year**: video appears for all years in its dateStart–dateEnd range. Videos with null dates shown in "Undated" section.
4. **Video cards**: poster thumbnail, title, date range, duration. DVD cover shown as secondary element (small icon or overlay on card — exact UX to be refined during build).
5. **Player overlay**: dark background, native `<video>` element at 4:3 aspect ratio, close via X / ESC / click outside. Show DVD cover alongside video in the overlay.
6. **Error handling**: video 404 → message in overlay. manifest fail → error page with retry.
7. **Mobile**: single-column grid, year labels as primary nav (scrubber is harder on small screens).

**Files:**
- `frontend/index.html`
- `frontend/app.js`
- `frontend/style.css`

### Step 3: Container + nginx (`nginx/Dockerfile`, `nginx/nginx.conf`, `nginx/.htpasswd`)

Slim Docker image: just the SPA files + nginx config.

1. **nginx.conf**:
   - `auth_basic` with `.htpasswd` (single shared user)
   - `location /videos/` + `/thumbs/` + `/covers/` → `Cache-Control: public, max-age=31536000, immutable`
   - `location /manifest.json` → `Cache-Control: no-cache`
   - Correct MIME types (video/mp4, image/jpeg, application/json)
   - `sendfile on`, `tcp_nopush on` for efficient large file serving
2. **Dockerfile**: `FROM nginx:alpine`, COPY frontend files + nginx.conf + .htpasswd
3. **Video/thumb/cover files served from PVC mount** (not baked into image)

**Files:**
- `nginx/Dockerfile`
- `nginx/nginx.conf`
- `nginx/.htpasswd` (generated via `htpasswd -c`)

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
- Smart TV browser optimization (best-effort via keyboard arrows)
- Mobile app, admin UI, search, upload functionality
- TLS/domain setup (already handled)

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
| Pipeline killed mid-write | ✓ staging + atomic swap | Old version stays live |
| manifest.json fetch fails | Needs impl in frontend | Error message + retry |
| Video file 404 | Needs impl in frontend | Error in player overlay |
| Slow connection / buffering | ✓ nginx range requests | Browser buffering indicator |

**2 critical gaps**: Frontend error handling for manifest failure and video 404.
Both are ~5 lines of JS each. Included in Step 2.

## Verification

1. **Pipeline**: `pytest tests/` — all filename parsing patterns + error handling
2. **Frontend**: Open `frontend/index.html` locally with a mock `manifest.json` — verify timeline renders, year labels clickable, video cards display, player overlay works
3. **Integration**: Deploy to k8s, hit the ingress URL, verify auth_basic prompts, timeline loads, videos play on iPad Safari + Chrome + desktop
4. **Edge cases**: Test with a video that has null dates, a DVD with multiple titles, the `christmas-04-05-06` edge case

## Completion Summary

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

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 9 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |
| Outside Voice | Codex via eng review | Independent plan challenge | 1 | issues_found | 16 findings, 5 incorporated |

**VERDICT:** ENG CLEARED — ready to implement. Run `/ship` when done.

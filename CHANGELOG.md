# Changelog

All notable changes to Family Videos will be documented in this file.

## [0.2.1.0] - 2026-04-08

### Fixed
- **"Undated" no longer overflows the year sidebar.** The spine was sized for 4-digit years (`clamp(140px, 14vw, 220px)` Instrument Serif) and "Undated" punched ~280px out of the 280px-wide rail, trampling the content column. A new `.spine-year--undated` modifier renders the label at `clamp(40px, 4.5vw, 56px)` italic muted — so it reads as a category label rather than a mislabeled year — and toggles on in lockstep with the spine text in `syncYearDisplay`. Mobile override at 32px so it still fits in a narrow banner.
- **Videos with dates in the filename no longer fall into Undated.** The DVD directory parser only understood explicit ranges (`YYYYMMDD-YYYYMMDD`, `YYYYMM-YYYYMM`, `YYYY-label`, `label-YY-YY`). Anything else — a single day like `19940328`, a single month like `200107-hawaii-pt2`, a labelled range like `20020702-20021225-alaskan-cruise-pt2`, or a labelled single date like `20120728-nickjen-reception` — silently landed in Undated. Pattern 1 and Pattern 2 now accept single dates and an optional `-label` suffix on ranges, recovering 6 of 7 previously-undated DVDs. The seventh (`19881123-1989325`, a 7-digit typo) still falls through and needs an override — that's intentional, we don't guess at mangled dates.

### Changed
- **Parser now validates real calendar dates via `datetime.date`.** The previous `1 <= sd <= 31` guard accepted `19990631` (June 31), `19990230` (Feb 30), and Feb 29 in non-leap years, emitting strings like `1999-06-31` that broke the frontend date parser. Invalid dates fall through to the next pattern instead.
- **Parser rejects dates outside `[1900, current_year+5]`** matching the frontend's `deriveYearRangeFromVideos` clamp. Year 0 (`00000101`, `0000-trip`) no longer parses.
- **Date ranges with end before start** (`19901231-19900101`) now fall through instead of silently inverting.
- **Labels starting with a digit are rejected as likely mangled dates.** `20010101-20020102x` used to parse as a single day with title `20020102x`, silently dropping the user's intended 2002 end date. Caught by adversarial review.
- **Multi-dash fragments in labels** (`20010101--double`, trailing dash) are now collapsed with split/filter/join so titles don't pick up leading or double spaces.

## [0.2.0.0] - 2026-04-08

### Added
- **Anchored Spine landing layout.** The catalog page is now a full editorial flow instead of a one-year-at-a-time grid. A massive Instrument Serif year number anchors the left rail (clamping from 140px on tablet to 220px on desktop), the top bar holds the timeline scrubber + chevrons + title, and the right column streams every year section as a continuous chronological feed. Solves the long-standing sparse-year problem: empty years used to render as blank pages with "No videos from 1989" — now they're omitted from the flow entirely so the catalog reads as one continuous family album. (Approved direction from `/design-shotgun` 2026-04-08, see `docs/plan.md`.)
- **Top + spine chevron navigation.** Two pairs of `‹ ›` chevrons (top bar left/right and spine up/down on desktop) step the current year through the set of years that actually have content, skipping empty runs. Built explicitly as tap targets for the primary user (grandparents prefer obvious targets over invisible drag affordances). On mobile the spine chevrons collapse out and the top-bar pair becomes the sole stepper to avoid duplicate targets.
- **Discontinuity markers in the timeline.** Runs of consecutive empty years (e.g., 1980–1982) collapse on the top scrubber into a `···` dot marker with a tooltip showing the skipped range. The scrubber now snaps directly between content years on drag instead of crawling through dead zones, and clicking a "···" snaps to the nearest year with content. Solves a UX bug found during dogfooding: the previous timeline made you drag through years that did nothing.
- **Clickable DVD cover lightbox.** The tiny 60px DVD cover thumbnails next to each group are unreadable for what they actually contain — many are handwritten VHS labels with notes like "Christmas 81+82, Disney 82, Jen 2nd Bd Party". Click the cover and a lightbox opens showing the full image (up to 1200×85vh, preserving aspect ratio). Closes via dedicated `×` button, Escape key, or backdrop click. Background goes inert while open, focus restores to the originating cover button on close (with `preventScroll: true` so the page doesn't yank the cover back into view), and a Tab focus trap is in place as a fallback for browsers without `inert` support. The image src is cleared on close to free the decoded bitmap.

### Changed
- **Editorial flow renders all year sections at once** instead of swapping a single year's grid on each navigation. Rendering all sections up front is what makes the new layout work as a continuous reading experience and lets scroll-spy track which year you're in. The DVD primary-year grouping ensures each multi-year DVD appears exactly once at its earliest year — no more `Feb 1979 – Jan 1982` showing up four times across 1979, 1980, 1981, 1982.
- **Scroll-spy via `IntersectionObserver`** now tracks the current year as you scroll, updating the spine year, the active timeline label, the active section highlight, and the URL hash in lockstep. Uses an anchor line ~24px below the sticky top bar (read live from the `--top-bar-height` CSS variable so the line follows the desktop/mobile difference). The observer maintains its own visible-set across callbacks instead of only inspecting the deltas in each entry batch, so a section that stays visible while another scrolls past is correctly preserved.
- **Programmatic-scroll guard.** Smooth `scrollIntoView` jumps trigger a 2-second guard window that stops the scroll-spy from fighting the in-flight animation; cleared early via the native `scrollend` event when the browser supports it.
- **Scrubber drag is now zero-layout-read per pointer move.** Label centers + the track rect are snapshotted into a closure cache at `mousedown` and read from the cache on every `onMove`, eliminating the layout thrash where the previous tick's `handle.style.left` write forced a synchronous layout flush on the next read. Active label and section toggling moved to O(1) `state.labelByYear` / `state.sectionByYear` hash lookups instead of iterating every label and section per drag tick. URL hash updates and screen-reader announcements are deferred during drag and flushed once on drag end so the history API and aria-live region don't churn on every pointer move.
- **DVD covers are now `<button>` wrappers around the `<img>`** so they're keyboard-accessible tap targets with a proper aria-label, focus ring, and hover lift instead of bare images.
- **Mobile spine** uses `100svh` instead of `100vh` so the year doesn't jump as the iOS dynamic address bar collapses on scroll.
- **Coupled scrubber track + labels** in a shared `.timeline-rail` so they scroll horizontally as one rigid unit. Previously the labels had their own `overflow-x: auto` and could drift relative to the static track, leaving the handle visually misaligned from the active label by 100+ pixels on long timelines.

### Fixed
- **DVD primary-year grouping anchors at the earliest video, not per-video.** The previous single-pass implementation keyed groups by `(perVideoYear, dvdId)`, so a DVD with videos in 2004, 2005, and 2006 silently split into three one-video groups in three separate sections. The two-pass implementation finds the earliest `dateStart` per DVD first, then buckets every video under that single anchor year. (Caught by Codex adversarial review.)
- **Empty manifest no longer renders the literal string "undefined" in 220px serif.** Both `applyHash` and `syncYearDisplay` now guard against null/undefined years, and an empty library shows a proper "No videos in this library yet." card with the spine and top timeline hidden.
- **`deriveYearRangeFromVideos` clamps to `[1900, currentYear+5]`** so a single bogus `99990101` value can't allocate 8000 years on the client.
- **Chevron click listeners no longer stack on manifest retry.** A `state.chevronsInitialized` guard mirrors the existing `scrubberInitialized` pattern; without it, every successful retry would double the click handlers and chevron presses would skip multiple years.
- **Deep-link `setTimeout(openPlayer)` race.** The timer is now tracked in `state.pendingPlayerTimer` and cancelled on every `hashchange`, so a fast back-button can't open a stale video from a prior deep link.
- **`getYearRange` falls back to a single year when only one date parses.** Previously a video with `dateStart` but no `dateEnd` returned `['undated']` and silently dropped out of its real year's density bar.
- **Density bar count uses primary-year grouping for the screen reader announcement** instead of the span-model count. A year with 1 primary video but 5 spanning videos used to announce "Showing 6 videos" while visually showing 1.
- **Cover viewer Tab focus trap** as defense in depth alongside `inert` for older browsers (Safari < 15.5) where `inert` isn't supported.

### Removed
- `groupByDvd()` helper (its only caller `renderGrid` was deleted in the refactor).
- `state.numericYears` cache (now narrowed to a one-time read inside `buildTimeline` for the slider ARIA bounds).
- `state.timelineItems` field (now a local var inside `buildTimeline` since the gap-collapse walk is its only consumer).
- Orphan `.year-section-empty` CSS rule (no JS producer after the condensing refactor).
- Dead `if (groups.length === 0) continue;` guard in `renderAllSections` (`state.yearsWithContent` is already filtered to non-empty).

## [0.1.3.0] - 2026-04-08

### Added
- Local dev server that runs the real site against production data. A new `dev` launch config (`.claude/launch.json`) and `scripts/dev_proxy.py` serve `frontend/` from `127.0.0.1:8765` while transparently proxying `/manifest.json`, `/videos/*`, `/thumbs/*`, `/covers/*`, and `/healthz` to a configured upstream with HTTP Basic Auth injected. Lets you iterate on HTML/CSS/JS against a real 74-video manifest without transcoding anything locally. Video seeking works end-to-end — the proxy forwards `Range` headers and relays `206 Partial Content` responses. Credentials and upstream URL live in `~/.config/family-videos/dev-auth` (outside the repo), so no private hostnames land in git.

## [0.1.2.0] - 2026-04-08

### Changed
- DVD group titles now read as human dates. A DVD like `197902-198201` used to render as the raw archive ID `197902 198201` — a grandparent-facing app leaking filesystem metadata. It now reads `Feb 1979 – Jan 1982`, and an `19830811-19831212`-style ID becomes `Aug 1983 – Dec 1983`. The monogram placeholder that shows when a cover image is missing now falls back to a neutral square when the title is purely numeric, instead of showing a meaningless "1".
- Timeline year display now uses tabular numerals. The big serif year no longer reflows horizontally while you drag the scrubber from, say, "1989" to "1990" — digits lock to a fixed width so the timeline feels calm.
- The active timeline year label now uses dark text with an amber underline instead of amber text. The old active-state color was `#C68B3F` on warm paper at 2.74:1 contrast, which failed WCAG AA for a grandparent audience.
- Error-state and player-error action buttons now use warm-dark text (`#2C2420`) on the amber background instead of white. White-on-amber was 2.93:1 (fails AA); dark-on-amber is 5.19:1 (light) and 6.47:1 (dark) and passes AA in both modes. The "Try Again" and player "Close" buttons are now legible in dark mode, which they were not before.
- Body text is now 18px instead of 16px, matching the stated DESIGN.md spec for a grandparent audience.
- Player overlay is now a proper modal dialog. The overlay used `role="region"` despite trapping focus like a modal; assistive tech had no way to know it was a blocking dialog. Now uses `role="dialog" aria-modal="true"` and toggles `inert` on the background app wrapper so VoiceOver and Tab navigation cannot escape the modal into dimmed content.
- Player error card (shown when a video file is missing) now reads as a warm surface card instead of a hardcoded `rgba(0,0,0,0.5)` backdrop. Warm paper in light mode, warm charcoal in dark mode, matching the Family Album language.
- Duration badges on video thumbnails are now tokenized via `--badge-bg`. In light mode they stay warm-dark; in dark mode they shift to near-black so the text stays visible against the dark charcoal thumbnail placeholders that used to hide them.
- Honor `prefers-reduced-motion` end-to-end. Smooth scroll in CSS is wrapped in `@media (prefers-reduced-motion: no-preference)`, and the JS `scrollIntoView` call now checks `matchMedia` at the point of use so users who request reduced motion actually get instant scroll instead of smooth.
- Duplicate screen reader announcement on year change removed. The visual year display had `aria-live="polite"` AND `setYear()` called `announce()` with richer text — so every scrub got read twice. The visual display is now `aria-hidden="true"` and the explicit announce (which also reads the video count) is the single source of truth.
- Error/player error buttons consolidated into one shared CSS rule. The two rules were 11 identical lines and had to be updated in lockstep by the contrast fix — clear evidence they should always have been one rule.

### Added
- `color-scheme: light dark` declaration on `:root` and paired `<meta name="theme-color">` tags for light and dark. Browser form controls, scrollbars, and (on mobile) the browser chrome/status bar now match the Family Album palette in both modes.
- `text-wrap: balance` on display headings so multi-word serif headings break evenly without awkward widows.
- `font-family` on the player close `×` button. It was inheriting Arial from the user agent — a browser default in a design that explicitly uses zero browser defaults.
- `--on-accent` design token for "text on amber backgrounds", fixed at warm-dark in both modes so future amber surfaces get AA contrast by default instead of regressing.
- Dev preview launch config (`.claude/launch.json`) that serves `frontend/` on `127.0.0.1:8765` via `python3 -m http.server`. Bound to loopback only so the dev server isn't exposed to LAN on untrusted networks.

### Fixed
- `matchMedia` read moved off the scrubber drag hot path. `setYear()` fires on every mousemove during drag; reading the reduced-motion preference on every call when the scroll branch is gated out was wasted work.

## [0.1.1.0] - 2026-04-08

### Fixed
- Published `manifest.json` is now readable to nginx in the staging container. The transcode pipeline runs as root, nginx runs as the unprivileged `nginx` user, and `tempfile.mkstemp` was creating the manifest's temp file with mode `0o600` per its documented security contract. The atomic rename preserved that mode, so nginx got `EACCES` on every request and the catalog page failed to load. `write_manifest_atomic` now `fchmod`s the open file descriptor to `0o644` before close, eliminating the path-based TOCTOU window between close and chmod.
- Pipeline now pins `umask 0o022` at the start of `run_pipeline` so directories, the `.healthz` readiness marker, transcoded mp4s, thumbnails, and covers are all world-readable regardless of the operator's process umask. Previously they relied on the alpine base image's default umask being `0o022`, which would have silently broken on a hardened base image with `umask 0o077`.

## [0.1.0.0] - 2026-04-07

### Changed
- Transcode pipeline now stages inside `/data/served` instead of the container's ephemeral root. Unchanged files are represented as cheap absolute symlinks into the served tree (faster than hardlinks on CephFS), and new transcodes publish via per-file `os.replace` for an atomic rename instead of a byte-by-byte copy back. The manifest.json rename at the end of publish is the commit point for the whole set.
- Pipeline runs now print per-title progress in the `as_completed` loop — `[N/total] transcoded foo.mp4`, `[N/total] skipped (up to date)`, or `[N/total] FAILED` — plus discovery and publish breadcrumbs, so long runs are observable instead of silent.
- Disk space pre-flight now measures only the three published content subdirs (videos/, thumbs/, covers/) via `rglob`, excluding leftover staging directories and the `.healthz`/`.transcode.lock` bookkeeping files. Headroom requirement dropped from 2× served size to 10% (with a 1 GiB floor).

### Added
- Single-writer advisory `fcntl.flock` on `/data/served/.transcode.lock` held for the entire pipeline run. A second concurrent invocation exits cleanly with code 2 instead of corrupting the manifest via a race. The lock file is opened with `O_CLOEXEC` (so ffmpeg subprocesses don't inherit it) and `O_NOFOLLOW` (so an operator-planted symlink at the lock path is rejected with ELOOP).
- Startup reaper `reap_stale_staging` that removes any leftover `.staging-*` directories from prior crashed runs. Handles real directories, stray symlinks, and regular files defensively — symlinks are unlinked without being followed, so a malicious `.staging-evil -> /etc` cannot escalate to deleting its target.
- `try/finally` cleanup wrapper around the pipeline body, so the staging dir is always `rmtree`'d — on normal return, dry runs, transcode errors, unhandled exceptions, and lock-acquisition failures. The lock is released in a nested `finally`, guaranteed even if staging cleanup raises.
- Pre-transcode `unlink` guard in `process_one_title` that breaks any symlink at the staging output path before invoking ffmpeg. Without it, ffmpeg's `-y` (open with `O_TRUNC`) would follow the staging symlink and zero out the served target on the other end.
- 25 new tests covering the symlink invariants, staging cleanup safety, flock semantics (concurrent-run rejection and release on every exit path), reaper symlink handling, `check_disk_space` formula, and the `copy_cover` symlink-break regression test. Test count grew from 43 to 68.

### Fixed
- `copy_cover` no longer follows the staging symlink and truncates the served original cover in place. `shutil.copy2` opens the destination with `O_TRUNC`, which bypassed this branch's atomic-publish guarantee for cover files specifically. The function now unlinks the staging entry before the copy, matching the pattern used in `process_one_title`.
- Pipeline filesystem errors from `mkdtemp`, the reaper, or the transcode body are no longer misreported as "Could not open lock file". The narrow `except OSError` around lock acquisition was previously catching every OSError raised inside the locked body and logging it with the wrong cause, making operational debugging harder.

## [0.0.2.0] - 2026-04-07

### Added
- nginx container image serving the frontend SPA with HTTP Basic Auth, rate limiting (5 req/s per real client IP), and cache headers (immutable for videos/thumbnails/covers, no-cache for manifest)
- Kubernetes deployment, service, and ingress manifests for hosting on a home k8s cluster
- Kustomize base/overlay pattern so cluster-specific details (domain, TLS, registry, credentials) stay out of the public repo
- Example overlay showing how to customize each manifest
- Readiness probe backed by a .healthz marker file on the PVC to detect Ceph mount failures
- Security headers on all responses (HSTS, X-Frame-Options, X-Content-Type-Options)
- Gzip compression for text assets (HTML, CSS, JS, JSON)
- Health check marker file written by the transcode pipeline for probe verification

## [0.0.1.0] - 2026-04-07

### Added
- Transcode pipeline that converts MKV files from DVD extracts into web-playable MP4s (h264 CRF 23, 480p, AAC 128k)
- Smart thumbnail selection that samples 5 frames per video and picks the one with highest visual variety (avoids black frames and color bars)
- DVD cover art copying to a served directory
- Filename parser that extracts dates and titles from DVD directory names (supports YYYYMMDD ranges, YYYYMM ranges, year+label, and label+year patterns)
- Override system via overrides.json for manual metadata corrections and title skipping
- JSON Schema validated manifest.json as the contract between pipeline and frontend
- Parallel transcoding via ProcessPoolExecutor with configurable worker count
- Idempotent re-runs: skips videos already transcoded unless source is newer
- Atomic manifest writes and safe staging-to-output publishing (works on k8s PVC mounts)
- Pre-flight disk space check and dry-run mode for previewing what would be transcoded
- Auto-skip for short titles (<60s) to filter junk MakeMKV menu extracts
- Dockerfile (Alpine + ffmpeg + Python) and k8s Job manifest with PVC mounts
- 43 pytest tests covering filename parsing, override merging, transcode logic, manifest validation, and publish workflow

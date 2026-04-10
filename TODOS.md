# TODOS

## Future Features

### Scene detection + DVD cover OCR matching
The DVD covers have scene *labels* (names like "Christmas Morning", "First Steps") but NOT timestamps. The full pipeline:
1. Scene detection splits the video into segments with timestamps
2. OCR/vision model extracts scene names from the cover JPG
3. Match detected scenes to cover labels

The cover is the key that names the scenes, video analysis provides the timestamps. Both scene splitting AND OCR are needed — neither alone is sufficient.

**Context:** Covers already exist in each MakeMKV output directory with printed scene lists. The existing iso→mkv pipeline already copies .jpg covers into the output directories.

### DVD cover UX refinement
Covers are valuable for the grandparent experience (recognizable cover art). V1 includes covers in the manifest and serves them via nginx, but the exact UX (detail panel, card overlay, player sidebar) should be refined once the basic app is working and can be tested with real users.

**Context:** User explicitly said covers are "very valuable for grandma to see as part of the experience." Want both video frame thumbnails AND covers visible. UX TBD. **Update from CEO review:** DVD grouping with cover art headers is now in V1 scope. This TODO is about further refinement beyond the group header layout.

**Partial progress:** v0.2.0.0 added a clickable cover lightbox — tapping the 60px cover thumbnail opens the full image in a lightbox modal (Escape/backdrop/× to close, focus trap, inert background). Solves the immediate "I can't read what's on this cover" problem since many covers are handwritten VHS labels. Future iterations could add a detail panel with the full DVD's video list, or sidebar context inside the player overlay.

### Full WCAG contrast audit (light + dark mode)
Run a systematic contrast check across all text/background combinations in both light and dark mode. The design review identified that muted text (#8B7E74) fails AA at 12-14px and added a darker alternative (#6B5E54) for small text. A full audit should verify all combinations meet WCAG AA, especially dark mode where the charcoal background + muted text ratio hasn't been tested.

**Context:** Design review Pass 6 found the issue. Eng review (re-run) unified `--text-muted` to #6B5E54 everywhere (passes AA at all sizes).

**Partial progress:** v0.1.2.0 /design-review audited and fixed: button text on amber (was 2.93, now 5.19 light / 6.47 dark via new `--on-accent` token), active timeline label (now warm-dark text with amber underline), duration badges tokenized for dark mode. Dark mode muted text `#A89B8F on #2D2824` verified at 5.38 via computed contrast.

**Remaining:** semantic colors (success/warning/error/info) on both light and dark backgrounds.
**Priority:** P3
**Depends on:** V1 frontend implementation (need actual rendered UI to test against).

### Play-all sequential mode
Button to play all videos from the selected year in sequence, auto-advancing when each video finishes. Lean-back viewing: grandma hits play and watches without clicking each video.

**Context:** Surfaced in CEO review, user chose to defer to post-V1. Natural fast-follow once the player overlay is solid. Effort: S. Edge cases to handle: video 404 mid-playlist (skip to next), resume on page reload (hash-based position tracking).
**Priority:** P2
**Depends on:** V1 player overlay working correctly.

## Frontend Eng Debt

### Bootstrap a frontend test framework
The frontend (vanilla JS/HTML/CSS) has zero automated tests. Backend `tests/` covers the transcode pipeline and filename parser (~100 pytest cases) but nothing exercises `frontend/app.js` — coverage audits during `/ship` are manual diagrams against live preview MCP runs. As `frontend/app.js` grows past 1100 lines with non-trivial state machines (drag cache, scroll-spy guard, deferred side effects, primary-year grouping, lightbox focus trap), the lack of automated coverage is increasingly load-bearing on manual verification.

**Context:** v0.2.0.0 ship surfaced this when the coverage audit could only produce a code-path diagram + user-flow checklist instead of running tests. The right tool is probably Vitest (unit) + Playwright (e2e against the dev proxy). Vitest can exercise pure functions (`getYearRange`, `nearestContentYear`, `formatDvdTitle`, `deriveYearRangeFromVideos`) immediately without DOM, and Playwright covers the drag/scroll/lightbox flows against real `manifest.json`.

**Priority:** P1
**Depends on:** Nothing — straight bootstrap.

### Massive DOM at archive scale
`renderAllSections()` renders every year section + every DVD group + every video card up front. Today's manifest (~74 videos across 20 years) keeps the full document at ~13,000px tall, which is fine. A larger archive (a few hundred videos across 40 years) would push that into territory where iPad/phone first-paint and GC pauses become noticeable.

**Context:** Codex adversarial review flagged it. The mitigation is virtualization: render only the year sections within ~2 viewports of the current scroll position, plus thumbnail lazy-load (already in place). Defer until a real scaling problem is observed — the current sparse-year condensing already cuts the section count by half.

**Priority:** P3
**Depends on:** Real perf measurement on a representative large archive.

### Override for the `19881123-1989325` 7-digit typo DVD — full range recovery
v0.2.2.0's Pattern 4.5 fallback now anchors this DVD at `1988-11-23` (the valid first token) instead of landing it in Undated — but it loses the `1989-3-25` end date that the user *meant*. Add an explicit override in `overrides.json` mapping this DVD to the intended `1988-11-23` – `1989-03-25` range to recover the full span.

**Context:** Adversarial review considered a YYYYMDD (single-digit month) fallback but rejected it — it would risk mis-parsing legitimate garbage strings. Override is the right tool here. 1 DVD. Priority downgraded from P3 to P4 since the video is no longer silently lost, just slightly less precise.
**Priority:** P4
**Depends on:** Nothing.

### Reduce 2-second programmatic-scroll guard ceiling
`scrollToYearSection` sets a 2000ms guard so the IntersectionObserver doesn't ping-pong during in-flight smooth scrolls. The native `scrollend` event clears it early on Chromium/Firefox, but Safari < 18 has no `scrollend` and pays the full 2 seconds. During those 2 seconds the scroll-spy is dead — if the user scrolls manually they don't see the spine update until the timer expires.

**Context:** Codex adversarial review. Lower-impact but real on Safari 17 and earlier. Fix: poll `window.scrollY` stabilization in a `requestAnimationFrame` loop and clear the guard when scroll position has held steady for 2 frames.

**Priority:** P3
**Depends on:** Nothing.

## Completed

### Drag cache invalidation on scrubber horizontal scroll
**Completed:** v0.2.2.0 (2026-04-09) — rewrote the drag cache to use scroll-invariant rail-relative coordinates so cached centers stay correct even when the scrubber scrolls mid-drag (no rebuild needed). Also added `ensureLabelVisible()` which nudges `scrubber.scrollLeft` when the drag target lands on an off-screen year, so the handle never leaves the visible scrubber. Original TODO proposed rebuilding the cache on every scroll event; the invariant-coordinate approach is strictly better.

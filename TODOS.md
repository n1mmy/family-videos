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

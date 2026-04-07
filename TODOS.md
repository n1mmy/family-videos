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

**Context:** User explicitly said covers are "very valuable for grandma to see as part of the experience." Want both video frame thumbnails AND covers visible. UX TBD.

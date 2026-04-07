# Design System — Family Videos

## Product Context
- **What this is:** A private family video timeline app for browsing ~50-100 digitized VHS tapes organized by year
- **Who it's for:** Grandparents and non-technical family members. The least technical person in the family is the primary user.
- **Space/industry:** Personal/family media viewing. Self-hosted on a home k8s cluster.
- **Project type:** Single-page web app (dedicated viewer, not a traditional SPA)

## Aesthetic Direction
- **Direction:** Family Album — warm minimal. The videos are the stars, everything else gets out of the way.
- **Decoration level:** Intentional — warm background tones, no decoration for its own sake. DVD covers and video thumbnails provide visual richness.
- **Mood:** Like opening a family photo album. Warm, personal, calm. Not cold tech, not retro-kitsch, not trendy.
- **Anti-patterns:** No purple gradients, no 3-column icon grids, no centered-everything layouts, no decorative blobs, no generic SaaS card grids.

## Typography
- **Display/Hero:** Instrument Serif — warm readable serif. Year numbers ("1994") feel like printed dates on a photo album page, not a tech UI. Load from Google Fonts.
- **Body:** DM Sans — clean, friendly, excellent readability at all sizes. Grandparents won't squint. Load from Google Fonts.
- **UI/Labels:** DM Sans (same as body)
- **Data/Tables:** DM Sans (tabular-nums) — for durations, video counts
- **Code:** Not applicable (no code-facing UI)
- **Loading:** Google Fonts CDN — `fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=Instrument+Serif`
- **Scale:**
  - 72px — year display (Instrument Serif)
  - 48px — large headings (Instrument Serif)
  - 32px — section headings (Instrument Serif)
  - 18px — body text (DM Sans)
  - 14px — small text, card titles (DM Sans)
  - 12px — metadata, labels (DM Sans)
  - 11px — badges, duration overlays (DM Sans, 500 weight)

## Color

### Light Mode
- **Approach:** Restrained — warm neutrals + one amber accent
- **Background:** #FAF7F2 — warm off-white, like aged album paper
- **Surface:** #FFFFFF — cards, overlays
- **Primary text:** #2C2420 — warm near-black (never pure #000)
- **Muted text:** #8B7E74 — warm gray for metadata, secondary info
- **Accent:** #C68B3F — amber/gold. Evokes film stock, warmth, old photographs. Used for active states, CTAs, timeline handle.
- **Accent hover:** #B07A32
- **Border:** #E8E2DA — warm light border for cards and dividers
- **Player overlay:** #1A1612 at 92% opacity — warm dark backdrop
- **Semantic:** success #4A7C59, warning #C68B3F, error #B85450, info #5B7FA5

### Dark Mode
- **Background:** #2D2824 — warm charcoal, like a cozy room at dusk
- **Surface:** #3A332D — elevated surfaces
- **Primary text:** #F0EBE3 — warm off-white
- **Muted text:** #A89B8F — warm mid-gray
- **Accent:** #D4A04A — slightly brighter amber for dark backgrounds
- **Accent hover:** #E0B35C
- **Border:** #4A4139 — warm dark border
- **Player overlay:** #1A1612 at 95% opacity
- **Strategy:** Warm charcoal base, slightly brighter accent to maintain contrast. Reduce saturation 10-20% on semantic colors.

## Spacing
- **Base unit:** 8px
- **Density:** Comfortable — grandparents need breathing room, not density
- **Scale:** xs(4) sm(8) md(16) lg(24) xl(32) 2xl(48) 3xl(64)
- **Card padding:** 12px (info area), 24px (component cards)
- **Grid gap:** 16px between video cards
- **Section gap:** 32px between DVD groups

## Layout
- **Approach:** Grid-disciplined — simple video card grid below sticky timeline
- **Grid:** `repeat(auto-fill, minmax(220px, 1fr))` for video cards
- **Max content width:** 1120px
- **Border radius:**
  - sm: 4px — cards, buttons, inputs
  - md: 8px — component cards, swatches
  - lg: 12px — mockup frames, large containers
  - full: 9999px — timeline handle, avatar circles
- **Zero chrome:** No nav bar, no sidebar, no hamburger menu, no settings icon. The timeline IS the only navigation.

## Motion
- **Approach:** Minimal-functional — calm and focused, not playful
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:**
  - micro: 100ms — button hover, focus ring
  - short: 150ms — timeline snap, card hover lift
  - medium: 250ms — player overlay fade, theme transition
  - long: 300ms — not commonly used
- **Specific animations:**
  - Timeline scrubber snap: 150ms ease-out
  - Thumbnail lazy-load: 200ms fade-in (opacity 0 to 1)
  - Player overlay open: 250ms fade backdrop + scale video from 0.95
  - Player overlay close: 200ms fade out
  - Card hover: translateY(-1px) + shadow elevation, 150ms
  - Theme toggle: 300ms background/color transition

## Shadows
- **Card:** `0 1px 3px rgba(44,36,32,0.06), 0 1px 2px rgba(44,36,32,0.04)`
- **Elevated (hover):** `0 4px 12px rgba(44,36,32,0.08), 0 2px 4px rgba(44,36,32,0.04)`
- **Dark mode card:** `0 1px 3px rgba(0,0,0,0.15), 0 1px 2px rgba(0,0,0,0.1)`
- **Dark mode elevated:** `0 4px 12px rgba(0,0,0,0.2), 0 2px 4px rgba(0,0,0,0.1)`

## Video-Specific
- **Aspect ratio:** 4:3 (VHS-era content, never crop to 16:9)
- **Duration badge:** bottom-right of thumbnail, `rgba(26,22,18,0.75)` background, white text, 11px DM Sans 500, 3px border-radius
- **Play icon on hover:** 40px white circle at 90% opacity, centered, with play triangle in primary text color. Hidden by default, appears on card hover with 150ms fade.
- **DVD cover placeholder:** gradient placeholder when no cover image exists
- **Thumbnail placeholder:** warm gradient (similar to the album paper tone) when image hasn't loaded or is missing

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-07 | Initial design system created | "Family Album" direction via /design-consultation. Warm minimal aesthetic to match family video content. |
| 2026-04-07 | Instrument Serif for display | Serif year numbers evoke printed photo album dates, differentiating from Jellyfin/Plex sans-serif UIs |
| 2026-04-07 | Amber accent (#C68B3F) | Warm film-stock tone instead of typical blue/purple tech accents. Avoids AI slop patterns. |
| 2026-04-07 | Zero chrome layout | Timeline is the only navigation. Radical simplicity for grandparent audience. |
| 2026-04-07 | Dark mode warm charcoal (#2D2824) | User feedback: initial dark mode (#1A1612) was too dark. Lifted to warm charcoal. |

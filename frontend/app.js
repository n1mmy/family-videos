// === CONFIG ===
var DEBOUNCE_MS = 150;
var RESIZE_DEBOUNCE_MS = 100;
// Programmatic scroll guard ceiling: long smooth-scrolls in Chromium can
// exceed 1s. Native `scrollend` clears the guard early when supported.
var PROGRAMMATIC_SCROLL_GUARD_MS = 2000;
var MANIFEST_URL = 'manifest.json';
var MAX_YEAR_SPAN = 100;

// === STATE ===
var state = {
  manifest: null,
  years: [],
  currentYear: null,
  // Span model: a multi-year DVD's videos appear in every year they touch.
  // Only used now for per-year density bars in the top timeline. User-facing
  // counts use state.dvdGroupsByYear (primary-year model) to avoid
  // double-counting multi-year DVDs.
  videosByYear: {},
  // Primary-year grouping: a DVD group appears exactly once, at the year
  // of its earliest video. Used for the editorial flow in the content column.
  dvdGroupsByYear: {},
  // Subset of state.years that have at least one DVD group anchored there.
  // Chevrons and keyboard navigation step through this, not state.years.
  yearsWithContent: [],
  maxVideosInYear: 0,
  playerVideoId: null,
  coverViewerOpen: false,
  lastFocusedCard: null,
  lastFocusedCover: null,
  isDragging: false,
  // Year → DOM element maps populated at build time. Lookups are O(1)
  // hash hits instead of CSS attribute selector walks during drag/scroll.
  labelByYear: {},
  sectionByYear: {},
  // Currently active DOM nodes for O(1) class toggling instead of
  // iterating every label and section on each year change.
  activeLabelEl: null,
  activeSectionEl: null,
  thumbnailObserver: null,
  sectionObserver: null,
  scrubberInitialized: false,
  chevronsInitialized: false,
  programmaticScrollUntil: 0,
  // Pending player-open timer from a deep-link hash; cleared on hashchange
  // so a stale video doesn't open after the user navigates away.
  pendingPlayerTimer: null
};

// === HELPERS ===
function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function formatDuration(seconds) {
  if (!seconds || !isFinite(seconds) || seconds < 0) return '--:--';
  var s = Math.floor(seconds);
  var h = Math.floor(s / 3600);
  var m = Math.floor((s % 3600) / 60);
  var sec = s % 60;
  if (h > 0) {
    return h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
  }
  return m + ':' + String(sec).padStart(2, '0');
}

function debounce(fn, ms) {
  var timer;
  return function() {
    clearTimeout(timer);
    timer = setTimeout(fn, ms);
  };
}

function getYearFromDate(dateStr) {
  if (!dateStr) return null;
  return parseInt(dateStr.substring(0, 4), 10);
}

function getYearRange(video) {
  var startYear = getYearFromDate(video.dateStart);
  var endYear = getYearFromDate(video.dateEnd);
  // If only one of the two dates parses, treat the video as a single
  // point at that year so density bars and the span model agree with
  // the primary-year grouping. Previously a video with only `dateStart`
  // returned ['undated'] and silently skipped its real numeric year.
  if (startYear === null && endYear === null) return ['undated'];
  if (startYear === null) return [endYear];
  if (endYear === null) return [startYear];
  if (startYear === endYear) return [startYear];
  if (endYear - startYear > MAX_YEAR_SPAN) return ['undated'];
  var range = [];
  for (var y = startYear; y <= endYear; y++) {
    range.push(y);
  }
  return range;
}

// Fallback when manifest.dateRange is missing or malformed: derive the
// (min, max) year span from video.dateStart values. Returns null if no
// videos have parseable dates. Clamped to [1900, currentYear+5] so a
// single bogus `99990101` value can't allocate 8000 years on the client.
function deriveYearRangeFromVideos(videos) {
  var min = null;
  var max = null;
  var FLOOR = 1900;
  var CEIL = new Date().getFullYear() + 5;
  for (var i = 0; i < videos.length; i++) {
    var sy = getYearFromDate(videos[i].dateStart);
    var ey = getYearFromDate(videos[i].dateEnd);
    if (sy !== null && sy >= FLOOR && sy <= CEIL && (min === null || sy < min)) min = sy;
    if (ey !== null && ey >= FLOOR && ey <= CEIL && (max === null || ey > max)) max = ey;
  }
  if (min === null || max === null) return null;
  return { start: min, end: max };
}

function findVideoById(id) {
  if (!state.manifest) return null;
  var videos = state.manifest.videos;
  for (var i = 0; i < videos.length; i++) {
    if (videos[i].id === id) return videos[i];
  }
  return null;
}

function announce(msg) {
  var el = $('#sr-announcements');
  if (el) el.textContent = msg;
}

function showEmptyLibraryState() {
  var container = $('#video-content');
  if (!container) return;
  container.innerHTML = '';
  var empty = document.createElement('div');
  empty.className = 'empty-library';
  empty.textContent = 'No videos in this library yet.';
  container.appendChild(empty);
  // Hide the spine + top timeline since there's nothing to anchor.
  var spine = $('.spine');
  if (spine) spine.classList.add('content-hidden');
  var topTl = $('.top-timeline');
  if (topTl) topTl.classList.add('content-hidden');
}

function prefersReducedMotion() {
  return matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// === MANIFEST ===
function fetchManifest() {
  $('#skeleton').style.display = '';
  $('#video-content').classList.add('content-hidden');
  $('#error-state').style.display = 'none';

  fetch(MANIFEST_URL)
    .then(function(res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.text();
    })
    .then(function(text) {
      var data;
      try { data = JSON.parse(text); } catch (e) { throw new Error('Invalid JSON'); }
      initApp(data);
    })
    .catch(function() {
      $('#skeleton').style.display = 'none';
      $('#error-state').style.display = 'flex';
    });
}

function initApp(data) {
  state.manifest = data;

  // Build year array from dateRange. Defensive: if dateRange is missing
  // or malformed, fall back to deriving the range from the videos
  // themselves so we never end up with an empty timeline that propagates
  // `undefined` into setYear/syncYearDisplay.
  var startYear = data.dateRange ? parseInt(data.dateRange.start, 10) : NaN;
  var endYear = data.dateRange ? parseInt(data.dateRange.end, 10) : NaN;
  if (!isFinite(startYear) || !isFinite(endYear)) {
    var derived = deriveYearRangeFromVideos(data.videos || []);
    if (derived) { startYear = derived.start; endYear = derived.end; }
  }
  state.years = [];
  if (isFinite(startYear) && isFinite(endYear)) {
    for (var y = startYear; y <= endYear; y++) {
      state.years.push(y);
    }
  }

  // Build videosByYear using span model
  state.videosByYear = {};
  for (var i = 0; i < state.years.length; i++) {
    state.videosByYear[state.years[i]] = [];
  }
  state.videosByYear['undated'] = [];

  var hasUndated = false;
  for (var j = 0; j < data.videos.length; j++) {
    var video = data.videos[j];
    var years = getYearRange(video);
    for (var k = 0; k < years.length; k++) {
      var yr = years[k];
      if (yr === 'undated') hasUndated = true;
      if (!state.videosByYear[yr]) state.videosByYear[yr] = [];
      state.videosByYear[yr].push(video);
    }
  }

  // Compute max videos in any year (for density bars)
  state.maxVideosInYear = 0;
  for (var i2 = 0; i2 < state.years.length; i2++) {
    var count = state.videosByYear[state.years[i2]].length;
    if (count > state.maxVideosInYear) state.maxVideosInYear = count;
  }

  // Add undated to year list if needed
  if (hasUndated) {
    state.years.push('undated');
  }

  // Build primary-year → DVD-groups map for the editorial flow.
  // Each DVD appears exactly once, anchored at the year of its EARLIEST
  // video, so a multi-year DVD doesn't duplicate across every year it
  // touches.
  //
  // Two passes are required: first walk every video to find the earliest
  // dateStart year per DVD, THEN walk again to bucket each DVD's full
  // video list under that single anchor year. The earlier single-pass
  // version keyed groups by `(perVideoYear, dvdId)` and silently split a
  // 2004-2006 DVD into three separate one-video groups in three separate
  // sections. (Caught by Codex adversarial review.)
  var dvdAnchorYear = {};
  for (var ev = 0; ev < data.videos.length; ev++) {
    var earlyVid = data.videos[ev];
    var ey = getYearFromDate(earlyVid.dateStart);
    if (ey === null) continue;
    if (!(earlyVid.dvd in dvdAnchorYear) || ey < dvdAnchorYear[earlyVid.dvd]) {
      dvdAnchorYear[earlyVid.dvd] = ey;
    }
  }

  var dvdByPrimaryYear = {};
  var dvdSeen = {};
  for (var vi = 0; vi < data.videos.length; vi++) {
    var vid = data.videos[vi];
    var anchor = (vid.dvd in dvdAnchorYear) ? dvdAnchorYear[vid.dvd] : 'undated';
    if (!dvdSeen[vid.dvd]) {
      dvdSeen[vid.dvd] = { dvd: vid.dvd, cover: vid.cover, videos: [] };
      if (!dvdByPrimaryYear[anchor]) dvdByPrimaryYear[anchor] = [];
      dvdByPrimaryYear[anchor].push(dvdSeen[vid.dvd]);
    }
    dvdSeen[vid.dvd].videos.push(vid);
  }
  state.dvdGroupsByYear = dvdByPrimaryYear;

  // Years with at least one DVD group anchored at them — used for chevron
  // stepping, keyboard nav, and the rendered section flow.
  state.yearsWithContent = state.years.filter(function(y) {
    return dvdByPrimaryYear[y] && dvdByPrimaryYear[y].length > 0;
  });

  // Show UI
  $('#skeleton').style.display = 'none';
  $('.top-timeline').classList.remove('content-hidden');
  $('.spine').classList.remove('content-hidden');
  $('#video-content').classList.remove('content-hidden');

  buildTimeline();
  renderAllSections();
  initSectionObserver();
  applyHash();
}

// === TIMELINE ===
function buildTimeline() {
  var labelsEl = $('.timeline-labels');
  labelsEl.innerHTML = '';

  // Reset the year → label map; createTimelineLabel populates it as we render.
  state.labelByYear = {};

  // Set slider ARIA bounds from the numeric year range (the scrubber
  // conceptually navigates the whole timeline, even though it only
  // lands on content years).
  var handle = $('.scrubber-handle');
  var numericYears = state.years.filter(function(y) { return y !== 'undated'; });
  if (numericYears.length > 0) {
    handle.setAttribute('aria-valuemin', numericYears[0]);
    handle.setAttribute('aria-valuemax', numericYears[numericYears.length - 1]);
  }

  // Walk state.years and collapse consecutive empty years into gap entries.
  // Local var — only used here to drive the render.
  var timelineItems = [];
  var contentSet = {};
  for (var ci = 0; ci < state.yearsWithContent.length; ci++) {
    contentSet[state.yearsWithContent[ci]] = true;
  }
  var i = 0;
  while (i < state.years.length) {
    var y = state.years[i];
    if (contentSet[y]) {
      timelineItems.push({ type: 'year', year: y });
      i++;
    } else {
      var gapStart = y;
      var gapEnd = y;
      while (i < state.years.length && !contentSet[state.years[i]]) {
        gapEnd = state.years[i];
        i++;
      }
      timelineItems.push({ type: 'gap', from: gapStart, to: gapEnd });
    }
  }

  for (var t = 0; t < timelineItems.length; t++) {
    var item = timelineItems[t];
    if (item.type === 'gap') {
      labelsEl.appendChild(createDiscontinuityMarker(item.from, item.to));
      continue;
    }
    labelsEl.appendChild(createTimelineLabel(item.year));
  }

  initScrubberDrag();
  initChevrons();
}

function createTimelineLabel(year) {
  var label = document.createElement('button');
  label.className = 'timeline-label';
  label.type = 'button';
  label.setAttribute('data-year', year);
  label.addEventListener('click', function() {
    setYear(year, { scroll: true });
  });

  // Density bar inside label (above the year text)
  if (year !== 'undated') {
    var bar = document.createElement('span');
    bar.className = 'density-bar';
    var count = (state.videosByYear[year] || []).length;
    if (count === 0) {
      bar.style.height = '0px';
    } else {
      var h = Math.max(2, Math.round((count / state.maxVideosInYear) * 12));
      bar.style.height = h + 'px';
    }
    label.appendChild(bar);
  }

  var text = document.createElement('span');
  text.textContent = year === 'undated' ? 'Undated' : year;
  label.appendChild(text);

  // Register for O(1) lookups in positionHandle, syncYearDisplay, etc.
  state.labelByYear[year] = label;
  return label;
}

function createDiscontinuityMarker(fromYear, toYear) {
  var marker = document.createElement('span');
  marker.className = 'timeline-discontinuity';
  marker.setAttribute('aria-hidden', 'true');
  var rangeLabel = (fromYear === toYear)
    ? fromYear + ' — no videos'
    : fromYear + '–' + toYear + ' — no videos';
  marker.setAttribute('title', rangeLabel);
  marker.textContent = '\u00B7\u00B7\u00B7'; // three middle dots
  return marker;
}

// Update spine + scrubber + labels display for a given year. Does NOT scroll.
function syncYearDisplay(year) {
  // Guard against empty-manifest paths that would otherwise propagate
  // `undefined` into spine.textContent ("undefined" rendered in 220px
  // serif), the URL hash, the SR announcement, etc.
  if (year == null) return;
  if (year === state.currentYear) return;
  state.currentYear = year;

  // Spine year
  var spine = $('#spine-year');
  if (spine) {
    spine.textContent = year === 'undated' ? 'Undated' : year;
    spine.classList.toggle('spine-year--undated', year === 'undated');
  }

  // Scrubber handle position
  positionHandle(year);

  // Toggle active class on the timeline label in O(1) via the year→el map.
  // Iterating every label per drag tick was the second-largest jank source.
  var nextLabel = state.labelByYear[year] || null;
  if (state.activeLabelEl !== nextLabel) {
    if (state.activeLabelEl) state.activeLabelEl.classList.remove('active');
    if (nextLabel) nextLabel.classList.add('active');
    state.activeLabelEl = nextLabel;
  }
  if (nextLabel && !state.isDragging) {
    nextLabel.scrollIntoView({
      block: 'nearest',
      inline: 'center',
      behavior: prefersReducedMotion() ? 'auto' : 'smooth'
    });
  }

  // Same O(1) treatment for the year section's active highlight.
  var nextSection = state.sectionByYear[year] || null;
  if (state.activeSectionEl !== nextSection) {
    if (state.activeSectionEl) state.activeSectionEl.classList.remove('active');
    if (nextSection) nextSection.classList.add('active');
    state.activeSectionEl = nextSection;
  }

  // ARIA
  var handle = $('.scrubber-handle');
  if (year !== 'undated') {
    handle.setAttribute('aria-valuenow', year);
    handle.setAttribute('aria-valuetext', year);
  }

  // Update chevron disabled state
  updateChevronState();

  // History + screen-reader announcement: defer during drag so we don't
  // hit the history API and re-write the aria-live node on every pointer
  // move. Both fire once on drag end inside initScrubberDrag's onEnd().
  if (!state.isDragging) {
    announceYearChange(year);
    syncHashForYear(year);
  }
}

function announceYearChange(year) {
  var primaryGroups = state.dvdGroupsByYear[year] || [];
  var count = 0;
  for (var pg = 0; pg < primaryGroups.length; pg++) {
    count += primaryGroups[pg].videos.length;
  }
  var yearLabel = year === 'undated' ? 'undated' : year;
  announce('Showing ' + count + ' video' + (count !== 1 ? 's' : '') + ' from ' + yearLabel);
}

function syncHashForYear(year) {
  var newHash = year === 'undated' ? '' : '#' + year;
  if (window.location.hash !== newHash) {
    history.replaceState(null, '', newHash || window.location.pathname);
  }
}

// User-initiated year change: sync display AND optionally scroll.
// Callers explicitly pass `scroll: false` during drag — scrollToYearSection
// no longer second-guesses its caller.
function setYear(year, opts) {
  opts = opts || {};
  syncYearDisplay(year);
  if (opts.scroll) {
    scrollToYearSection(year);
  }
}

function scrollToYearSection(year) {
  var section = state.sectionByYear[year] || null;
  if (!section) return;
  // Mark scroll as programmatic so the IntersectionObserver doesn't
  // ping-pong the spine while we're smooth-scrolling. Native `scrollend`
  // (when supported) clears the guard early; the ceiling is the fallback.
  state.programmaticScrollUntil = Date.now() + PROGRAMMATIC_SCROLL_GUARD_MS;
  section.scrollIntoView({
    block: 'start',
    behavior: prefersReducedMotion() ? 'auto' : 'smooth'
  });
}

// Position the scrubber handle by reading the actual DOM center of the
// target year label. Flex `space-between` does not space label *centers*
// linearly when labels have different widths (e.g. "1979" vs "Undated"),
// so index-based math gets it wrong. Reading the layout keeps the handle
// pinned to the label the user sees.
function positionHandle(year) {
  var handle = $('.scrubber-handle');
  var track = $('.scrubber-track');
  if (!handle || !track) return;
  var label = state.labelByYear[year] || null;
  if (!label) { handle.style.left = '0%'; return; }
  var lr = label.getBoundingClientRect();
  var tr = track.getBoundingClientRect();
  if (tr.width <= 0) { handle.style.left = '0%'; return; }
  var centerX = lr.left + lr.width / 2;
  var pct = ((centerX - tr.left) / tr.width) * 100;
  pct = Math.max(0, Math.min(100, pct));
  handle.style.left = pct + '%';
}

function initScrubberDrag() {
  if (state.scrubberInitialized) return;
  state.scrubberInitialized = true;
  var handle = $('.scrubber-handle');
  var track = $('.scrubber-track');

  // Drag-session cache: snapshot every content label's center X plus
  // the track rect at mousedown so onMove does ZERO layout reads per
  // pointer move. Without this, the previous tick's `handle.style.left`
  // write forces a synchronous layout flush on the next read — classic
  // read-after-write thrash that visibly janks drag on slower hardware.
  var dragCache = null;

  function buildDragCache() {
    var cache = { years: [], centers: [] };
    for (var year in state.labelByYear) {
      if (!Object.prototype.hasOwnProperty.call(state.labelByYear, year)) continue;
      var lr = state.labelByYear[year].getBoundingClientRect();
      cache.years.push(year === 'undated' ? 'undated' : parseInt(year, 10));
      cache.centers.push(lr.left + lr.width / 2);
    }
    return cache;
  }

  // Find the content-year whose cached center is closest to the pointer.
  // Gaps are absent from state.labelByYear, so dragging always snaps to
  // a real content year even when the pointer lands visually on a "···".
  function getYearFromPointer(clientX) {
    var cache = dragCache || buildDragCache();
    if (cache.centers.length === 0) return null;
    var bestIdx = -1;
    var bestDist = Infinity;
    for (var i = 0; i < cache.centers.length; i++) {
      var d = Math.abs(clientX - cache.centers[i]);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    }
    return bestIdx === -1 ? null : cache.years[bestIdx];
  }

  function onMove(clientX) {
    if (!state.isDragging) return;
    var year = getYearFromPointer(clientX);
    if (year != null && year !== state.currentYear) {
      handle.classList.add('dragging');
      // scroll: false during drag — onEnd handles the final scroll once,
      // and the deferred announce/hash side effects fire there too.
      setYear(year, { scroll: false });
    }
  }

  function onEnd() {
    state.isDragging = false;
    handle.classList.remove('dragging');
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onEnd);
    document.removeEventListener('touchmove', onTouchMove);
    document.removeEventListener('touchend', onEnd);
    dragCache = null;
    // Flush the side effects we deferred during drag, then scroll once
    // to the year the user actually landed on.
    if (state.currentYear != null) {
      announceYearChange(state.currentYear);
      syncHashForYear(state.currentYear);
      scrollToYearSection(state.currentYear);
    }
  }

  function onMouseMove(e) { onMove(e.clientX); }
  function onTouchMove(e) { e.preventDefault(); onMove(e.touches[0].clientX); }

  handle.addEventListener('mousedown', function(e) {
    e.preventDefault();
    state.isDragging = true;
    dragCache = buildDragCache();
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onEnd);
  });

  handle.addEventListener('touchstart', function(e) {
    e.preventDefault();
    state.isDragging = true;
    dragCache = buildDragCache();
    document.addEventListener('touchmove', onTouchMove, { passive: false });
    document.addEventListener('touchend', onEnd);
  });

  // Click on track to jump — one-shot, no cache needed.
  track.addEventListener('click', function(e) {
    var year = getYearFromPointer(e.clientX);
    if (year != null) setYear(year, { scroll: true });
  });
}

// === CHEVRONS (top bar + spine) ===
function initChevrons() {
  // Guard against listener stacking on manifest retry — without this,
  // every retry doubles the click handlers and chevron presses end up
  // skipping multiple years.
  if (state.chevronsInitialized) {
    updateChevronState();
    return;
  }
  state.chevronsInitialized = true;
  var prevBtns = [$('.timeline-chevron-left'), $('.spine-chevron-up')];
  var nextBtns = [$('.timeline-chevron-right'), $('.spine-chevron-down')];

  for (var i = 0; i < prevBtns.length; i++) {
    if (prevBtns[i]) prevBtns[i].addEventListener('click', stepPrev);
  }
  for (var j = 0; j < nextBtns.length; j++) {
    if (nextBtns[j]) nextBtns[j].addEventListener('click', stepNext);
  }
  updateChevronState();
}

// Step the current year by `delta` (-1 = previous, +1 = next) within
// state.yearsWithContent. If currentYear isn't in the content set
// (e.g., the user navigated to an empty year via hash), snap to the
// nearest content year first instead of stepping past it.
function stepYear(delta) {
  var years = state.yearsWithContent;
  var idx = years.indexOf(state.currentYear);
  if (idx === -1) {
    var nearest = nearestContentYear(state.currentYear);
    if (nearest !== null && nearest !== state.currentYear) {
      setYear(nearest, { scroll: true });
    }
    return;
  }
  var targetIdx = idx + delta;
  if (targetIdx >= 0 && targetIdx < years.length) {
    setYear(years[targetIdx], { scroll: true });
  }
}

function stepPrev() { stepYear(-1); }
function stepNext() { stepYear(1); }

function updateChevronState() {
  var years = state.yearsWithContent;
  var idx = years.indexOf(state.currentYear);
  var atStart = idx <= 0;
  var atEnd = idx < 0 || idx >= years.length - 1;
  var prev = [$('.timeline-chevron-left'), $('.spine-chevron-up')];
  var next = [$('.timeline-chevron-right'), $('.spine-chevron-down')];
  for (var i = 0; i < prev.length; i++) {
    if (prev[i]) prev[i].disabled = atStart;
  }
  for (var j = 0; j < next.length; j++) {
    if (next[j]) next[j].disabled = atEnd;
  }
}

// === GRID: render year sections for every year WITH content ===
// Years with zero primary-anchored DVD groups are omitted from the flow,
// so the editorial column stays continuous. The timeline still shows every
// year as a tick mark, and clicking an empty year scrolls to the nearest
// year with content. state.sectionByYear is rebuilt here so scroll/lookup
// hot paths can hit a hash instead of walking the DOM.
function renderAllSections() {
  var container = $('#video-content');
  container.innerHTML = '';
  state.sectionByYear = {};

  for (var i = 0; i < state.yearsWithContent.length; i++) {
    var year = state.yearsWithContent[i];
    var groups = state.dvdGroupsByYear[year];

    var section = document.createElement('section');
    section.className = 'year-section';
    section.setAttribute('data-year', year);

    var heading = document.createElement('h2');
    heading.className = 'year-section-heading';
    heading.textContent = year === 'undated' ? 'Undated' : year;
    section.appendChild(heading);

    for (var g = 0; g < groups.length; g++) {
      var group = groups[g];
      var groupEl = document.createElement('div');
      groupEl.className = 'dvd-group';

      var header = document.createElement('div');
      header.className = 'dvd-group-header';

      var coverEl = createCoverElement(group.cover, group.dvd);
      header.appendChild(coverEl);

      var titleEl = document.createElement('span');
      titleEl.className = 'dvd-group-title';
      titleEl.textContent = formatDvdTitle(group.dvd);
      header.appendChild(titleEl);

      groupEl.appendChild(header);

      var grid = document.createElement('div');
      grid.className = 'video-grid';

      for (var v = 0; v < group.videos.length; v++) {
        var card = createVideoCard(group.videos[v]);
        grid.appendChild(card);
      }

      groupEl.appendChild(grid);
      section.appendChild(groupEl);
    }

    container.appendChild(section);
    state.sectionByYear[year] = section;
  }

  observeThumbnails();
}

// Return the nearest year in state.yearsWithContent to a given year.
// Used when the user clicks an "empty" year label or navigates to one
// via hash — we jump to the nearest actual content rather than showing
// an empty page.
function nearestContentYear(year) {
  var content = state.yearsWithContent;
  if (content.length === 0) return null;
  if (content.indexOf(year) !== -1) return year;
  if (year === 'undated') {
    // Undated isn't in the content set (otherwise we'd have returned
    // above). Fall back to the last *numeric* content year, or undated
    // itself if that's all there is.
    for (var u = content.length - 1; u >= 0; u--) {
      if (content[u] !== 'undated') return content[u];
    }
    return 'undated';
  }
  // Numeric year — find the nearest numeric content year by distance.
  var best = null;
  var bestDist = Infinity;
  for (var i = 0; i < content.length; i++) {
    var c = content[i];
    if (c === 'undated') continue;
    var d = Math.abs(year - c);
    if (d < bestDist) { best = c; bestDist = d; }
  }
  // If the only content is undated, fall back to it.
  return best !== null ? best : 'undated';
}

// === SCROLL-SPY: which year section is currently anchored ===
function initSectionObserver() {
  if (state.sectionObserver) {
    state.sectionObserver.disconnect();
    state.sectionObserver = null;
  }
  if (!('IntersectionObserver' in window)) return;

  // Read the actual sticky top-bar height from the CSS custom property
  // so the anchor line follows the value used in CSS (96px desktop,
  // 140px mobile). A hardcoded 120px would flip the active year too
  // early on mobile.
  var rootStyles = getComputedStyle(document.documentElement);
  var topBarPxStr = rootStyles.getPropertyValue('--top-bar-height').trim();
  var topBarPx = parseInt(topBarPxStr, 10) || 96;
  // Anchor line sits ~24px below the sticky top bar.
  var anchorTop = topBarPx + 24;

  // We track the set of currently-intersecting sections across callbacks
  // (IntersectionObserver entries only contain sections whose state
  // CHANGED, not all currently visible). On every callback we update our
  // local set, then pick the topmost visible section as the current year.
  var visibleSet = {};

  var observer = new IntersectionObserver(function(entries) {
    for (var e = 0; e < entries.length; e++) {
      var entry = entries[e];
      var year = entry.target.getAttribute('data-year');
      if (entry.isIntersecting) {
        visibleSet[year] = entry.target;
      } else {
        delete visibleSet[year];
      }
    }

    // If we're mid-programmatic-scroll, let setYear own the state.
    if (Date.now() < state.programmaticScrollUntil) return;

    // Pick the topmost section currently in the anchor band.
    var bestYear = null;
    var bestTop = Infinity;
    for (var y in visibleSet) {
      if (!Object.prototype.hasOwnProperty.call(visibleSet, y)) continue;
      var rect = visibleSet[y].getBoundingClientRect();
      if (rect.top < bestTop) {
        bestTop = rect.top;
        bestYear = y;
      }
    }
    if (bestYear !== null && bestYear !== String(state.currentYear)) {
      var next = bestYear === 'undated' ? 'undated' : parseInt(bestYear, 10);
      syncYearDisplay(next);
    }
  }, {
    // Top-anchor line ~24px below the sticky top bar; the bottom 70% of
    // the viewport is excluded so a section stays "current" until its
    // bottom crosses the 30% mark and the next section pushes through.
    rootMargin: '-' + anchorTop + 'px 0px -70% 0px',
    threshold: 0
  });

  var sections = $$('.year-section');
  for (var i = 0; i < sections.length; i++) {
    observer.observe(sections[i]);
  }
  state.sectionObserver = observer;
}

function createCoverElement(coverUrl, dvdId) {
  if (!coverUrl) return createCoverMonogram(dvdId);
  var title = formatDvdTitle(dvdId);

  // Wrap in a button so the tiny thumbnail is a first-class tap target
  // with keyboard support; click opens a full-size lightbox for jpegs
  // that are too detailed to read at 60px.
  var btn = document.createElement('button');
  btn.className = 'dvd-cover-button';
  btn.type = 'button';
  btn.setAttribute('aria-label', 'View ' + title + ' cover');

  var img = document.createElement('img');
  img.className = 'dvd-cover';
  img.alt = title + ' cover';
  img.src = coverUrl;
  img.onerror = function() { btn.replaceWith(createCoverMonogram(dvdId)); };
  btn.appendChild(img);

  btn.addEventListener('click', function() {
    openCoverViewer(coverUrl, title);
  });

  return btn;
}

function createCoverMonogram(dvdId) {
  var mono = document.createElement('div');
  mono.className = 'dvd-cover-monogram';
  var title = formatDvdTitle(dvdId);
  // Find the first letter in the formatted title; fall back to a neutral symbol
  // when the title is purely numeric (dates) so grandparents don't see "1".
  var firstLetter = title.match(/[A-Za-z]/);
  mono.textContent = firstLetter ? firstLetter[0].toUpperCase() : '\u25A0';
  mono.setAttribute('aria-hidden', 'true');
  return mono;
}

var MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function parseDatePart(part) {
  // Accept YYYY, YYYYMM, or YYYYMMDD; return {year, month?, day?} or null
  if (/^\d{4}$/.test(part)) return { year: parseInt(part, 10) };
  if (/^\d{6}$/.test(part)) {
    var m = parseInt(part.slice(4, 6), 10);
    if (m < 1 || m > 12) return null;
    return { year: parseInt(part.slice(0, 4), 10), month: m };
  }
  if (/^\d{8}$/.test(part)) {
    var m2 = parseInt(part.slice(4, 6), 10);
    var d = parseInt(part.slice(6, 8), 10);
    if (m2 < 1 || m2 > 12 || d < 1 || d > 31) return null;
    return { year: parseInt(part.slice(0, 4), 10), month: m2, day: d };
  }
  return null;
}

function formatDatePart(p) {
  if (!p) return '';
  if (p.month) return MONTH_NAMES[p.month - 1] + ' ' + p.year;
  return String(p.year);
}

function formatDvdTitle(dvdId) {
  // Numeric date range: YYYYMM-YYYYMM or YYYYMMDD-YYYYMMDD → "Feb 1979 – Jan 1982"
  var rangeMatch = dvdId.match(/^(\d{4,8})-(\d{4,8})$/);
  if (rangeMatch) {
    var start = parseDatePart(rangeMatch[1]);
    var end = parseDatePart(rangeMatch[2]);
    if (start && end) {
      var s = formatDatePart(start);
      var e = formatDatePart(end);
      return s === e ? s : s + ' \u2013 ' + e;
    }
  }
  // Default: replace dashes with spaces and title-case words
  return dvdId.replace(/-/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
}

function createVideoCard(video) {
  var card = document.createElement('div');
  card.className = 'video-card';
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  card.setAttribute('aria-label', 'Play ' + video.title + ', ' + formatDuration(video.duration));
  card.setAttribute('data-video-id', video.id);

  // Thumbnail wrapper
  var thumb = document.createElement('div');
  thumb.className = 'video-card-thumb';

  // Placeholder icon
  var placeholder = document.createElement('div');
  placeholder.className = 'thumb-placeholder';
  placeholder.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="4" width="20" height="16" rx="2"/><circle cx="12" cy="12" r="3"/><path d="M2 8h2M20 8h2"/></svg>';
  thumb.appendChild(placeholder);

  // Lazy image
  var img = document.createElement('img');
  img.setAttribute('data-src', video.thumbnail);
  img.alt = video.title;
  img.onload = function() { img.classList.add('loaded'); };
  img.onerror = function() { img.style.display = 'none'; };
  thumb.appendChild(img);

  // Duration badge
  var badge = document.createElement('span');
  badge.className = 'duration-badge';
  badge.textContent = formatDuration(video.duration);
  thumb.appendChild(badge);

  // Play icon
  var playIcon = document.createElement('div');
  playIcon.className = 'play-icon';
  playIcon.innerHTML = '<svg viewBox="0 0 24 24"><polygon points="8,5 19,12 8,19"/></svg>';
  thumb.appendChild(playIcon);

  card.appendChild(thumb);

  // Info
  var info = document.createElement('div');
  info.className = 'video-card-info';
  var title = document.createElement('div');
  title.className = 'video-card-title';
  title.textContent = video.title;
  info.appendChild(title);
  card.appendChild(info);

  // Click handler
  card.addEventListener('click', function() { openPlayer(video.id); });

  return card;
}

// === PLAYER ===
function openPlayer(videoId) {
  var video = findVideoById(videoId);
  if (!video) return;

  state.playerVideoId = videoId;
  state.lastFocusedCard = document.activeElement;

  var overlay = $('#player-overlay');
  var videoEl = $('#player-video');
  var errorEl = $('#player-error');

  errorEl.classList.remove('visible');
  videoEl.style.display = '';
  videoEl.poster = video.thumbnail;
  videoEl.src = video.file;

  videoEl.onerror = function() {
    videoEl.style.display = 'none';
    errorEl.classList.add('visible');
  };

  overlay.classList.add('opening');
  requestAnimationFrame(function() {
    overlay.classList.add('visible');
    overlay.classList.remove('opening');
  });

  videoEl.play().catch(function() {});

  // Update hash
  history.pushState(null, '', '#' + videoId);

  // Mark the rest of the page inert so assistive tech and mouse focus
  // cannot escape the modal into background content while it's open.
  var appEl = $('.app');
  if (appEl && 'inert' in appEl) appEl.inert = true;

  // Focus close button and trap focus within overlay
  $('#player-close-btn').focus();
  overlay.addEventListener('keydown', trapFocus);

  announce('Playing ' + video.title);
}

function trapFocus(e) {
  if (e.key !== 'Tab') return;
  var overlay = $('#player-overlay');
  var focusable = overlay.querySelectorAll('button, video, [tabindex]:not([tabindex="-1"])');
  if (!focusable.length) return;
  var first = focusable[0];
  var last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

// === COVER VIEWER (lightbox for DVD cover jpegs) ===
function openCoverViewer(coverUrl, title) {
  var overlay = $('#cover-overlay');
  var img = $('#cover-image');
  if (!overlay || !img) return;

  state.coverViewerOpen = true;
  state.lastFocusedCover = document.activeElement;

  img.src = coverUrl;
  img.alt = title + ' cover';

  overlay.classList.add('opening');
  requestAnimationFrame(function() {
    overlay.classList.add('visible');
    overlay.classList.remove('opening');
  });

  // Mark the rest of the page inert so focus/AT can't escape the modal
  // into background content while the lightbox is open. The Tab trap
  // below is defense in depth for older Safari (no `inert` until 15.5).
  var appEl = $('.app');
  if (appEl && 'inert' in appEl) appEl.inert = true;
  overlay.addEventListener('keydown', trapFocusInCoverViewer);

  $('#cover-close-btn').focus();
  announce('Viewing ' + title + ' cover');
}

function closeCoverViewer() {
  if (!state.coverViewerOpen) return;
  var overlay = $('#cover-overlay');
  var img = $('#cover-image');
  overlay.classList.remove('visible');
  overlay.classList.remove('opening');
  overlay.removeEventListener('keydown', trapFocusInCoverViewer);
  state.coverViewerOpen = false;

  // Drop the cover image src so the GC can release the decoded bitmap
  // (a few hundred KB per cover; pile up after browsing many).
  if (img) img.removeAttribute('src');

  var appEl = $('.app');
  if (appEl && 'inert' in appEl) appEl.inert = false;

  if (state.lastFocusedCover && state.lastFocusedCover.isConnected) {
    // preventScroll keeps the page from yanking the originating cover
    // back into view if it scrolled offscreen behind the lightbox.
    try { state.lastFocusedCover.focus({ preventScroll: true }); }
    catch (e) { state.lastFocusedCover.focus(); }
  }
  state.lastFocusedCover = null;
}

// Tab trap for the cover viewer — fallback for browsers without `inert`
// support and defense in depth alongside `inert`. The viewer only contains
// the close button as a focusable element, so any Tab keeps focus there.
function trapFocusInCoverViewer(e) {
  if (e.key !== 'Tab') return;
  e.preventDefault();
  var btn = $('#cover-close-btn');
  if (btn) btn.focus();
}

function closePlayer() {
  if (!state.playerVideoId) return;

  var overlay = $('#player-overlay');
  var videoEl = $('#player-video');

  videoEl.pause();
  videoEl.onerror = null;
  videoEl.removeAttribute('src');
  videoEl.load();

  overlay.classList.remove('visible');
  overlay.removeEventListener('keydown', trapFocus);
  state.playerVideoId = null;

  // Re-enable the rest of the page now that the modal is closed.
  var appEl = $('.app');
  if (appEl && 'inert' in appEl) appEl.inert = false;

  // Restore hash to current year (replaceState avoids polluting back button)
  var yearHash = state.currentYear && state.currentYear !== 'undated'
    ? '#' + state.currentYear : '';
  history.replaceState(null, '', yearHash || window.location.pathname);

  // Return focus
  if (state.lastFocusedCard && state.lastFocusedCard.isConnected) {
    state.lastFocusedCard.focus();
  }
  state.lastFocusedCard = null;

  announce('Video closed');
}

// === THUMBNAILS ===
function observeThumbnails() {
  if (state.thumbnailObserver) {
    state.thumbnailObserver.disconnect();
    state.thumbnailObserver = null;
  }
  var images = $$('.video-card-thumb img[data-src]');
  if (!images.length) return;

  if ('IntersectionObserver' in window) {
    var observer = new IntersectionObserver(function(entries) {
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].isIntersecting) {
          var img = entries[i].target;
          img.src = img.getAttribute('data-src');
          img.removeAttribute('data-src');
          observer.unobserve(img);
        }
      }
    }, { rootMargin: '200px' });

    for (var i = 0; i < images.length; i++) {
      observer.observe(images[i]);
    }
    state.thumbnailObserver = observer;
  } else {
    // Eager fallback
    for (var j = 0; j < images.length; j++) {
      images[j].src = images[j].getAttribute('data-src');
      images[j].removeAttribute('data-src');
    }
  }
}

// Preconnect hint on hover
document.addEventListener('mouseover', function(e) {
  var card = e.target.closest('.video-card');
  if (!card) return;
  var videoId = card.getAttribute('data-video-id');
  var video = findVideoById(videoId);
  if (!video) return;
  // Add preconnect for video URL origin if relative, skip
  try {
    var url = new URL(video.file, window.location.href);
    if (url.origin !== window.location.origin) {
      var existing = document.querySelector('link[rel="preconnect"][href="' + url.origin + '"]');
      if (!existing) {
        var link = document.createElement('link');
        link.rel = 'preconnect';
        link.href = url.origin;
        document.head.appendChild(link);
      }
    }
  } catch (e) {}
});

// === DEEP LINKING ===
function applyHash() {
  var hash = window.location.hash.replace('#', '');
  var firstContentYear = state.yearsWithContent[0] || state.years[0];

  // Empty-library guard: nothing to navigate to. Render an empty-state
  // message in the content column instead of routing `undefined` through
  // the year machinery.
  if (firstContentYear == null) {
    showEmptyLibraryState();
    return;
  }

  if (!hash) {
    setYear(firstContentYear, { scroll: false });
    return;
  }

  // 4-digit year check
  if (/^\d{4}$/.test(hash)) {
    var year = parseInt(hash, 10);
    // Snap to nearest year with content so empty-year deep links still
    // land on something visible.
    var target = nearestContentYear(year);
    if (target !== null) {
      setYear(target, { scroll: true });
    } else {
      setYear(firstContentYear, { scroll: false });
    }
    return;
  }

  // Video ID lookup
  var video = findVideoById(hash);
  if (video) {
    var videoYear = getYearFromDate(video.dateStart) || 'undated';
    var targetYear = nearestContentYear(videoYear) || firstContentYear;
    setYear(targetYear, { scroll: true });
    // Delay player open slightly so grid renders first. Track the timer
    // so a fast hashchange can cancel a stale player open.
    if (state.pendingPlayerTimer) clearTimeout(state.pendingPlayerTimer);
    state.pendingPlayerTimer = setTimeout(function() {
      state.pendingPlayerTimer = null;
      openPlayer(hash);
    }, DEBOUNCE_MS + 50);
  } else {
    setYear(firstContentYear, { scroll: false });
  }
}

window.addEventListener('hashchange', function() {
  var hash = window.location.hash.replace('#', '');

  // Cancel any in-flight pending player open from a prior deep link;
  // otherwise a fast back-button could open the stale video.
  if (state.pendingPlayerTimer) {
    clearTimeout(state.pendingPlayerTimer);
    state.pendingPlayerTimer = null;
  }

  // If player is open and hash no longer matches a video, close player
  if (state.playerVideoId && hash !== state.playerVideoId) {
    closePlayer();
  }

  if (!state.playerVideoId) {
    applyHash();
  }
});

// === KEYBOARD ===
document.addEventListener('keydown', function(e) {
  // Escape closes whichever modal is on top (cover viewer beats player
  // because it only opens from a non-modal surface).
  if (e.key === 'Escape' && state.coverViewerOpen) {
    closeCoverViewer();
    e.preventDefault();
    return;
  }
  if (e.key === 'Escape' && state.playerVideoId) {
    closePlayer();
    e.preventDefault();
    return;
  }

  // Don't interfere when a modal is open or when typing in an input
  if (state.coverViewerOpen) return;
  if (state.playerVideoId) return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

  if (e.key === 'ArrowLeft') {
    e.preventDefault();
    stepPrev();
    return;
  }

  if (e.key === 'ArrowRight') {
    e.preventDefault();
    stepNext();
    return;
  }

  // Enter/Space on video card
  if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('video-card')) {
    e.preventDefault();
    var videoId = e.target.getAttribute('data-video-id');
    if (videoId) openPlayer(videoId);
  }
});

// Keep the handle pinned to its label's DOM center when the window resizes,
// since positionHandle() reads layout coordinates.
window.addEventListener('resize', debounce(function() {
  if (state.currentYear != null) positionHandle(state.currentYear);
}, RESIZE_DEBOUNCE_MS));

// Clear the programmatic-scroll guard as soon as the browser reports the
// user-initiated scroll has finished. This lets the scroll-spy resume
// updating immediately without waiting for the 2s ceiling fallback.
if ('onscrollend' in window) {
  window.addEventListener('scrollend', function() {
    state.programmaticScrollUntil = 0;
  });
}

// === INIT ===
$('#retry-btn').addEventListener('click', fetchManifest);
$('#player-close-btn').addEventListener('click', closePlayer);
$('#player-error-close').addEventListener('click', closePlayer);

// Cover viewer: dedicated close button + click-on-backdrop to dismiss.
// The image itself swallows the click so clicking the cover art doesn't
// close; only the surrounding backdrop does.
$('#cover-close-btn').addEventListener('click', closeCoverViewer);
$('#cover-overlay').addEventListener('click', function(e) {
  if (e.target === this) closeCoverViewer();
});

fetchManifest();

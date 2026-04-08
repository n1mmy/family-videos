// === CONFIG ===
var DEBOUNCE_MS = 150;
var MANIFEST_URL = 'manifest.json';
var MAX_YEAR_SPAN = 100;

// === STATE ===
var state = {
  manifest: null,
  years: [],
  currentYear: null,
  videosByYear: {},
  // Primary-year grouping: a DVD group appears exactly once, at the year
  // of its earliest video. Used for the editorial flow in the content column.
  dvdGroupsByYear: {},
  // Subset of state.years that have at least one DVD group anchored there.
  // Chevrons and keyboard navigation step through this, not state.years.
  yearsWithContent: [],
  maxVideosInYear: 0,
  playerVideoId: null,
  lastFocusedCard: null,
  isDragging: false,
  numericYears: [],
  thumbnailObserver: null,
  sectionObserver: null,
  scrubberInitialized: false,
  programmaticScrollUntil: 0
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
  if (startYear === null || endYear === null) return ['undated'];
  if (startYear === endYear) return [startYear];
  if (endYear - startYear > MAX_YEAR_SPAN) return ['undated'];
  var range = [];
  for (var y = startYear; y <= endYear; y++) {
    range.push(y);
  }
  return range;
}

function groupByDvd(videos) {
  var groups = [];
  var groupMap = {};
  for (var i = 0; i < videos.length; i++) {
    var v = videos[i];
    if (!groupMap[v.dvd]) {
      var group = { dvd: v.dvd, cover: v.cover, videos: [] };
      groupMap[v.dvd] = group;
      groups.push(group);
    }
    groupMap[v.dvd].videos.push(v);
  }
  return groups;
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

  // Build year array from dateRange
  var startYear = parseInt(data.dateRange.start, 10);
  var endYear = parseInt(data.dateRange.end, 10);
  state.years = [];
  for (var y = startYear; y <= endYear; y++) {
    state.years.push(y);
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

  // Cache numeric years (used by scrubber, keyboard, positioning)
  state.numericYears = state.years.slice();

  // Add undated to year list if needed
  if (hasUndated) {
    state.years.push('undated');
  }

  // Build primary-year → DVD-groups map for the editorial flow.
  // Each DVD appears exactly once, anchored at the year of its earliest
  // video. This keeps multi-year DVDs from duplicating across every year
  // they touch, and lets the content column flow as a single chronological
  // stream skipping years with no content.
  var dvdByPrimaryYear = {};
  var dvdSeen = {};
  for (var vi = 0; vi < data.videos.length; vi++) {
    var vid = data.videos[vi];
    var primaryYear = getYearFromDate(vid.dateStart);
    if (primaryYear === null) primaryYear = 'undated';
    var dvdKey = vid.dvd;
    var groupKey = primaryYear + '|' + dvdKey;
    if (!dvdSeen[groupKey]) {
      dvdSeen[groupKey] = { dvd: dvdKey, cover: vid.cover, videos: [] };
      if (!dvdByPrimaryYear[primaryYear]) dvdByPrimaryYear[primaryYear] = [];
      dvdByPrimaryYear[primaryYear].push(dvdSeen[groupKey]);
    }
    dvdSeen[groupKey].videos.push(vid);
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

  // Set slider ARIA
  var handle = $('.scrubber-handle');
  var numericYears = state.numericYears;
  if (numericYears.length > 0) {
    handle.setAttribute('aria-valuemin', numericYears[0]);
    handle.setAttribute('aria-valuemax', numericYears[numericYears.length - 1]);
  }

  for (var i = 0; i < state.years.length; i++) {
    var year = state.years[i];
    var label = document.createElement('button');
    label.className = 'timeline-label';
    label.type = 'button';
    label.setAttribute('data-year', year);
    label.addEventListener('click', (function(y) {
      return function() {
        // Clicking an empty year snaps to the nearest year with content.
        var target = nearestContentYear(y);
        if (target !== null) setYear(target, { scroll: true });
      };
    })(year));

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

    labelsEl.appendChild(label);
  }

  initScrubberDrag();
  initChevrons();
}

// Update spine + scrubber + labels display for a given year. Does NOT scroll.
function syncYearDisplay(year) {
  if (year === state.currentYear) return;
  state.currentYear = year;

  // Spine year
  var spine = $('#spine-year');
  if (spine) {
    spine.textContent = year === 'undated' ? 'Undated' : year;
  }

  // Scrubber handle position
  positionHandle(year);

  // Active timeline label + active section highlight
  var labels = $$('.timeline-label');
  for (var i = 0; i < labels.length; i++) {
    var labelYear = labels[i].getAttribute('data-year');
    var isActive = labelYear === String(year);
    labels[i].classList.toggle('active', isActive);
    if (isActive && !state.isDragging) {
      labels[i].scrollIntoView({
        block: 'nearest',
        inline: 'center',
        behavior: prefersReducedMotion() ? 'auto' : 'smooth'
      });
    }
  }

  var sections = $$('.year-section');
  for (var s = 0; s < sections.length; s++) {
    var sy = sections[s].getAttribute('data-year');
    sections[s].classList.toggle('active', sy === String(year));
  }

  // ARIA
  var handle = $('.scrubber-handle');
  if (year !== 'undated') {
    handle.setAttribute('aria-valuenow', year);
    handle.setAttribute('aria-valuetext', year);
  }

  // Update chevron disabled state
  updateChevronState();

  // Screen reader announcement
  var count = (state.videosByYear[year] || []).length;
  var yearLabel = year === 'undated' ? 'undated' : year;
  announce('Showing ' + count + ' video' + (count !== 1 ? 's' : '') + ' from ' + yearLabel);

  // Hash (without triggering hashchange re-render)
  var newHash = year === 'undated' ? '' : '#' + year;
  if (window.location.hash !== newHash) {
    history.replaceState(null, '', newHash || window.location.pathname);
  }
}

// User-initiated year change: sync display AND scroll to the section.
function setYear(year, opts) {
  opts = opts || {};
  syncYearDisplay(year);
  if (opts.scroll) {
    scrollToYearSection(year);
  }
}

function scrollToYearSection(year) {
  var section = document.querySelector('.year-section[data-year="' + year + '"]');
  if (!section) return;
  // Mark scroll as programmatic so the IntersectionObserver doesn't
  // ping-pong the spine while we're smooth-scrolling.
  state.programmaticScrollUntil = Date.now() + 700;
  section.scrollIntoView({
    block: 'start',
    behavior: prefersReducedMotion() ? 'auto' : 'smooth'
  });
}

function positionHandle(year) {
  var handle = $('.scrubber-handle');
  var numericYears = state.numericYears;
  if (year === 'undated' || numericYears.length === 0) {
    handle.style.left = '100%';
    return;
  }
  var idx = numericYears.indexOf(year);
  if (idx === -1) { handle.style.left = '0%'; return; }
  var pct = numericYears.length === 1 ? 50 : (idx / (numericYears.length - 1)) * 100;
  handle.style.left = pct + '%';
}

function initScrubberDrag() {
  if (state.scrubberInitialized) return;
  state.scrubberInitialized = true;
  var handle = $('.scrubber-handle');
  var track = $('.scrubber-track');

  function getYearFromPointer(clientX) {
    var rect = track.getBoundingClientRect();
    var ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    var numericYears = state.numericYears;
    var idx = Math.round(ratio * (numericYears.length - 1));
    return numericYears[idx];
  }

  function onMove(clientX) {
    if (!state.isDragging) return;
    var year = getYearFromPointer(clientX);
    if (year && year !== state.currentYear) {
      handle.classList.add('dragging');
      setYear(year, { scroll: true });
    }
  }

  function onEnd() {
    state.isDragging = false;
    handle.classList.remove('dragging');
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onEnd);
    document.removeEventListener('touchmove', onTouchMove);
    document.removeEventListener('touchend', onEnd);
  }

  function onMouseMove(e) { onMove(e.clientX); }
  function onTouchMove(e) { e.preventDefault(); onMove(e.touches[0].clientX); }

  handle.addEventListener('mousedown', function(e) {
    e.preventDefault();
    state.isDragging = true;
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onEnd);
  });

  handle.addEventListener('touchstart', function(e) {
    e.preventDefault();
    state.isDragging = true;
    document.addEventListener('touchmove', onTouchMove, { passive: false });
    document.addEventListener('touchend', onEnd);
  });

  // Click on track to jump
  track.addEventListener('click', function(e) {
    var year = getYearFromPointer(e.clientX);
    if (year) setYear(year, { scroll: true });
  });
}

// === CHEVRONS (top bar + spine) ===
function initChevrons() {
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

function stepPrev() {
  var years = state.yearsWithContent;
  var idx = years.indexOf(state.currentYear);
  if (idx === -1) {
    // Current year is an "empty" year — jump to the nearest content year
    // at or before it. Fall back to the first content year.
    var nearest = nearestContentYear(state.currentYear);
    if (nearest !== null && nearest !== state.currentYear) {
      setYear(nearest, { scroll: true });
    }
    return;
  }
  if (idx > 0) {
    setYear(years[idx - 1], { scroll: true });
  }
}

function stepNext() {
  var years = state.yearsWithContent;
  var idx = years.indexOf(state.currentYear);
  if (idx === -1) {
    var nearest = nearestContentYear(state.currentYear);
    if (nearest !== null && nearest !== state.currentYear) {
      setYear(nearest, { scroll: true });
    }
    return;
  }
  if (idx < years.length - 1) {
    setYear(years[idx + 1], { scroll: true });
  }
}

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
// year with content.
function renderAllSections() {
  var container = $('#video-content');
  container.innerHTML = '';

  for (var i = 0; i < state.yearsWithContent.length; i++) {
    var year = state.yearsWithContent[i];
    var groups = state.dvdGroupsByYear[year] || [];
    if (groups.length === 0) continue;

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
    // Undated is either present in yearsWithContent or it isn't; if not,
    // fall back to the last numeric year with content.
    return content[content.length - 1];
  }
  var best = content[0];
  var bestDist = Math.abs(year - best);
  for (var i = 1; i < content.length; i++) {
    var c = content[i];
    if (c === 'undated') continue;
    var d = Math.abs(year - c);
    if (d < bestDist) { best = c; bestDist = d; }
  }
  return best;
}

// === SCROLL-SPY: which year section is currently anchored ===
function initSectionObserver() {
  if (state.sectionObserver) {
    state.sectionObserver.disconnect();
    state.sectionObserver = null;
  }
  if (!('IntersectionObserver' in window)) return;

  // An anchor line ~25% down from the sticky top bar; the last section
  // to cross it becomes the "current" year.
  var observer = new IntersectionObserver(function(entries) {
    // If we're mid-programmatic-scroll, let setYear own the state.
    if (Date.now() < state.programmaticScrollUntil) return;

    // Find the visible entry closest to the top of the observer's rootMargin line.
    var best = null;
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      if (!entry.isIntersecting) continue;
      if (!best || entry.boundingClientRect.top < best.boundingClientRect.top) {
        best = entry;
      }
    }
    if (best) {
      var year = best.target.getAttribute('data-year');
      if (year !== String(state.currentYear)) {
        var next = year === 'undated' ? 'undated' : parseInt(year, 10);
        syncYearDisplay(next);
      }
    }
  }, {
    // Top-anchor line about 120px below the sticky top bar.
    rootMargin: '-120px 0px -70% 0px',
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
  var img = document.createElement('img');
  img.className = 'dvd-cover';
  img.alt = formatDvdTitle(dvdId) + ' cover';
  img.src = coverUrl;
  img.onerror = function() { img.replaceWith(createCoverMonogram(dvdId)); };
  return img;
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
    // Delay player open slightly so grid renders first
    setTimeout(function() { openPlayer(hash); }, DEBOUNCE_MS + 50);
  } else {
    setYear(firstContentYear, { scroll: false });
  }
}

window.addEventListener('hashchange', function() {
  var hash = window.location.hash.replace('#', '');

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
  // Escape closes player
  if (e.key === 'Escape' && state.playerVideoId) {
    closePlayer();
    e.preventDefault();
    return;
  }

  // Don't interfere when player is open or typing in an input
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

// === INIT ===
$('#retry-btn').addEventListener('click', fetchManifest);
$('#player-close-btn').addEventListener('click', closePlayer);
$('#player-error-close').addEventListener('click', closePlayer);

fetchManifest();

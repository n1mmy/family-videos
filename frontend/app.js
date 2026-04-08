// === CONFIG ===
var DEBOUNCE_MS = 150;
var MANIFEST_URL = 'manifest.json';

// === STATE ===
var state = {
  manifest: null,
  years: [],
  currentYear: null,
  videosByYear: {},
  maxVideosInYear: 0,
  playerVideoId: null,
  lastFocusedCard: null,
  isDragging: false
};

// === HELPERS ===
function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function formatDuration(seconds) {
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

  // Add undated to year list if needed
  if (hasUndated) {
    state.years.push('undated');
  }

  // Show UI
  $('#skeleton').style.display = 'none';
  $('.timeline').classList.remove('content-hidden');
  $('#video-content').classList.remove('content-hidden');

  buildTimeline();
  applyHash();
}

// === TIMELINE ===
function buildTimeline() {
  var labelsEl = $('.timeline-labels');
  labelsEl.innerHTML = '';

  // Set slider ARIA
  var handle = $('.scrubber-handle');
  var numericYears = state.years.filter(function(y) { return y !== 'undated'; });
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
      return function() { setYear(y); };
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

  // Drag handling
  initScrubberDrag();
}

function setYear(year) {
  state.currentYear = year;

  // Update year display (instant)
  var display = $('.timeline-year-display');
  display.textContent = year === 'undated' ? 'Undated' : year;

  // Update handle position (instant)
  positionHandle(year);

  // Update active label
  var labels = $$('.timeline-label');
  for (var i = 0; i < labels.length; i++) {
    var labelYear = labels[i].getAttribute('data-year');
    var isActive = labelYear === String(year);
    labels[i].classList.toggle('active', isActive);
    if (isActive) {
      // Scroll active label into view for mobile
      labels[i].scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
    }
  }

  // Update ARIA
  var handle = $('.scrubber-handle');
  if (year !== 'undated') {
    handle.setAttribute('aria-valuenow', year);
    handle.setAttribute('aria-valuetext', year);
  }

  // Screen reader announcement
  var count = (state.videosByYear[year] || []).length;
  var yearLabel = year === 'undated' ? 'undated' : year;
  announce('Showing ' + count + ' video' + (count !== 1 ? 's' : '') + ' from ' + yearLabel);

  // Update hash (without triggering hashchange re-render)
  var newHash = year === 'undated' ? '' : '#' + year;
  if (window.location.hash !== newHash) {
    history.replaceState(null, '', newHash || window.location.pathname);
  }

  // Debounced grid render
  debouncedRenderGrid();
}

var debouncedRenderGrid = debounce(function() {
  renderGrid();
}, DEBOUNCE_MS);

function positionHandle(year) {
  var handle = $('.scrubber-handle');
  var numericYears = state.years.filter(function(y) { return y !== 'undated'; });
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
  var handle = $('.scrubber-handle');
  var track = $('.scrubber-track');

  function getYearFromPointer(clientX) {
    var rect = track.getBoundingClientRect();
    var ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    var numericYears = state.years.filter(function(y) { return y !== 'undated'; });
    var idx = Math.round(ratio * (numericYears.length - 1));
    return numericYears[idx];
  }

  function onMove(clientX) {
    if (!state.isDragging) return;
    var year = getYearFromPointer(clientX);
    if (year && year !== state.currentYear) {
      handle.classList.add('dragging');
      setYear(year);
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
    if (year) setYear(year);
  });
}

// === GRID ===
function renderGrid() {
  var container = $('#video-content');
  container.innerHTML = '';

  var year = state.currentYear;
  var videos = state.videosByYear[year] || [];

  if (videos.length === 0) {
    var empty = document.createElement('div');
    empty.className = 'empty-year';
    empty.textContent = year === 'undated'
      ? 'No undated videos'
      : 'No videos from ' + year;
    container.appendChild(empty);
    return;
  }

  // Utility heading
  var heading = document.createElement('h2');
  heading.className = 'year-heading';
  heading.textContent = year === 'undated'
    ? 'Undated'
    : 'Videos from ' + year;
  container.appendChild(heading);

  // Group by DVD
  var groups = groupByDvd(videos);

  for (var g = 0; g < groups.length; g++) {
    var group = groups[g];
    var groupEl = document.createElement('div');
    groupEl.className = 'dvd-group';

    // Group header
    var header = document.createElement('div');
    header.className = 'dvd-group-header';

    var coverEl = createCoverElement(group.cover, group.dvd);
    header.appendChild(coverEl);

    var titleEl = document.createElement('span');
    titleEl.className = 'dvd-group-title';
    titleEl.textContent = formatDvdTitle(group.dvd);
    header.appendChild(titleEl);

    groupEl.appendChild(header);

    // Video grid
    var grid = document.createElement('div');
    grid.className = 'video-grid';

    for (var v = 0; v < group.videos.length; v++) {
      var card = createVideoCard(group.videos[v]);
      grid.appendChild(card);
    }

    groupEl.appendChild(grid);
    container.appendChild(groupEl);
  }

  observeThumbnails();
}

function createCoverElement(coverUrl, dvdId) {
  var img = document.createElement('img');
  img.className = 'dvd-cover';
  img.alt = formatDvdTitle(dvdId) + ' cover';
  img.src = coverUrl;
  img.onerror = function() {
    // Replace with monogram
    var mono = document.createElement('div');
    mono.className = 'dvd-cover-monogram';
    mono.textContent = dvdId.charAt(0).toUpperCase();
    mono.setAttribute('aria-hidden', 'true');
    img.replaceWith(mono);
  };
  return img;
}

function formatDvdTitle(dvdId) {
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

  // Focus close button
  $('#player-close-btn').focus();

  announce('Playing ' + video.title);
}

function closePlayer() {
  if (!state.playerVideoId) return;

  var overlay = $('#player-overlay');
  var videoEl = $('#player-video');

  videoEl.pause();
  videoEl.removeAttribute('src');
  videoEl.load();

  overlay.classList.remove('visible');
  state.playerVideoId = null;

  // Restore hash to current year
  var yearHash = state.currentYear && state.currentYear !== 'undated'
    ? '#' + state.currentYear : '';
  history.pushState(null, '', yearHash || window.location.pathname);

  // Return focus
  if (state.lastFocusedCard && state.lastFocusedCard.isConnected) {
    state.lastFocusedCard.focus();
  }
  state.lastFocusedCard = null;

  announce('Video closed');
}

// === THUMBNAILS ===
function observeThumbnails() {
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

  if (!hash) {
    setYear(state.years[0]);
    return;
  }

  // 4-digit year check
  if (/^\d{4}$/.test(hash)) {
    var year = parseInt(hash, 10);
    if (state.videosByYear[year]) {
      setYear(year);
    } else {
      setYear(state.years[0]);
    }
    return;
  }

  // Video ID lookup
  var video = findVideoById(hash);
  if (video) {
    var videoYear = getYearFromDate(video.dateStart) || 'undated';
    setYear(videoYear);
    // Delay player open slightly so grid renders first
    setTimeout(function() { openPlayer(hash); }, DEBOUNCE_MS + 50);
  } else {
    setYear(state.years[0]);
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

  var numericYears = state.years.filter(function(y) { return y !== 'undated'; });

  if (e.key === 'ArrowLeft') {
    e.preventDefault();
    var idx = numericYears.indexOf(state.currentYear);
    if (idx > 0) {
      setYear(numericYears[idx - 1]);
    } else if (state.currentYear === 'undated' && numericYears.length > 0) {
      setYear(numericYears[numericYears.length - 1]);
    }
    return;
  }

  if (e.key === 'ArrowRight') {
    e.preventDefault();
    var idx2 = numericYears.indexOf(state.currentYear);
    if (idx2 >= 0 && idx2 < numericYears.length - 1) {
      setYear(numericYears[idx2 + 1]);
    }
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

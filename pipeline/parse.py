"""Filename parsing, title generation, override merging, and utilities."""

import calendar
import datetime
import hashlib
import re


# Year range that the timeline can reasonably display. Matches the
# FLOOR/CEIL used by frontend/app.js deriveYearRangeFromVideos — a
# parser that accepted year 0 or 9999 would silently place videos
# thousands of years away on the timeline, or worse, emit an invalid
# YYYY-MM-DD string ("0-01-01") that breaks downstream consumers.
_YEAR_FLOOR = 1900
_YEAR_CEIL = datetime.date.today().year + 5


def _valid_date(y, m, d):
    """True iff (y, m, d) is a real calendar date within our year range.

    Uses datetime.date so Feb 30, Jun 31, leap-year edge cases, etc.
    are rejected — the old ``1 <= d <= 31`` guard happily accepted
    them and produced strings like ``1999-06-31`` that crashed the
    frontend at render time.
    """
    if not (_YEAR_FLOOR <= y <= _YEAR_CEIL):
        return False
    try:
        datetime.date(y, m, d)
    except ValueError:
        return False
    return True


def _valid_year_month(y, m):
    """True iff (y, m) is a valid year/month within our year range."""
    return _YEAR_FLOOR <= y <= _YEAR_CEIL and 1 <= m <= 12


def _label_from_suffix(suffix):
    """Turn an optional ``-label`` suffix into a space-separated title.

    Returns None unless the suffix contains at least one segment
    whose first character is a letter. Splitting on ``-`` and
    filtering empty parts tolerates ``--double``, ``-trailing-``,
    and similar typos without producing leading/double spaces.

    The first-segment-must-start-with-a-letter rule is defensive:
    ``20010101-20020102x`` would otherwise be parsed as a single day
    ``2001-01-01`` with title ``20020102x``, silently dropping the
    2002 end date the user meant. Legitimate labels start with a
    letter (``our-wedding``, ``hawaii-pt2``, ``alaskan-cruise-pt2``);
    numeric-leading ``labels`` are almost always mangled date tokens.
    """
    if not suffix:
        return None
    parts = [p for p in suffix.split("-") if p]
    if not parts:
        return None
    if not parts[0][0].isalpha():
        return None
    return " ".join(parts)


def parse_dirname(name):
    """Parse a DVD directory name into date range, title, and years.

    Tries patterns in priority order. All date patterns allow an
    optional trailing ``-label`` (e.g. ``20020702-20021225-alaskan-cruise-pt2``
    or ``200107-hawaii-pt2``), and single-date variants handle DVDs
    that cover one day or one month without a range.

    1. YYYYMMDD[-YYYYMMDD][-label]  (day range or single day)
    2. YYYYMM[-YYYYMM][-label]      (month range or single month)
    3. YYYY-label                   (year + descriptive label)
    4. label-YY-YY-...              (text prefix + 2-digit year suffixes)
    5. Fallback (unparseable)

    Returns dict with keys: dateStart, dateEnd, title, years.
    """
    # Pattern 1: YYYYMMDD with optional -YYYYMMDD end and optional -label
    m = re.fullmatch(r"(\d{8})(?:-(\d{8}))?(?:-(.+))?", name)
    if m:
        s, e, label = m.group(1), m.group(2), m.group(3)
        sy, sm, sd = int(s[:4]), int(s[4:6]), int(s[6:8])
        if _valid_date(sy, sm, sd):
            ds = f"{sy:04d}-{sm:02d}-{sd:02d}"
            if e is not None:
                ey, em, ed = int(e[:4]), int(e[4:6]), int(e[6:8])
                if _valid_date(ey, em, ed) and (ey, em, ed) >= (sy, sm, sd):
                    de = f"{ey:04d}-{em:02d}-{ed:02d}"
                    title = _label_from_suffix(label)
                    # Don't swallow garbage digits as a label (e.g. the
                    # 7-digit typo case or a mangled end-date like
                    # "20020102x").
                    if label and title is None:
                        pass  # fall through to other patterns
                    else:
                        years = list(range(sy, ey + 1))
                        return {
                            "dateStart": ds,
                            "dateEnd": de,
                            "title": title,
                            "years": years,
                        }
            else:
                title = _label_from_suffix(label)
                if label and title is None:
                    pass  # fall through
                else:
                    return {
                        "dateStart": ds,
                        "dateEnd": None,
                        "title": title,
                        "years": [sy],
                    }

    # Pattern 2: YYYYMM with optional -YYYYMM end and optional -label
    m = re.fullmatch(r"(\d{6})(?:-(\d{6}))?(?:-(.+))?", name)
    if m:
        s, e, label = m.group(1), m.group(2), m.group(3)
        sy, sm = int(s[:4]), int(s[4:6])
        if _valid_year_month(sy, sm):
            ds = f"{sy:04d}-{sm:02d}"
            if e is not None:
                ey, em = int(e[:4]), int(e[4:6])
                if _valid_year_month(ey, em) and (ey, em) >= (sy, sm):
                    de = f"{ey:04d}-{em:02d}"
                    title = _label_from_suffix(label)
                    if label and title is None:
                        pass
                    else:
                        years = list(range(sy, ey + 1))
                        return {
                            "dateStart": ds,
                            "dateEnd": de,
                            "title": title,
                            "years": years,
                        }
            else:
                title = _label_from_suffix(label)
                if label and title is None:
                    pass
                else:
                    return {
                        "dateStart": ds,
                        "dateEnd": None,
                        "title": title,
                        "years": [sy],
                    }

    # Pattern 3: YYYY-label (4-digit year followed by text)
    m = re.fullmatch(r"(\d{4})-(.+)", name)
    if m:
        year_str, label = m.group(1), m.group(2)
        # Only match if label contains at least one non-digit character
        # (avoids grabbing "1997-04-05-06" as year+label)
        if re.search(r"[a-zA-Z]", label):
            year = int(year_str)
            if _YEAR_FLOOR <= year <= _YEAR_CEIL:
                title = label.replace("-", " ")
                return {
                    "dateStart": year_str,
                    "dateEnd": None,
                    "title": title,
                    "years": [year],
                }

    # Pattern 4: label-YY-YY-... (text prefix + trailing 2-digit years)
    parts = name.split("-")
    if len(parts) >= 2:
        # Find where trailing 2-digit year segments begin
        trail_start = len(parts)
        for i in range(len(parts) - 1, -1, -1):
            if re.fullmatch(r"\d{2}", parts[i]):
                trail_start = i
            else:
                break
        if trail_start < len(parts) and trail_start > 0:
            label_parts = parts[:trail_start]
            year_parts = parts[trail_start:]
            # Must have at least one non-digit label segment
            if any(re.search(r"[a-zA-Z]", p) for p in label_parts):
                title = " ".join(label_parts)
                years = []
                for yy in year_parts:
                    n = int(yy)
                    years.append(1900 + n if n >= 70 else 2000 + n)
                years.sort()
                ds = str(min(years))
                de = str(max(years))
                return {
                    "dateStart": ds,
                    "dateEnd": de,
                    "title": title,
                    "years": years,
                }

    # Pattern 5: Fallback
    return {"dateStart": None, "dateEnd": None, "title": name, "years": None}


def generate_title(parsed):
    """Generate a human-readable title from parsed directory metadata.

    Uses en-dash (\u2013) between date ranges and calendar.month_abbr for month names.
    """
    ds = parsed["dateStart"]
    de = parsed["dateEnd"]

    # If a title was already extracted (label-based patterns), use it
    if parsed["title"] is not None:
        return parsed["title"]

    # No title extracted — generate from dates
    if ds is None:
        return "Unknown"

    start_str = _format_date(ds)
    if de is not None:
        end_str = _format_date(de)
        return f"{start_str} \u2013 {end_str}"
    return start_str


def _format_date(d):
    """Format a date string (YYYY, YYYY-MM, or YYYY-MM-DD) for display."""
    parts = d.split("-")
    if len(parts) == 3:
        y, m, day = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{calendar.month_abbr[m]} {day}, {y}"
    if len(parts) == 2:
        y, m = int(parts[0]), int(parts[1])
        return f"{calendar.month_abbr[m]} {y}"
    return parts[0]


def merge_overrides(parsed, overrides, dvd_name, title_name):
    """Merge override data into parsed metadata.

    Applies DVD-level overrides first, then per-title overrides on top.
    Override fields: title, dateStart, dateEnd, skip.
    """
    result = dict(parsed)
    result.setdefault("skip", False)

    # DVD-level overrides
    dvd_ovr = overrides.get(dvd_name, {})
    for key in ("title", "dateStart", "dateEnd", "skip"):
        if key in dvd_ovr:
            result[key] = dvd_ovr[key]

    # Per-title overrides (take precedence)
    title_key = f"{dvd_name}/{title_name}"
    title_ovr = overrides.get(title_key, {})
    for key in ("title", "dateStart", "dateEnd", "skip"):
        if key in title_ovr:
            result[key] = title_ovr[key]

    return result


def make_video_id(dvd_name, title_stem):
    """Generate a video ID from DVD directory name and title stem.

    Example: make_video_id("197902-198201", "title00") -> "197902-198201-title00"
    """
    return f"{dvd_name}-{title_stem}"


def compute_file_hash(filepath):
    """Compute a short cache-busting hash from file content.

    Reads the first 4KB + file size, returns first 8 hex chars of SHA-256.
    """
    import os

    size = os.path.getsize(filepath)
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        h.update(f.read(4096))
    h.update(str(size).encode())
    return h.hexdigest()[:8]

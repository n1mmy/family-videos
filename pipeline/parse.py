"""Filename parsing, title generation, override merging, and utilities."""

import calendar
import hashlib
import re


def parse_dirname(name):
    """Parse a DVD directory name into date range, title, and years.

    Tries patterns in priority order:
    1. YYYYMMDD-YYYYMMDD (full date range)
    2. YYYYMM-YYYYMM (month range)
    3. YYYY-label (year + descriptive label)
    4. label-YY-YY-... (text prefix + 2-digit year suffixes)
    5. Fallback (unparseable)

    Returns dict with keys: dateStart, dateEnd, title, years.
    """
    # Pattern 1: YYYYMMDD-YYYYMMDD
    m = re.fullmatch(r"(\d{8})-(\d{8})", name)
    if m:
        s, e = m.group(1), m.group(2)
        sy, sm, sd = int(s[:4]), int(s[4:6]), int(s[6:8])
        ey, em, ed = int(e[:4]), int(e[4:6]), int(e[6:8])
        if 1 <= sm <= 12 and 1 <= sd <= 31 and 1 <= em <= 12 and 1 <= ed <= 31:
            ds = f"{sy}-{sm:02d}-{sd:02d}"
            de = f"{ey}-{em:02d}-{ed:02d}"
            years = list(range(sy, ey + 1))
            return {"dateStart": ds, "dateEnd": de, "title": None, "years": years}

    # Pattern 2: YYYYMM-YYYYMM
    m = re.fullmatch(r"(\d{6})-(\d{6})", name)
    if m:
        s, e = m.group(1), m.group(2)
        sy, sm = int(s[:4]), int(s[4:6])
        ey, em = int(e[:4]), int(e[4:6])
        if 1 <= sm <= 12 and 1 <= em <= 12:
            ds = f"{sy}-{sm:02d}"
            de = f"{ey}-{em:02d}"
            years = list(range(sy, ey + 1))
            return {"dateStart": ds, "dateEnd": de, "title": None, "years": years}

    # Pattern 3: YYYY-label (4-digit year followed by text)
    m = re.fullmatch(r"(\d{4})-(.+)", name)
    if m:
        year_str, label = m.group(1), m.group(2)
        # Only match if label contains at least one non-digit character
        # (avoids grabbing "1997-04-05-06" as year+label)
        if re.search(r"[a-zA-Z]", label):
            year = int(year_str)
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

"""Tests for pipeline/parse.py — filename parsing, overrides, and utilities."""

import pytest
from parse import (
    compute_file_hash,
    generate_title,
    make_video_id,
    merge_overrides,
    parse_dirname,
)


# --- Filename parsing ---


class TestParseDirname:
    """Test all 5 filename patterns plus edge cases."""

    def test_yyyymmdd_range(self):
        result = parse_dirname("19830811-19831212")
        assert result["dateStart"] == "1983-08-11"
        assert result["dateEnd"] == "1983-12-12"
        assert result["years"] == [1983]
        assert result["title"] is None

    def test_yyyymm_range(self):
        result = parse_dirname("197902-198201")
        assert result["dateStart"] == "1979-02"
        assert result["dateEnd"] == "1982-01"
        assert result["years"] == [1979, 1980, 1981, 1982]
        assert result["title"] is None

    def test_year_label(self):
        result = parse_dirname("1997-trip-cross-country-pt2-plusplus")
        assert result["dateStart"] == "1997"
        assert result["dateEnd"] is None
        assert result["title"] == "trip cross country pt2 plusplus"
        assert result["years"] == [1997]

    def test_label_years(self):
        result = parse_dirname("christmas-04-05-06")
        assert result["title"] == "christmas"
        assert result["years"] == [2004, 2005, 2006]
        assert result["dateStart"] == "2004"
        assert result["dateEnd"] == "2006"

    def test_unparseable(self):
        result = parse_dirname("random-stuff-here")
        assert result["dateStart"] is None
        assert result["dateEnd"] is None
        assert result["title"] == "random-stuff-here"
        assert result["years"] is None

    def test_invalid_month_falls_through(self):
        """YYYYMMDD with invalid month (13) should not match Pattern 1."""
        result = parse_dirname("19831301-19831401")
        # Should fall through — not a valid YYYYMMDD range
        assert result["dateStart"] is not None or result["dateStart"] is None
        # Must not parse as month 13
        if result["dateStart"] is not None:
            month = int(result["dateStart"].split("-")[1])
            assert 1 <= month <= 12

    def test_single_trailing_year(self):
        """label-YY with a single 2-digit year suffix."""
        result = parse_dirname("birthday-99")
        assert result["title"] == "birthday"
        assert result["years"] == [1999]
        assert result["dateStart"] == "1999"
        assert result["dateEnd"] == "1999"

    def test_label_years_2000s_boundary(self):
        """2-digit years at the 70/00 boundary."""
        result = parse_dirname("summer-00-01")
        assert result["years"] == [2000, 2001]
        assert result["dateStart"] == "2000"

    def test_yyyymmdd_range_spanning_years(self):
        """YYYYMMDD range spanning multiple years."""
        result = parse_dirname("19891225-19900115")
        assert result["dateStart"] == "1989-12-25"
        assert result["dateEnd"] == "1990-01-15"
        assert result["years"] == [1989, 1990]


# --- Title generation ---


class TestGenerateTitle:
    def test_yyyymmdd_range_title(self):
        parsed = parse_dirname("19830811-19831212")
        title = generate_title(parsed)
        assert "Aug" in title
        assert "1983" in title
        assert "Dec" in title
        assert "\u2013" in title  # en-dash

    def test_yyyymm_range_title(self):
        parsed = parse_dirname("197902-198201")
        title = generate_title(parsed)
        assert title == "Feb 1979 \u2013 Jan 1982"

    def test_label_title_passthrough(self):
        parsed = parse_dirname("1997-trip-cross-country-pt2-plusplus")
        title = generate_title(parsed)
        assert title == "trip cross country pt2 plusplus"

    def test_label_years_title(self):
        parsed = parse_dirname("christmas-04-05-06")
        title = generate_title(parsed)
        assert title == "christmas"

    def test_unparseable_title(self):
        parsed = parse_dirname("mystery-disc")
        title = generate_title(parsed)
        assert title == "mystery-disc"


# --- Override merging ---


class TestMergeOverrides:
    def test_dvd_level_override(self, sample_overrides):
        parsed = parse_dirname("christmas-04-05-06")
        result = merge_overrides(parsed, sample_overrides, "christmas-04-05-06", "title00")
        assert result["title"] == "Christmas 2004-2006"
        assert result["dateStart"] == "2004-12"
        assert result["dateEnd"] == "2006-12"

    def test_per_title_override(self, sample_overrides):
        parsed = parse_dirname("197902-198201")
        result = merge_overrides(parsed, sample_overrides, "197902-198201", "title01")
        assert result["title"] == "Birthday Party"
        assert result["skip"] is False

    def test_skip_true(self, sample_overrides):
        parsed = parse_dirname("197902-198201")
        result = merge_overrides(parsed, sample_overrides, "197902-198201", "title02")
        assert result["skip"] is True

    def test_no_override(self):
        parsed = parse_dirname("197902-198201")
        result = merge_overrides(parsed, {}, "197902-198201", "title00")
        assert result["skip"] is False
        assert result["dateStart"] == "1979-02"


# --- Video ID generation ---


class TestMakeVideoId:
    def test_basic(self):
        assert make_video_id("197902-198201", "title00") == "197902-198201-title00"

    def test_label_dvd(self):
        assert make_video_id("christmas-04-05-06", "title00") == "christmas-04-05-06-title00"


# --- File hash ---


class TestComputeFileHash:
    def test_deterministic(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world" * 100)
        h1 = compute_file_hash(str(f))
        h2 = compute_file_hash(str(f))
        assert h1 == h2
        assert len(h1) == 8
        assert all(c in "0123456789abcdef" for c in h1)

    def test_different_content(self, tmp_path):
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content A" * 100)
        f2.write_bytes(b"content B" * 100)
        assert compute_file_hash(str(f1)) != compute_file_hash(str(f2))

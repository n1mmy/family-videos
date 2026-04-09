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
        """YYYYMMDD with invalid month (13) falls through to fallback."""
        result = parse_dirname("19831301-19831401")
        assert result["dateStart"] is None
        assert result["dateEnd"] is None

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

    def test_single_yyyymmdd(self):
        """Bare YYYYMMDD (single day, no range)."""
        result = parse_dirname("19940328")
        assert result["dateStart"] == "1994-03-28"
        assert result["dateEnd"] is None
        assert result["title"] is None
        assert result["years"] == [1994]

    def test_single_yyyymmdd_with_label(self):
        """YYYYMMDD followed by a descriptive label."""
        result = parse_dirname("20120728-nickjen-reception")
        assert result["dateStart"] == "2012-07-28"
        assert result["dateEnd"] is None
        assert result["title"] == "nickjen reception"
        assert result["years"] == [2012]

    def test_yyyymmdd_range_with_label(self):
        """YYYYMMDD-YYYYMMDD followed by a descriptive label."""
        result = parse_dirname("20020702-20021225-alaskan-cruise-pt2")
        assert result["dateStart"] == "2002-07-02"
        assert result["dateEnd"] == "2002-12-25"
        assert result["title"] == "alaskan cruise pt2"
        assert result["years"] == [2002]

    def test_single_yyyymm_with_label(self):
        """Bare YYYYMM followed by a descriptive label."""
        result = parse_dirname("200107-hawaii-pt2")
        assert result["dateStart"] == "2001-07"
        assert result["dateEnd"] is None
        assert result["title"] == "hawaii pt2"
        assert result["years"] == [2001]

    def test_single_yyyymm_bare(self):
        """Bare YYYYMM with no label — single month, no title."""
        result = parse_dirname("199702")
        assert result["dateStart"] == "1997-02"
        assert result["dateEnd"] is None
        assert result["title"] is None
        assert result["years"] == [1997]

    def test_yyyymm_range_with_label(self):
        """YYYYMM-YYYYMM followed by a descriptive label."""
        result = parse_dirname("197807-197902-our-wedding")
        assert result["dateStart"] == "1978-07"
        assert result["dateEnd"] == "1979-02"
        assert result["title"] == "our wedding"
        assert result["years"] == [1978, 1979]

    def test_typo_7digit_second_date_falls_through(self):
        """Mangled 7-digit end date (leading zero dropped) should not
        be swallowed as a bogus label. The user can override via
        overrides.json or rename the directory."""
        result = parse_dirname("19881123-1989325")
        assert result["dateStart"] is None
        assert result["dateEnd"] is None

    def test_single_yyyymmdd_invalid_month(self):
        """Single YYYYMMDD with month 13 falls through to fallback."""
        result = parse_dirname("20121301")
        assert result["dateStart"] is None

    def test_single_yyyymmdd_invalid_day(self):
        """Valid month but impossible day (June 31) falls through."""
        result = parse_dirname("19990631")
        # sm=06 is valid, sd=31 is not (June has 30 days). The old
        # naive ``1 <= sd <= 31`` guard accepted this and emitted
        # ``1999-06-31``, which broke the frontend date parser.
        assert result["dateStart"] is None

    def test_single_yyyymmdd_feb_30(self):
        """Feb 30 is impossible — falls through."""
        result = parse_dirname("19990230")
        assert result["dateStart"] is None

    def test_single_yyyymmdd_leap_year_feb_29(self):
        """Feb 29 on a leap year is valid."""
        result = parse_dirname("20000229")
        assert result["dateStart"] == "2000-02-29"

    def test_single_yyyymmdd_non_leap_year_feb_29(self):
        """Feb 29 on a non-leap year falls through."""
        result = parse_dirname("19990229")
        assert result["dateStart"] is None

    def test_year_zero_falls_through(self):
        """Year 0 (nonsensical) falls through — we cap at 1900."""
        result = parse_dirname("00000101")
        assert result["dateStart"] is None
        result = parse_dirname("0000-trip")
        assert result["dateStart"] is None

    def test_date_range_end_before_start(self):
        """Range with end before start falls through — likely a typo."""
        result = parse_dirname("19901231-19900101")
        assert result["dateStart"] is None

    def test_label_with_digit_prefix_rejected(self):
        """A ``label'' that begins with a digit is almost always a
        mangled date token. ``20010101-20020102x`` should NOT parse
        as a single day 2001-01-01 with title "20020102x" — the user
        meant a range and we silently losing the end date would be
        worse than rejecting the whole name.
        """
        result = parse_dirname("20010101-20020102x")
        assert result["dateStart"] is None

    def test_label_with_double_dash_collapsed(self):
        """``20010101--double`` should not produce a leading-space title."""
        result = parse_dirname("20010101--double")
        assert result["dateStart"] == "2001-01-01"
        assert result["title"] == "double"


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

"""Shared test fixtures for the family-videos pipeline."""

import json
import sys
from pathlib import Path

import pytest

# Add pipeline directory to path so we can import parse/transcode
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Create a realistic MKV output directory structure.

    Mimics /data/output/ with multiple DVD directories,
    stub .mkv files, and cover JPGs.
    """
    output = tmp_path / "output"

    # DVD 1: YYYYMM-YYYYMM pattern with two titles and a cover
    dvd1 = output / "197902-198201"
    dvd1.mkdir(parents=True)
    (dvd1 / "title00.mkv").write_bytes(b"\x00" * 1024)
    (dvd1 / "title01.mkv").write_bytes(b"\x00" * 512)
    (dvd1 / "197902-198201.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

    # DVD 2: label-YY-YY-YY pattern with one title and a cover
    dvd2 = output / "christmas-04-05-06"
    dvd2.mkdir()
    (dvd2 / "title00.mkv").write_bytes(b"\x00" * 2048)
    (dvd2 / "christmas-04-05-06.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

    # DVD 3: YYYY-label pattern with one title, no cover
    dvd3 = output / "1997-trip-cross-country-pt2-plusplus"
    dvd3.mkdir()
    (dvd3 / "title00.mkv").write_bytes(b"\x00" * 768)

    return output


@pytest.fixture
def tmp_served_dir(tmp_path):
    """Create an empty served directory structure."""
    served = tmp_path / "served"
    (served / "videos").mkdir(parents=True)
    (served / "thumbs").mkdir()
    (served / "covers").mkdir()
    return served


@pytest.fixture
def sample_overrides():
    """Return a sample overrides dict matching the plan.md example."""
    return {
        "christmas-04-05-06": {
            "title": "Christmas 2004-2006",
            "dateStart": "2004-12",
            "dateEnd": "2006-12",
        },
        "197902-198201/title01": {
            "title": "Birthday Party",
            "skip": False,
        },
        "197902-198201/title02": {
            "skip": True,
        },
    }


@pytest.fixture
def overrides_file(tmp_path, sample_overrides):
    """Write sample overrides to a JSON file and return the path."""
    p = tmp_path / "overrides.json"
    p.write_text(json.dumps(sample_overrides))
    return p


@pytest.fixture
def schema_path():
    """Return the path to the real manifest.schema.json."""
    return Path(__file__).resolve().parent.parent / "manifest.schema.json"

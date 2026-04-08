"""Tests for pipeline/transcode.py — pipeline logic with mocked ffmpeg/ffprobe."""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from transcode import (
    build_manifest,
    check_disk_space,
    copy_cover,
    extract_smart_thumbnail,
    get_duration,
    process_one_title,
    run_pipeline,
    should_skip,
    swap_symlink,
    transcode_one,
    validate_manifest,
    write_manifest_atomic,
)


# --- Idempotency ---


class TestIdempotency:
    def test_skip_existing_newer_mp4(self, tmp_path):
        mkv = tmp_path / "source.mkv"
        mp4 = tmp_path / "output.mp4"
        mkv.write_bytes(b"\x00" * 100)
        time.sleep(0.05)
        mp4.write_bytes(b"\x00" * 50)
        assert should_skip(mkv, mp4) is True

    def test_retranscode_when_source_newer(self, tmp_path):
        mkv = tmp_path / "source.mkv"
        mp4 = tmp_path / "output.mp4"
        mp4.write_bytes(b"\x00" * 50)
        time.sleep(0.05)
        mkv.write_bytes(b"\x00" * 100)
        assert should_skip(mkv, mp4) is False

    def test_no_output_yet(self, tmp_path):
        mkv = tmp_path / "source.mkv"
        mp4 = tmp_path / "output.mp4"
        mkv.write_bytes(b"\x00" * 100)
        assert should_skip(mkv, mp4) is False


# --- ffmpeg ---


class TestTranscodeOne:
    def test_ffmpeg_failure_returns_false(self, tmp_path):
        mkv = tmp_path / "source.mkv"
        mp4 = tmp_path / "output.mp4"
        mkv.write_bytes(b"\x00" * 100)

        with patch("transcode.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error message")
            assert transcode_one(mkv, mp4) is False

    def test_ffmpeg_command_args(self, tmp_path):
        mkv = tmp_path / "source.mkv"
        mp4 = tmp_path / "videos" / "output.mp4"
        mkv.write_bytes(b"\x00" * 100)

        with patch("transcode.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            transcode_one(mkv, mp4)

            args = mock_run.call_args[0][0]
            assert args[0] == "ffmpeg"
            assert "-crf" in args
            assert args[args.index("-crf") + 1] == "23"
            assert "-vf" in args
            vf = args[args.index("-vf") + 1]
            assert "yadif" in vf
            assert "scale=-2:480" in vf
            assert "setsar=1:1" in vf
            assert "+faststart" in " ".join(args)
            assert "-c:a" in args
            assert "aac" in args


# --- Smart thumbnail ---


class TestSmartThumbnail:
    def test_picks_highest_variance(self, tmp_path):
        mkv = tmp_path / "video.mkv"
        thumb = tmp_path / "thumb.jpg"
        mkv.write_bytes(b"\x00" * 100)

        call_count = [0]

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            result = MagicMock()
            if "rawvideo" in cmd:
                # First 5 calls: return frames with different variance
                call_count[0] += 1
                if call_count[0] == 3:
                    # High variance frame (varied bytes)
                    result.stdout = bytes(range(256)) * 4
                else:
                    # Low variance frame (uniform bytes)
                    result.stdout = bytes([128]) * 1024
                result.returncode = 0
            else:
                # Final JPEG extraction
                result.returncode = 0
                thumb.write_bytes(b"\xff\xd8\xff")
            return result

        with patch("transcode.subprocess.run", side_effect=mock_run):
            ok = extract_smart_thumbnail(mkv, thumb, 100.0)
            assert ok is True

    def test_zero_duration_skips(self, tmp_path):
        mkv = tmp_path / "video.mkv"
        thumb = tmp_path / "thumb.jpg"
        mkv.write_bytes(b"\x00" * 100)
        assert extract_smart_thumbnail(mkv, thumb, 0.0) is False


# --- Duration / filtering ---


class TestDuration:
    def test_get_duration_success(self):
        with patch("transcode.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"format": {"duration": "3600.5"}}),
            )
            assert get_duration(Path("/fake/video.mkv")) == 3600.5

    def test_get_duration_failure(self):
        with patch("transcode.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert get_duration(Path("/fake/video.mkv")) == 0.0

    def test_duration_filter_skips_short(self, tmp_output_dir, tmp_path, schema_path):
        """Short titles (<60s) should be skipped."""
        # Mock ffprobe to return 30s for all files
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == "ffprobe":
                result.returncode = 0
                result.stdout = json.dumps({"format": {"duration": "30.0"}})
            else:
                result.returncode = 0
            return result

        served = tmp_path / "served"
        with patch("transcode.subprocess.run", side_effect=mock_run):
            ret = run_pipeline(
                str(tmp_output_dir), str(served),
                None, str(schema_path),
                dry_run=True, min_duration=60, workers=1,
            )
        assert ret == 0

    def test_custom_min_duration(self, tmp_output_dir, tmp_path, schema_path):
        """With --min-duration=10, a 30s video should NOT be skipped."""
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == "ffprobe":
                result.returncode = 0
                result.stdout = json.dumps({"format": {"duration": "30.0"}})
            else:
                result.returncode = 0
            return result

        served = tmp_path / "served"
        with patch("transcode.subprocess.run", side_effect=mock_run):
            ret = run_pipeline(
                str(tmp_output_dir), str(served),
                None, str(schema_path),
                dry_run=True, min_duration=10, workers=1,
            )
        assert ret == 0


# --- Skip override ---


class TestSkipOverride:
    def test_skip_override_excludes_title(self, tmp_path, schema_path):
        """A title with skip=true in overrides should be excluded."""
        # Set up a minimal DVD directory
        output = tmp_path / "output"
        dvd = output / "test-dvd"
        dvd.mkdir(parents=True)
        (dvd / "title00.mkv").write_bytes(b"\x00" * 100)
        (dvd / "title01.mkv").write_bytes(b"\x00" * 100)

        overrides = {"test-dvd/title01": {"skip": True}}
        ovr_path = tmp_path / "overrides.json"
        ovr_path.write_text(json.dumps(overrides))

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == "ffprobe":
                result.returncode = 0
                result.stdout = json.dumps({"format": {"duration": "120.0"}})
            else:
                result.returncode = 0
            return result

        served = tmp_path / "served"
        with patch("transcode.subprocess.run", side_effect=mock_run):
            ret = run_pipeline(
                str(output), str(served),
                str(ovr_path), str(schema_path),
                dry_run=True, min_duration=60, workers=1,
            )
        assert ret == 0


# --- Atomic manifest ---


class TestManifest:
    def test_atomic_write(self, tmp_path):
        manifest = {"title": "Test", "dateRange": {"start": "2000", "end": "2005"}, "videos": []}
        target = tmp_path / "manifest.json"
        write_manifest_atomic(manifest, target)
        assert target.exists()
        loaded = json.loads(target.read_text())
        assert loaded["title"] == "Test"
        # No temp files should remain
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) == 1

    def test_validates_against_schema(self, schema_path):
        manifest = {
            "title": "Family Videos",
            "dateRange": {"start": "1979", "end": "2006"},
            "videos": [
                {
                    "id": "197902-198201-title00",
                    "title": "Feb 1979 – Jan 1982",
                    "dateStart": "1979-02",
                    "dateEnd": "1982-01",
                    "duration": 6135,
                    "file": "videos/197902-198201-title00.mp4?v=a1b2c3",
                    "thumbnail": "thumbs/197902-198201-title00.jpg?v=d4e5f6",
                    "cover": "covers/197902-198201.jpg",
                    "dvd": "197902-198201",
                    "sourceFile": "197902-198201/title00.mkv",
                }
            ],
        }
        validate_manifest(manifest, schema_path)

    def test_invalid_manifest_fails(self, schema_path):
        manifest = {
            "title": "Family Videos",
            "dateRange": {"start": "1979", "end": "2006"},
            "videos": [
                {
                    "id": "test",
                    # Missing required fields
                }
            ],
        }
        import jsonschema as js
        with pytest.raises(js.ValidationError):
            validate_manifest(manifest, schema_path)

    def test_nullable_dates_valid(self, schema_path):
        """Videos with null dateStart/dateEnd should validate."""
        manifest = {
            "title": "Family Videos",
            "dateRange": {"start": None, "end": None},
            "videos": [
                {
                    "id": "unknown-title00",
                    "title": "Unknown Disc",
                    "dateStart": None,
                    "dateEnd": None,
                    "duration": 3600,
                    "file": "videos/unknown-title00.mp4?v=abc123",
                    "thumbnail": "thumbs/unknown-title00.jpg?v=def456",
                    "cover": "",
                    "dvd": "unknown",
                    "sourceFile": "unknown/title00.mkv",
                }
            ],
        }
        validate_manifest(manifest, schema_path)


# --- Dry run ---


class TestDryRun:
    def test_dry_run_no_ffmpeg(self, tmp_output_dir, tmp_path, schema_path):
        """Dry run should never call ffmpeg (only ffprobe for duration)."""
        ffmpeg_called = []

        def mock_run(cmd, **kwargs):
            if cmd[0] == "ffmpeg":
                ffmpeg_called.append(cmd)
            result = MagicMock()
            if cmd[0] == "ffprobe":
                result.returncode = 0
                result.stdout = json.dumps({"format": {"duration": "120.0"}})
            else:
                result.returncode = 0
            return result

        served = tmp_path / "served"
        with patch("transcode.subprocess.run", side_effect=mock_run):
            ret = run_pipeline(
                str(tmp_output_dir), str(served),
                None, str(schema_path),
                dry_run=True, min_duration=60, workers=1,
            )
        assert ret == 0
        assert len(ffmpeg_called) == 0


# --- Symlink swap ---


class TestSymlinkSwap:
    def test_first_run_creates_symlink(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        served = tmp_path / "served"
        swap_symlink(staging, served)
        assert served.is_symlink()
        assert served.resolve() == staging.resolve()

    def test_swap_replaces_existing(self, tmp_path):
        old_staging = tmp_path / "old"
        old_staging.mkdir()
        new_staging = tmp_path / "new"
        new_staging.mkdir()
        served = tmp_path / "served"
        os.symlink(str(old_staging), str(served))
        swap_symlink(new_staging, served)
        assert served.is_symlink()
        assert served.resolve() == new_staging.resolve()


# --- Cover copy ---


class TestCopyCover:
    def test_copies_existing_cover(self, tmp_path):
        dvd_dir = tmp_path / "my-dvd"
        dvd_dir.mkdir()
        (dvd_dir / "my-dvd.jpg").write_bytes(b"\xff\xd8\xff")
        covers_dir = tmp_path / "covers"
        covers_dir.mkdir()

        result = copy_cover(dvd_dir, covers_dir, "my-dvd")
        assert result == "covers/my-dvd.jpg"
        assert (covers_dir / "my-dvd.jpg").exists()

    def test_returns_empty_when_no_cover(self, tmp_path):
        dvd_dir = tmp_path / "my-dvd"
        dvd_dir.mkdir()
        covers_dir = tmp_path / "covers"
        covers_dir.mkdir()

        result = copy_cover(dvd_dir, covers_dir, "my-dvd")
        assert result == ""

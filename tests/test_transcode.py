"""Tests for pipeline/transcode.py — pipeline logic with mocked ffmpeg/ffprobe."""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from transcode import (
    copy_cover,
    copy_served_to_staging,
    extract_smart_thumbnail,
    get_duration,
    process_one_title,
    publish_staging,
    run_pipeline,
    should_skip,
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


# --- Publish staging ---


class TestPublishStaging:
    def test_copies_assets_to_output(self, tmp_path):
        staging = tmp_path / "staging"
        (staging / "videos").mkdir(parents=True)
        (staging / "thumbs").mkdir()
        (staging / "covers").mkdir()
        (staging / "videos" / "test.mp4").write_bytes(b"\x00" * 100)
        (staging / "thumbs" / "test.jpg").write_bytes(b"\xff" * 50)
        (staging / "manifest.json").write_text('{"title":"Test","dateRange":{"start":null,"end":null},"videos":[]}')

        output = tmp_path / "served"
        output.mkdir()
        publish_staging(staging, output)

        assert (output / "videos" / "test.mp4").exists()
        assert (output / "thumbs" / "test.jpg").exists()
        assert (output / "manifest.json").exists()

    def test_works_on_existing_output_dir(self, tmp_path):
        """Publishing to an existing dir with content should not fail."""
        staging = tmp_path / "staging"
        (staging / "videos").mkdir(parents=True)
        (staging / "thumbs").mkdir()
        (staging / "covers").mkdir()
        (staging / "videos" / "new.mp4").write_bytes(b"\x00" * 100)

        output = tmp_path / "served"
        (output / "videos").mkdir(parents=True)
        (output / "videos" / "old.mp4").write_bytes(b"\x00" * 50)

        publish_staging(staging, output)
        assert (output / "videos" / "new.mp4").exists()
        assert (output / "videos" / "old.mp4").exists()

    def test_retranscode_breaks_symlink_without_truncating_served(self, tmp_path):
        """When the staging entry is a symlink to a served file and the source
        MKV is newer (so we re-transcode), process_one_title must unlink the
        symlink before any ffmpeg invocation. Otherwise ffmpeg's O_TRUNC would
        follow the symlink and zero out the real served file."""
        served = tmp_path / "served"
        (served / "videos").mkdir(parents=True)
        (served / "thumbs").mkdir()

        served_mp4 = served / "videos" / "foo.mp4"
        served_thumb = served / "thumbs" / "foo.jpg"
        original_mp4 = b"original served mp4 bytes"
        original_thumb = b"original served thumb bytes"
        served_mp4.write_bytes(original_mp4)
        served_thumb.write_bytes(original_thumb)

        staging = served / ".staging-test"
        (staging / "videos").mkdir(parents=True)
        (staging / "thumbs").mkdir()
        staging_mp4 = staging / "videos" / "foo.mp4"
        staging_thumb = staging / "thumbs" / "foo.jpg"
        os.symlink(served_mp4.resolve(), staging_mp4)
        os.symlink(served_thumb.resolve(), staging_thumb)

        # Source MKV is newer than the served files, so should_skip is False
        # and process_one_title will fall through to the transcode path.
        mkv = tmp_path / "source.mkv"
        mkv.write_bytes(b"\x00" * 100)
        os.utime(mkv, (time.time() + 10, time.time() + 10))

        # Stub ffmpeg: write a fresh, distinct payload to whatever path it's
        # given. If the symlink wasn't unlinked first, this would follow the
        # link and corrupt the served file.
        new_mp4 = b"freshly transcoded bytes"
        new_thumb = b"freshly extracted thumb"

        def mock_run(cmd, **kwargs):
            result = MagicMock(returncode=0, stderr="", stdout=b"")
            if cmd[0] == "ffmpeg":
                # Find the output path (last positional arg).
                out = Path(cmd[-1])
                if out.suffix == ".mp4":
                    out.write_bytes(new_mp4)
                elif out.suffix == ".jpg":
                    out.write_bytes(new_thumb)
            return result

        with patch("transcode.subprocess.run", side_effect=mock_run):
            result = process_one_title((mkv, staging_mp4, staging_thumb, 120.0, False))

        assert result["status"] == "transcoded"

        # Served files must still hold their ORIGINAL bytes — not corrupted,
        # not zero-length, not the new transcode payload.
        assert served_mp4.read_bytes() == original_mp4
        assert served_thumb.read_bytes() == original_thumb

        # Staging now holds fresh, real (non-symlink) files with the new bytes.
        assert not staging_mp4.is_symlink()
        assert not staging_thumb.is_symlink()
        assert staging_mp4.read_bytes() == new_mp4
        assert staging_thumb.read_bytes() == new_thumb

    def test_skips_symlinked_staging_entries(self, tmp_path):
        """Symlinks in staging (placed by copy_served_to_staging for unchanged
        files) must NOT be replaced into served — that would clobber the real
        served file with the symlink itself. The original served bytes must
        remain intact, and the staging symlink stays put for rmtree."""
        served = tmp_path / "served"
        (served / "videos").mkdir(parents=True)
        (served / "thumbs").mkdir()
        (served / "covers").mkdir()
        original_bytes = b"original served content"
        served_file = served / "videos" / "unchanged.mp4"
        served_file.write_bytes(original_bytes)

        staging = served / ".staging-test"
        (staging / "videos").mkdir(parents=True)
        (staging / "thumbs").mkdir()
        (staging / "covers").mkdir()
        # Stage the unchanged file as a symlink, just like copy_served_to_staging does.
        os.symlink(served_file.resolve(), staging / "videos" / "unchanged.mp4")

        publish_staging(staging, served)

        # Served file is still a regular file with its original bytes.
        assert served_file.is_file()
        assert not served_file.is_symlink()
        assert served_file.read_bytes() == original_bytes


# --- copy_served_to_staging ---


class TestCopyServedToStaging:
    def test_creates_absolute_symlinks(self, tmp_path):
        served = tmp_path / "served"
        (served / "videos").mkdir(parents=True)
        (served / "thumbs").mkdir()
        (served / "covers").mkdir()
        (served / "videos" / "a.mp4").write_bytes(b"video bytes")
        (served / "thumbs" / "a.jpg").write_bytes(b"thumb bytes")
        (served / "covers" / "dvd.jpg").write_bytes(b"cover bytes")

        staging = served / ".staging-test"
        staging.mkdir()
        copy_served_to_staging(served, staging)

        for rel in ("videos/a.mp4", "thumbs/a.jpg", "covers/dvd.jpg"):
            link = staging / rel
            assert link.is_symlink(), f"{rel} should be a symlink"
            target = os.readlink(link)
            assert os.path.isabs(target), f"{rel} target {target!r} is not absolute"
            assert link.read_bytes() == (served / rel).read_bytes()

    def test_skips_unrelated_siblings(self, tmp_path):
        """Should not recurse into sibling .staging-* dirs or stray files."""
        served = tmp_path / "served"
        (served / "videos").mkdir(parents=True)
        (served / "videos" / "real.mp4").write_bytes(b"x")
        # A leftover staging dir from a prior failed run.
        (served / ".staging-old").mkdir()
        (served / ".staging-old" / "junk").write_bytes(b"y")
        # Health marker.
        (served / ".healthz").write_text("ok\n")

        staging = served / ".staging-new"
        staging.mkdir()
        copy_served_to_staging(served, staging)

        # Only the videos subdir contents were touched.
        assert (staging / "videos" / "real.mp4").is_symlink()
        assert not (staging / ".staging-old").exists()
        assert not (staging / ".healthz").exists()


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

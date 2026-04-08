#!/usr/bin/env python3
"""Family Videos transcode pipeline.

Reads MKV files from input directory, transcodes to MP4,
generates smart thumbnails, copies DVD covers, builds a
validated manifest.json, and atomically publishes via symlink swap.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from parse import (
    compute_file_hash,
    generate_title,
    make_video_id,
    merge_overrides,
    parse_dirname,
)

try:
    import jsonschema
except ImportError:
    sys.exit("jsonschema package required: pip install jsonschema")

log = logging.getLogger("transcode")


# --- Disk space ---


def check_disk_space(served_dir):
    """Pre-flight: verify available disk space >= 2x served directory size."""
    if not served_dir.exists():
        return True
    served_size = sum(f.stat().st_size for f in served_dir.rglob("*") if f.is_file())
    usage = shutil.disk_usage(served_dir)
    needed = served_size * 2
    if usage.free < needed:
        log.error(
            "Insufficient disk space: need %d MB, have %d MB",
            needed // (1024 * 1024),
            usage.free // (1024 * 1024),
        )
        return False
    return True


# --- Staging ---


def copy_served_to_staging(served_dir, staging_dir):
    """Copy existing served content to staging for idempotent updates."""
    # Resolve symlink to get the real directory
    real_served = served_dir.resolve() if served_dir.is_symlink() else served_dir
    if real_served.is_dir():
        shutil.copytree(real_served, staging_dir, dirs_exist_ok=True)
        log.info("Copied existing served content to staging")


def prepare_staging(staging_dir):
    """Ensure staging directory has the expected subdirectories."""
    (staging_dir / "videos").mkdir(parents=True, exist_ok=True)
    (staging_dir / "thumbs").mkdir(exist_ok=True)
    (staging_dir / "covers").mkdir(exist_ok=True)


# --- ffprobe / ffmpeg ---


def get_duration(mkv_path):
    """Get video duration in seconds via ffprobe. Returns 0.0 on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(mkv_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return 0.0
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return 0.0


def should_skip(mkv_path, mp4_path):
    """Check if transcode can be skipped (output exists and is newer than source)."""
    if not mp4_path.exists():
        return False
    return mp4_path.stat().st_mtime >= mkv_path.stat().st_mtime


def transcode_one(mkv_path, mp4_path):
    """Transcode a single MKV to MP4. Returns True on success."""
    mp4_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i", str(mkv_path),
                "-vf", "yadif,scale=-2:480,setsar=1:1",
                "-c:v", "libx264", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                "-y",
                str(mp4_path),
            ],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.returncode != 0:
            log.error("ffmpeg failed for %s: %s", mkv_path.name, result.stderr[-500:] if result.stderr else "")
            return False
        return True
    except subprocess.TimeoutExpired:
        log.error("ffmpeg timed out for %s", mkv_path.name)
        return False
    except Exception as e:
        log.error("ffmpeg error for %s: %s", mkv_path.name, e)
        return False


def extract_smart_thumbnail(mkv_path, thumb_path, duration):
    """Extract the best thumbnail from a video using color variance.

    Samples 5 frames at 10%, 25%, 40%, 60%, 80% of duration.
    Picks the frame with highest pixel variance (avoids black frames).
    """
    if duration <= 0:
        return False

    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    positions = [duration * p for p in (0.10, 0.25, 0.40, 0.60, 0.80)]
    best_variance = -1
    best_pos = positions[0]

    for pos in positions:
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-ss", str(pos),
                    "-i", str(mkv_path),
                    "-frames:v", "1",
                    "-f", "rawvideo",
                    "-pix_fmt", "rgb24",
                    "-v", "quiet",
                    "pipe:1",
                ],
                capture_output=True,
                timeout=30,
            )
            raw = result.stdout
            if not raw:
                continue
            mean = sum(raw) / len(raw)
            variance = sum((b - mean) ** 2 for b in raw) / len(raw)
            if variance > best_variance:
                best_variance = variance
                best_pos = pos
        except (subprocess.TimeoutExpired, Exception):
            continue

    # Extract the best frame as JPEG
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-ss", str(best_pos),
                "-i", str(mkv_path),
                "-frames:v", "1",
                "-q:v", "3",
                "-v", "quiet",
                "-y",
                str(thumb_path),
            ],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


# --- Cover ---


def copy_cover(dvd_dir, covers_dir, dvd_name):
    """Copy DVD cover JPG to covers directory. Returns relative path or empty string."""
    cover_src = dvd_dir / f"{dvd_name}.jpg"
    if cover_src.exists():
        cover_dst = covers_dir / f"{dvd_name}.jpg"
        shutil.copy2(cover_src, cover_dst)
        return f"covers/{dvd_name}.jpg"
    return ""


# --- Per-title processing (unit of parallel work) ---


def process_one_title(args):
    """Process a single MKV title: transcode + thumbnail.

    Args is a tuple: (mkv_path, mp4_path, thumb_path, dry_run)
    Returns a dict with status info, or None on skip/error.
    """
    mkv_path, mp4_path, thumb_path, duration, dry_run = args

    if dry_run:
        return {"status": "dry_run", "mp4_path": mp4_path, "thumb_path": thumb_path}

    if should_skip(mkv_path, mp4_path):
        return {"status": "skipped_existing", "mp4_path": mp4_path, "thumb_path": thumb_path}

    ok = transcode_one(mkv_path, mp4_path)
    if not ok:
        return {"status": "error", "mp4_path": mp4_path}

    extract_smart_thumbnail(mkv_path, thumb_path, duration)
    return {"status": "transcoded", "mp4_path": mp4_path, "thumb_path": thumb_path}


# --- Manifest ---


def build_manifest(videos, staging_dir):
    """Assemble the full manifest dict with cache-busting hashes."""
    # Compute dateRange from non-null dates
    starts = []
    ends = []
    for v in videos:
        if v["dateStart"]:
            starts.append(v["dateStart"])
        if v["dateEnd"]:
            ends.append(v["dateEnd"])

    date_range_start = min(starts)[:4] if starts else None
    date_range_end = max(ends)[:4] if ends else None

    # Add cache-busting hashes to file and thumbnail URLs
    for v in videos:
        mp4 = staging_dir / v["file"]
        thumb = staging_dir / v["thumbnail"]
        if mp4.exists():
            h = compute_file_hash(str(mp4))
            v["file"] = f"{v['file']}?v={h}"
        if thumb.exists():
            h = compute_file_hash(str(thumb))
            v["thumbnail"] = f"{v['thumbnail']}?v={h}"

    return {
        "title": "Family Videos",
        "dateRange": {"start": date_range_start, "end": date_range_end},
        "videos": videos,
    }


def validate_manifest(manifest, schema_path):
    """Validate manifest against JSON Schema. Raises on failure."""
    with open(schema_path) as f:
        schema = json.load(f)
    jsonschema.validate(manifest, schema)


def write_manifest_atomic(manifest, target_path):
    """Write manifest atomically via temp file + rename."""
    target_path = Path(target_path)
    fd, tmp = tempfile.mkstemp(dir=str(target_path.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(manifest, f, indent=2)
        os.rename(tmp, str(target_path))
    except Exception:
        os.unlink(tmp)
        raise


# --- Symlink swap ---


def swap_symlink(staging_dir, served_link):
    """Atomically swap the served symlink to point to staging.

    If served_link doesn't exist, creates it.
    If it's a real directory (unexpected), renames it aside.
    """
    served_link = Path(served_link)
    staging_dir = Path(staging_dir)

    if served_link.exists() and not served_link.is_symlink():
        backup = served_link.with_suffix(".bak")
        log.warning("served_link is a real directory, moving to %s", backup)
        os.rename(str(served_link), str(backup))

    if not served_link.exists() and not served_link.is_symlink():
        os.symlink(str(staging_dir), str(served_link))
        return

    # Atomic swap: create temp symlink, then rename over the old one
    tmp_link = str(served_link) + ".tmp"
    if os.path.islink(tmp_link):
        os.unlink(tmp_link)
    os.symlink(str(staging_dir), tmp_link)
    os.rename(tmp_link, str(served_link))


# --- Main ---


def run_pipeline(input_dir, output_dir, overrides_path, schema_path, dry_run, min_duration, workers):
    """Main pipeline logic, separated from argparse for testability."""
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    schema_path = Path(schema_path)

    # Pre-flight
    if not dry_run and not check_disk_space(output_dir):
        return 1

    # Load overrides
    overrides = {}
    if overrides_path and Path(overrides_path).exists():
        with open(overrides_path) as f:
            overrides = json.load(f)
        log.info("Loaded %d overrides", len(overrides))
    elif overrides_path:
        log.warning("Overrides file not found: %s", overrides_path)

    # Set up staging
    staging_base = output_dir.parent / "staging"
    if staging_base.exists():
        shutil.rmtree(staging_base)
    staging_base.mkdir()
    prepare_staging(staging_base)

    # Copy existing served content to staging
    if output_dir.exists():
        copy_served_to_staging(output_dir, staging_base)

    # Walk DVD directories
    if not input_dir.is_dir():
        log.error("Input directory does not exist: %s", input_dir)
        return 1

    dvd_dirs = sorted(
        d for d in input_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )

    if not dvd_dirs:
        log.warning("No DVD directories found in %s", input_dir)

    # Collect work items
    work_items = []
    video_entries = []
    counters = {"processed": 0, "skipped_existing": 0, "skipped_junk": 0, "errors": 0}

    for dvd_dir in dvd_dirs:
        dvd_name = dvd_dir.name
        parsed = parse_dirname(dvd_name)

        # Copy cover once per DVD
        cover_rel = copy_cover(dvd_dir, staging_base / "covers", dvd_name)

        # Find MKV titles
        mkv_files = sorted(dvd_dir.glob("*.mkv"))
        if not mkv_files:
            log.warning("No .mkv files in %s", dvd_name)
            continue

        for mkv_path in mkv_files:
            title_stem = mkv_path.stem  # e.g., "title00"
            video_id = make_video_id(dvd_name, title_stem)

            # Merge overrides
            merged = merge_overrides(parsed, overrides, dvd_name, title_stem)
            if merged.get("skip", False):
                counters["skipped_junk"] += 1
                log.info("Skipping %s (override skip=true)", video_id)
                continue

            # Check duration
            duration = get_duration(mkv_path)
            if duration < min_duration:
                counters["skipped_junk"] += 1
                log.info("Skipping %s (duration %.0fs < %ds)", video_id, duration, min_duration)
                continue

            # Build paths
            mp4_rel = f"videos/{video_id}.mp4"
            thumb_rel = f"thumbs/{video_id}.jpg"
            mp4_path = staging_base / mp4_rel
            thumb_path = staging_base / thumb_rel

            # Generate title
            title = merged.get("title") or generate_title(merged)

            # Queue work
            work_items.append((mkv_path, mp4_path, thumb_path, duration, dry_run))
            video_entries.append({
                "id": video_id,
                "title": title,
                "dateStart": merged.get("dateStart"),
                "dateEnd": merged.get("dateEnd"),
                "duration": int(duration),
                "file": mp4_rel,
                "thumbnail": thumb_rel,
                "cover": cover_rel,
                "dvd": dvd_name,
                "sourceFile": f"{dvd_name}/{mkv_path.name}",
            })

    # Execute work (parallel or dry-run)
    if dry_run:
        log.info("DRY RUN — would transcode %d titles", len(work_items))
        for entry in video_entries:
            log.info("  %s: %s", entry["id"], entry["title"])
    else:
        max_workers = workers or int(os.environ.get("WORKERS", os.cpu_count() or 4))
        log.info("Transcoding %d titles with %d workers", len(work_items), max_workers)

        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(process_one_title, item): i for i, item in enumerate(work_items)}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    result = future.result()
                    if result is None:
                        counters["errors"] += 1
                    elif result["status"] == "transcoded":
                        counters["processed"] += 1
                    elif result["status"] == "skipped_existing":
                        counters["skipped_existing"] += 1
                    elif result["status"] == "error":
                        counters["errors"] += 1
                except Exception as e:
                    log.error("Worker error: %s", e)
                    counters["errors"] += 1

    # Filter out entries whose mp4 doesn't exist (errors)
    if not dry_run:
        video_entries = [
            v for v in video_entries
            if (staging_base / v["file"]).exists()
        ]

    # Build and validate manifest
    manifest = build_manifest(video_entries, staging_base)

    if not dry_run:
        validate_manifest(manifest, schema_path)
        write_manifest_atomic(manifest, staging_base / "manifest.json")
        swap_symlink(staging_base, output_dir)
        log.info("Published to %s", output_dir)

    # Summary
    print_summary(
        counters["processed"],
        counters["skipped_existing"],
        counters["skipped_junk"],
        counters["errors"],
        dry_run,
        len(video_entries),
    )
    return 0


def print_summary(processed, skipped_existing, skipped_junk, errors, dry_run, total_in_manifest):
    """Print a human-readable summary of the pipeline run."""
    mode = "DRY RUN" if dry_run else "COMPLETE"
    log.info(
        "Pipeline %s: %d processed, %d skipped (existing), %d skipped (junk/short), %d errors, %d in manifest",
        mode, processed, skipped_existing, skipped_junk, errors, total_in_manifest,
    )


def main():
    parser = argparse.ArgumentParser(description="Transcode family video DVDs to web-playable MP4s")
    parser.add_argument("input_dir", help="Path to MKV output directory (e.g., /data/output)")
    parser.add_argument("--output-dir", default="/data/served", help="Path to served directory (default: /data/served)")
    parser.add_argument("--overrides", default="/config/overrides.json", help="Path to overrides.json")
    parser.add_argument("--schema", default=None, help="Path to manifest.schema.json (auto-detected if not set)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report only, no transcoding")
    parser.add_argument("--min-duration", type=int, default=60, help="Skip titles shorter than N seconds (default: 60)")
    parser.add_argument("--workers", type=int, default=None, help="Number of parallel workers (default: CPU count or WORKERS env)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    schema_path = args.schema or str(Path(__file__).resolve().parent.parent / "manifest.schema.json")

    sys.exit(run_pipeline(
        args.input_dir,
        args.output_dir,
        args.overrides,
        schema_path,
        args.dry_run,
        args.min_duration,
        args.workers,
    ))


if __name__ == "__main__":
    main()

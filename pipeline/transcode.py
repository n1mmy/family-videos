#!/usr/bin/env python3
"""Family Videos transcode pipeline.

Reads MKV files from input directory, transcodes to MP4, generates smart
thumbnails, copies DVD covers, and builds a validated manifest.json. New
outputs are written to a hidden staging dir inside /data/served and
published via per-file os.replace; the atomic manifest.json rename at the
end of publish_staging is the commit point. Unchanged files are
represented in staging as cheap absolute symlinks into served.
"""

import argparse
import errno
import fcntl
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


# The three published content subdirs under /data/served. Anything else at
# the root of served (.healthz, .staging-*, etc.) is bookkeeping that the
# pipeline owns but does not publish via the manifest.
CONTENT_SUBDIRS = ("videos", "thumbs", "covers")


# --- Disk space ---


def check_disk_space(served_dir):
    """Pre-flight: verify served filesystem has headroom for new transcodes.

    Staging now lives inside served_dir and symlinks existing files into
    place, so the published tree doesn't temporarily double in size. We
    just need enough free space to write whatever new MP4s this run
    produces. We can't cheaply predict that, so require 10% of current
    published content as headroom (with a 1 GiB floor for empty/new
    served dirs). Only the published CONTENT_SUBDIRS are measured —
    leftover .staging-* dirs from prior crashed runs (which contain
    symlinks that would otherwise inflate the size via stat-follows) are
    intentionally excluded.
    """
    if not served_dir.exists():
        return True
    served_size = 0
    for subdir in CONTENT_SUBDIRS:
        d = served_dir / subdir
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and not f.is_symlink():
                served_size += f.stat().st_size
    usage = shutil.disk_usage(served_dir)
    needed = max(served_size // 10, 1024 * 1024 * 1024)
    if usage.free < needed:
        log.error(
            "Insufficient disk space on %s: need %d MB headroom, have %d MB free",
            served_dir,
            needed // (1024 * 1024),
            usage.free // (1024 * 1024),
        )
        return False
    return True


# --- Staging ---


def copy_served_to_staging(served_dir, staging_dir):
    """Symlink existing served content into staging for idempotent updates.

    Uses absolute symlinks instead of full copies — staging now lives on
    the same filesystem as served, and CephFS handles symlinks much more
    cheaply than hardlinks (which incur per-link MDS bookkeeping). Only
    the three known content subdirs are linked, which avoids any risk of
    recursing into the staging dir itself or other unexpected siblings
    (e.g., .healthz, prior staging dirs).

    publish_staging will skip these symlinks at publish time — the served
    target is already where it needs to be. Re-transcodes break the link
    safely because process_one_title unlinks the symlink before invoking
    ffmpeg, so the served target is never opened with O_TRUNC.
    """
    real_served = served_dir.resolve()
    if not real_served.is_dir():
        return
    count = 0
    for subdir in CONTENT_SUBDIRS:
        src_dir = real_served / subdir
        if not src_dir.is_dir():
            continue
        dst_dir = staging_dir / subdir
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in src_dir.iterdir():
            if not f.is_file() or f.is_symlink():
                continue
            dst = dst_dir / f.name
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            # f is already absolute (parent real_served was resolve()'d
            # above) and not a symlink, so f.resolve() would be a wasted
            # realpath() syscall per file on CephFS.
            os.symlink(f, dst)
            count += 1
    log.info("Symlinked %d existing files into staging", count)


def prepare_staging(staging_dir):
    """Ensure staging directory has the expected subdirectories."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    for subdir in CONTENT_SUBDIRS:
        (staging_dir / subdir).mkdir(exist_ok=True)


def reap_stale_staging(output_dir):
    """Remove leftover .staging-* dirs from prior crashed runs.

    With staging now living inside output_dir on the persistent CephFS PVC
    (rather than the container's ephemeral root), crash residue is no
    longer wiped on pod restart. This reaper rmtrees any sibling .staging-*
    found at the root of output_dir before a new run begins. Safe because
    nginx only aliases CONTENT_SUBDIRS, and run_pipeline holds an
    exclusive flock so no concurrent run's live staging dir can be reaped.
    """
    if not output_dir.is_dir():
        return
    reaped = 0
    for entry in output_dir.iterdir():
        if entry.is_dir() and entry.name.startswith(".staging-"):
            shutil.rmtree(entry, ignore_errors=True)
            reaped += 1
    if reaped:
        log.info("Reaped %d leftover staging dirs from prior runs", reaped)


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
        if mp4_path.exists():
            mp4_path.unlink()
        return False
    except Exception as e:
        log.error("ffmpeg error for %s: %s", mkv_path.name, e)
        if mp4_path.exists():
            mp4_path.unlink()
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

    Args is a tuple: (mkv_path, mp4_path, thumb_path, duration, dry_run)
    Returns a dict with status info.
    """
    mkv_path, mp4_path, thumb_path, duration, dry_run = args

    if dry_run:
        return {"status": "dry_run", "mp4_path": mp4_path, "thumb_path": thumb_path}

    if should_skip(mkv_path, mp4_path):
        return {"status": "skipped_existing", "mp4_path": mp4_path, "thumb_path": thumb_path}

    # Break any symlink from copy_served_to_staging before re-transcoding.
    # ffmpeg's -y opens output files via open(O_WRONLY|O_CREAT|O_TRUNC),
    # which follows symlinks and would truncate the served target on the
    # other end. Unlinking removes only the staging-side symlink; the
    # served file stays intact until publish replaces it with the new one.
    if mp4_path.is_symlink() or mp4_path.exists():
        mp4_path.unlink()
    if thumb_path.is_symlink() or thumb_path.exists():
        thumb_path.unlink()

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


# --- Publish ---


def publish_staging(staging_dir, output_dir):
    """Publish staged content to the output directory.

    Real files in staging (newly transcoded outputs) are moved with
    os.replace — an atomic per-file rename on the same filesystem, no
    bytes copied. Symlinks in staging are skipped: they were placed by
    copy_served_to_staging to mark "unchanged from served, leave alone",
    and replacing them would atomically clobber the served target with
    the symlink itself. They get cleaned up when staging is rmtree'd.

    Falls back to shutil.copy2 + unlink on EXDEV so this still works in
    tests (and any caller) that places staging on a different mount.

    The atomic manifest.json write at the end is the commit point.
    """
    staging_dir = Path(staging_dir)
    output_dir = Path(output_dir)

    for subdir in CONTENT_SUBDIRS:
        (output_dir / subdir).mkdir(parents=True, exist_ok=True)

    for subdir in CONTENT_SUBDIRS:
        src = staging_dir / subdir
        if not src.is_dir():
            continue
        dst = output_dir / subdir
        for f in src.iterdir():
            if f.is_symlink() or not f.is_file():
                continue
            target = dst / f.name
            try:
                os.replace(f, target)
            except OSError as e:
                if e.errno == errno.EXDEV:
                    shutil.copy2(f, target)
                    f.unlink()
                else:
                    raise

    # Atomic manifest write is the publish signal
    manifest_src = staging_dir / "manifest.json"
    if manifest_src.exists():
        write_manifest_atomic(
            json.load(open(manifest_src)),
            output_dir / "manifest.json",
        )

    # Write health check marker (k8s readiness probe reads this via nginx)
    (output_dir / ".healthz").write_text("ok\n")


# --- Main ---


def run_pipeline(input_dir, output_dir, overrides_path, schema_path, dry_run, min_duration, workers):
    """Main pipeline logic, separated from argparse for testability.

    Holds an exclusive advisory flock on output_dir/.transcode.lock for
    the entire run, so the reaper, staging setup, transcodes, and publish
    are guaranteed single-writer even if a stray operator launches a
    second pipeline or k8s retries the Job before the prior pod's exit
    has been observed. Returns 2 if the lock is already held.
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    schema_path = Path(schema_path)

    # Pre-flight that doesn't need the lock
    if not input_dir.is_dir():
        log.error("Input directory does not exist: %s", input_dir)
        return 1
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

    output_dir.mkdir(parents=True, exist_ok=True)

    # Single-writer lock. Held for the rest of run_pipeline so the reaper,
    # mkdtemp, transcodes, and publish are all guaranteed to be the only
    # writers under output_dir. Released when the fd is closed in finally.
    lock_path = output_dir / ".transcode.lock"
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("Another transcode pipeline already holds %s — aborting", lock_path)
        os.close(lock_fd)
        return 2

    staging_base = None
    try:
        # Reap leftover staging dirs from prior crashed runs (safe under lock).
        reap_stale_staging(output_dir)

        # Set up staging *inside* output_dir so it shares the served
        # filesystem. That lets copy_served_to_staging symlink existing
        # content (cheap on CephFS, unlike hardlinks) and publish_staging
        # use os.replace (atomic per-file rename, no bytes copied). The
        # leading dot keeps the dir invisible to nginx, which only aliases
        # /videos/, /thumbs/, /covers/.
        staging_base = Path(tempfile.mkdtemp(
            dir=str(output_dir),
            prefix=".staging-",
        ))
        prepare_staging(staging_base)

        # Copy existing served content to staging (preserves previously transcoded files)
        if output_dir.exists() and output_dir.is_dir():
            copy_served_to_staging(output_dir, staging_base)

        return _run_pipeline_body(
            input_dir, output_dir, staging_base, overrides,
            schema_path, dry_run, min_duration, workers,
        )
    finally:
        if staging_base is not None:
            shutil.rmtree(staging_base, ignore_errors=True)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _run_pipeline_body(input_dir, output_dir, staging_base, overrides, schema_path, dry_run, min_duration, workers):
    """Inner body of run_pipeline. Assumes the lock is held and staging is set up.

    Split out so run_pipeline can keep the flock + reaper + staging
    cleanup logic at one indentation level and the transcode work itself
    at another. Returns the same exit code as run_pipeline.
    """
    dvd_dirs = sorted(
        d for d in input_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )

    if not dvd_dirs:
        log.warning("No DVD directories found in %s", input_dir)
    else:
        log.info("Found %d DVD directories in %s", len(dvd_dirs), input_dir)

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
        total = len(work_items)
        log.info("Transcoding %d titles with %d workers", total, max_workers)

        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(process_one_title, item) for item in work_items]
            done = 0
            for future in as_completed(futures):
                done += 1
                try:
                    result = future.result()
                    if result is None:
                        counters["errors"] += 1
                        log.warning("[%d/%d] error (no result)", done, total)
                    elif result["status"] == "transcoded":
                        counters["processed"] += 1
                        log.info("[%d/%d] transcoded %s", done, total, result["mp4_path"].name)
                    elif result["status"] == "skipped_existing":
                        counters["skipped_existing"] += 1
                        log.info("[%d/%d] skipped (up to date) %s", done, total, result["mp4_path"].name)
                    elif result["status"] == "error":
                        counters["errors"] += 1
                        log.warning("[%d/%d] FAILED %s", done, total, result["mp4_path"].name)
                except Exception as e:
                    log.error("[%d/%d] worker error: %s", done, total, e)
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
        log.info("Validating manifest (%d entries)", len(video_entries))
        validate_manifest(manifest, schema_path)
        write_manifest_atomic(manifest, staging_base / "manifest.json")
        log.info("Publishing staged content to %s", output_dir)
        publish_staging(staging_base, output_dir)
        log.info("Published to %s", output_dir)
    # Note: staging cleanup is handled by run_pipeline's finally block,
    # so dry-run, error paths, and exceptions all get the same cleanup.

    # Summary
    print_summary(
        counters["processed"],
        counters["skipped_existing"],
        counters["skipped_junk"],
        counters["errors"],
        dry_run,
        len(video_entries),
    )
    if counters["errors"] > 0:
        log.error("%d transcode errors occurred", counters["errors"])
        return 1
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

    # In Docker, schema is at /app/manifest.schema.json (same dir as script).
    # In dev, schema is at repo root (parent of pipeline/).
    script_dir = Path(__file__).resolve().parent
    schema_path = args.schema
    if not schema_path:
        candidate = script_dir / "manifest.schema.json"
        if candidate.exists():
            schema_path = str(candidate)
        else:
            schema_path = str(script_dir.parent / "manifest.schema.json")

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

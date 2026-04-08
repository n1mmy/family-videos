# Changelog

All notable changes to Family Videos will be documented in this file.

## [0.1.1.0] - 2026-04-08

### Fixed
- Published `manifest.json` is now readable to nginx in the staging container. The transcode pipeline runs as root, nginx runs as the unprivileged `nginx` user, and `tempfile.mkstemp` was creating the manifest's temp file with mode `0o600` per its documented security contract. The atomic rename preserved that mode, so nginx got `EACCES` on every request and the catalog page failed to load. `write_manifest_atomic` now `fchmod`s the open file descriptor to `0o644` before close, eliminating the path-based TOCTOU window between close and chmod.
- Pipeline now pins `umask 0o022` at the start of `run_pipeline` so directories, the `.healthz` readiness marker, transcoded mp4s, thumbnails, and covers are all world-readable regardless of the operator's process umask. Previously they relied on the alpine base image's default umask being `0o022`, which would have silently broken on a hardened base image with `umask 0o077`.

## [0.1.0.0] - 2026-04-07

### Changed
- Transcode pipeline now stages inside `/data/served` instead of the container's ephemeral root. Unchanged files are represented as cheap absolute symlinks into the served tree (faster than hardlinks on CephFS), and new transcodes publish via per-file `os.replace` for an atomic rename instead of a byte-by-byte copy back. The manifest.json rename at the end of publish is the commit point for the whole set.
- Pipeline runs now print per-title progress in the `as_completed` loop — `[N/total] transcoded foo.mp4`, `[N/total] skipped (up to date)`, or `[N/total] FAILED` — plus discovery and publish breadcrumbs, so long runs are observable instead of silent.
- Disk space pre-flight now measures only the three published content subdirs (videos/, thumbs/, covers/) via `rglob`, excluding leftover staging directories and the `.healthz`/`.transcode.lock` bookkeeping files. Headroom requirement dropped from 2× served size to 10% (with a 1 GiB floor).

### Added
- Single-writer advisory `fcntl.flock` on `/data/served/.transcode.lock` held for the entire pipeline run. A second concurrent invocation exits cleanly with code 2 instead of corrupting the manifest via a race. The lock file is opened with `O_CLOEXEC` (so ffmpeg subprocesses don't inherit it) and `O_NOFOLLOW` (so an operator-planted symlink at the lock path is rejected with ELOOP).
- Startup reaper `reap_stale_staging` that removes any leftover `.staging-*` directories from prior crashed runs. Handles real directories, stray symlinks, and regular files defensively — symlinks are unlinked without being followed, so a malicious `.staging-evil -> /etc` cannot escalate to deleting its target.
- `try/finally` cleanup wrapper around the pipeline body, so the staging dir is always `rmtree`'d — on normal return, dry runs, transcode errors, unhandled exceptions, and lock-acquisition failures. The lock is released in a nested `finally`, guaranteed even if staging cleanup raises.
- Pre-transcode `unlink` guard in `process_one_title` that breaks any symlink at the staging output path before invoking ffmpeg. Without it, ffmpeg's `-y` (open with `O_TRUNC`) would follow the staging symlink and zero out the served target on the other end.
- 25 new tests covering the symlink invariants, staging cleanup safety, flock semantics (concurrent-run rejection and release on every exit path), reaper symlink handling, `check_disk_space` formula, and the `copy_cover` symlink-break regression test. Test count grew from 43 to 68.

### Fixed
- `copy_cover` no longer follows the staging symlink and truncates the served original cover in place. `shutil.copy2` opens the destination with `O_TRUNC`, which bypassed this branch's atomic-publish guarantee for cover files specifically. The function now unlinks the staging entry before the copy, matching the pattern used in `process_one_title`.
- Pipeline filesystem errors from `mkdtemp`, the reaper, or the transcode body are no longer misreported as "Could not open lock file". The narrow `except OSError` around lock acquisition was previously catching every OSError raised inside the locked body and logging it with the wrong cause, making operational debugging harder.

## [0.0.2.0] - 2026-04-07

### Added
- nginx container image serving the frontend SPA with HTTP Basic Auth, rate limiting (5 req/s per real client IP), and cache headers (immutable for videos/thumbnails/covers, no-cache for manifest)
- Kubernetes deployment, service, and ingress manifests for hosting on a home k8s cluster
- Kustomize base/overlay pattern so cluster-specific details (domain, TLS, registry, credentials) stay out of the public repo
- Example overlay showing how to customize each manifest
- Readiness probe backed by a .healthz marker file on the PVC to detect Ceph mount failures
- Security headers on all responses (HSTS, X-Frame-Options, X-Content-Type-Options)
- Gzip compression for text assets (HTML, CSS, JS, JSON)
- Health check marker file written by the transcode pipeline for probe verification

## [0.0.1.0] - 2026-04-07

### Added
- Transcode pipeline that converts MKV files from DVD extracts into web-playable MP4s (h264 CRF 23, 480p, AAC 128k)
- Smart thumbnail selection that samples 5 frames per video and picks the one with highest visual variety (avoids black frames and color bars)
- DVD cover art copying to a served directory
- Filename parser that extracts dates and titles from DVD directory names (supports YYYYMMDD ranges, YYYYMM ranges, year+label, and label+year patterns)
- Override system via overrides.json for manual metadata corrections and title skipping
- JSON Schema validated manifest.json as the contract between pipeline and frontend
- Parallel transcoding via ProcessPoolExecutor with configurable worker count
- Idempotent re-runs: skips videos already transcoded unless source is newer
- Atomic manifest writes and safe staging-to-output publishing (works on k8s PVC mounts)
- Pre-flight disk space check and dry-run mode for previewing what would be transcoded
- Auto-skip for short titles (<60s) to filter junk MakeMKV menu extracts
- Dockerfile (Alpine + ffmpeg + Python) and k8s Job manifest with PVC mounts
- 43 pytest tests covering filename parsing, override merging, transcode logic, manifest validation, and publish workflow

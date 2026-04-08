# Changelog

All notable changes to Family Videos will be documented in this file.

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

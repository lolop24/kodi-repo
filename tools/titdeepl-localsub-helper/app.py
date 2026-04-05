#!/usr/bin/env python3
"""HTTP helper that queues OpenSubtitles upload jobs for TitDeepL LocalSub."""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from string import hexdigits
from pathlib import Path

from flask import Flask, jsonify, request

APP_ROOT = Path(__file__).resolve().parent
UPLOADER_PATH = APP_ROOT / "uploader.py"
DATA_DIR = Path(os.getenv("HELPER_DATA_DIR", APP_ROOT / ".data")).expanduser().resolve()
JOBS_DIR = DATA_DIR / "jobs"
BACKUP_SUBTITLES_DIR = DATA_DIR / "saved-subtitles"
BROWSER_PROFILE_DIR = DATA_DIR / "browser-profile"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
JOB_QUEUE: queue.Queue[str] = queue.Queue()
JOB_RUNTIME: dict[str, dict[str, object]] = {}
JOB_LOCK = threading.Lock()

app = Flask(__name__)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def ensure_dirs() -> None:
    for path in (DATA_DIR, JOBS_DIR, BACKUP_SUBTITLES_DIR, BROWSER_PROFILE_DIR, SCREENSHOT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sanitize_filename(name: str) -> str:
    cleaned = os.path.basename((name or "").strip().replace("\x00", ""))
    return cleaned or "subtitle.srt"


def slugify(value: str) -> str:
    allowed = []
    for char in (value or ""):
        if char.isalnum():
            allowed.append(char)
        elif char in ("-", "_", "."):
            allowed.append(char)
        else:
            allowed.append("_")
    return "".join(allowed).strip("._") or "subtitle"


def build_backup_subtitle_path(job_id: str, imdb_id: str, release_name: str, subtitle_filename: str) -> Path:
    stem, ext = os.path.splitext(sanitize_filename(subtitle_filename))
    release_slug = slugify(release_name)[:80]
    imdb_slug = slugify(imdb_id)
    backup_name = "%s_%s_%s%s" % (imdb_slug, release_slug, job_id[:8], ext or ".srt")
    return BACKUP_SUBTITLES_DIR / backup_name


def strip_subtitle_suffixes(value: str) -> str:
    stem = Path(sanitize_filename(value)).stem
    suffixes = (".upload", ".deepl", ".uk", ".ua", ".cs", ".cz", ".sk")
    changed = True
    while changed and stem:
        changed = False
        lowered = stem.lower()
        for suffix in suffixes:
            if lowered.endswith(suffix):
                stem = stem[: -len(suffix)].rstrip("._- ")
                changed = True
                break
    return stem


def build_lookup_candidates(release_name: str, source_filename: str) -> list[str]:
    candidates: list[str] = []
    for value in (
        release_name,
        strip_subtitle_suffixes(release_name),
        source_filename,
        strip_subtitle_suffixes(source_filename),
    ):
        slug = slugify(value)[:80]
        if slug and slug not in candidates:
            candidates.append(slug)
    return candidates


def extract_job_prefix_from_backup(path: Path) -> str:
    suffix = path.stem.rsplit("_", 1)[-1].lower()
    if len(suffix) == 8 and all(char in hexdigits for char in suffix):
        return suffix
    return ""


def find_job_by_prefix(job_prefix: str) -> dict[str, object] | None:
    if not job_prefix:
        return None
    for candidate in JOBS_DIR.iterdir():
        if candidate.is_dir() and candidate.name.startswith(job_prefix):
            return read_job(candidate.name)
    return None


def job_status_priority(job: dict[str, object] | None) -> int:
    status = str((job or {}).get("status") or "").strip().lower()
    if status == "finished":
        return 2
    if status in {"queued", "running"}:
        return 1
    return 0


def match_saved_subtitle(imdb_id: str, release_name: str, source_filename: str, language: str = "uk") -> dict[str, object] | None:
    imdb_slug = slugify(imdb_id)
    if not imdb_slug:
        return None

    candidates = build_lookup_candidates(release_name, source_filename)
    best: dict[str, object] | None = None

    for path in BACKUP_SUBTITLES_DIR.glob(f"{imdb_slug}_*.srt"):
        stem = path.stem
        score = 0
        matched_by = ""
        matched_value = ""

        for index, candidate in enumerate(candidates):
            exact_prefix = f"{imdb_slug}_{candidate}_"
            if stem.startswith(exact_prefix):
                score = 300 - index
                matched_by = "release_exact"
                matched_value = candidate
                break

        if not score:
            for index, candidate in enumerate(candidates):
                if candidate and candidate in stem:
                    score = 200 - index
                    matched_by = "release_contains"
                    matched_value = candidate
                    break

        if not score:
            continue

        stat = path.stat()
        job = find_job_by_prefix(extract_job_prefix_from_backup(path))
        priority = job_status_priority(job)
        if best is None or (score, priority, stat.st_mtime) > (
            int(best["score"]),
            int(best["job_priority"]),
            float(best["mtime"]),
        ):
            best = {
                "path": path,
                "score": score,
                "mtime": stat.st_mtime,
                "job_priority": priority,
                "job": job,
                "matched_by": matched_by,
                "matched_value": matched_value,
                "language": language,
            }

    return best


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def job_meta_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def read_job(job_id: str) -> dict[str, object] | None:
    path = job_meta_path(job_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_job(job: dict[str, object]) -> None:
    path = job_meta_path(str(job["job_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job, indent=2, sort_keys=True), encoding="utf-8")


def update_job(job_id: str, **changes) -> dict[str, object]:
    with JOB_LOCK:
        job = read_job(job_id)
        if job is None:
            raise KeyError(job_id)
        job.update(changes)
        job["updated_at"] = now_iso()
        write_job(job)
        return job


def require_auth():
    expected = os.getenv("HELPER_TOKEN", "").strip()
    if not expected:
        return None
    header = request.headers.get("Authorization", "").strip()
    if header == f"Bearer {expected}":
        return None
    return jsonify({"error": "Unauthorized"}), 401


def job_response(job: dict[str, object]) -> dict[str, object]:
    allowed = {
        "job_id",
        "status",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "error",
        "returncode",
        "subtitle_filename",
        "language",
        "imdb_id",
        "fps",
        "release_name",
        "machine_translated",
        "auto_submit",
        "log_path",
        "stored_subtitle_path",
    }
    return {key: value for key, value in job.items() if key in allowed and value not in (None, "")}


def build_upload_command(job: dict[str, object], runtime: dict[str, object]) -> tuple[list[str], dict[str, str]]:
    command = [sys.executable, str(UPLOADER_PATH)]
    if env_bool("HELPER_USE_XVFB", True) and not env_bool("HELPER_HEADLESS", False):
        command = ["xvfb-run", "-a"] + command

    command.extend(
        [
            "--subtitle",
            str(runtime["subtitle_path"]),
            "--language",
            str(job["language"]),
            "--imdb-id",
            str(job["imdb_id"]),
            "--fps",
            str(job.get("fps") or "25.000"),
            "--release-name",
            str(job["release_name"]),
            "--browser",
            os.getenv("HELPER_BROWSER", "auto"),
            "--user-data-dir",
            str(BROWSER_PROFILE_DIR),
            "--screenshot-dir",
            str(SCREENSHOT_DIR),
            "--timeout",
            os.getenv("HELPER_TIMEOUT", "120"),
        ]
    )

    browser_path = os.getenv("HELPER_BROWSER_PATH", "").strip()
    if browser_path:
        command.extend(["--browser-path", browser_path])
    if env_bool("HELPER_HEADLESS", False):
        command.append("--headless")
    if bool(job.get("machine_translated", False)):
        command.append("--machine-translated")
    if bool(job.get("auto_submit", False)) or env_bool("HELPER_FORCE_SUBMIT", False):
        command.append("--submit")
    if runtime.get("comment"):
        command.extend(["--comment", str(runtime["comment"])])

    env = os.environ.copy()
    username = str(runtime.get("username") or os.getenv("OPENSUBTITLES_USERNAME", "")).strip()
    password = str(runtime.get("password") or os.getenv("OPENSUBTITLES_PASSWORD", "")).strip()
    if username:
        env["OPENSUBTITLES_USERNAME"] = username
    if password:
        env["OPENSUBTITLES_PASSWORD"] = password
    if not username or not password:
        command.append("--anonymous")

    return command, env


def process_job(job_id: str) -> None:
    runtime = JOB_RUNTIME.get(job_id)
    if runtime is None:
        raise RuntimeError("Job runtime is missing.")

    job = update_job(job_id, status="running", started_at=now_iso(), error="")
    log_path = Path(str(job["log_path"]))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if env_bool("HELPER_DRY_RUN", False):
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("[%s] DRY RUN\n" % now_iso())
            handle.write(json.dumps(job_response(job), indent=2, sort_keys=True))
            handle.write("\n")
        update_job(job_id, status="finished", finished_at=now_iso(), returncode=0)
        JOB_RUNTIME.pop(job_id, None)
        return

    command, env = build_upload_command(job, runtime)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("[%s] Starting uploader\n" % now_iso())
        handle.write(json.dumps({"command": command}, indent=2))
        handle.write("\n")
        handle.flush()
        result = subprocess.run(
            command,
            cwd=str(APP_ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )

    if result.returncode != 0:
        update_job(
            job_id,
            status="failed",
            finished_at=now_iso(),
            returncode=result.returncode,
            error="Uploader exited with code %s" % result.returncode,
        )
        JOB_RUNTIME.pop(job_id, None)
        return

    update_job(job_id, status="finished", finished_at=now_iso(), returncode=0, error="")
    JOB_RUNTIME.pop(job_id, None)


def worker_loop() -> None:
    while True:
        job_id = JOB_QUEUE.get()
        try:
            process_job(job_id)
        except Exception as exc:  # pragma: no cover - background error path
            try:
                update_job(job_id, status="failed", finished_at=now_iso(), error=str(exc))
            except Exception:
                pass
            JOB_RUNTIME.pop(job_id, None)
        finally:
            JOB_QUEUE.task_done()


@app.get("/healthz")
def healthz():
    return jsonify(
        {
            "ok": True,
            "time": now_iso(),
            "dry_run": env_bool("HELPER_DRY_RUN", False),
            "data_dir": str(DATA_DIR),
            "browser": os.getenv("HELPER_BROWSER", "auto"),
        }
    )


@app.get("/api/stored-subtitles/lookup")
def lookup_stored_subtitle():
    auth = require_auth()
    if auth is not None:
        return auth

    imdb_id = str(request.args.get("imdb_id") or "").strip()
    release_name = str(request.args.get("release_name") or "").strip()
    source_filename = str(request.args.get("source_filename") or "").strip()
    language = str(request.args.get("language") or "uk").strip() or "uk"
    if not imdb_id:
        return jsonify({"error": "imdb_id is required."}), 400

    match = match_saved_subtitle(imdb_id, release_name, source_filename, language=language)
    if match is None:
        return jsonify({"found": False})

    subtitle_path = Path(match["path"])
    subtitle_content_b64 = base64.b64encode(subtitle_path.read_bytes()).decode("ascii")
    job = match.get("job")

    response = {
        "found": True,
        "subtitle_filename": subtitle_path.name,
        "stored_subtitle_path": str(subtitle_path),
        "subtitle_content_b64": subtitle_content_b64,
        "matched_by": match.get("matched_by", ""),
        "matched_value": match.get("matched_value", ""),
        "job_id": job.get("job_id", "") if job else "",
        "job_status": job.get("status", "") if job else "",
        "job_returncode": job.get("returncode", "") if job else "",
    }
    return jsonify(response)


@app.post("/api/upload-jobs")
def create_upload_job():
    auth = require_auth()
    if auth is not None:
        return auth

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected a JSON object."}), 400

    subtitle_content_b64 = str(payload.get("subtitle_content_b64") or "").strip()
    imdb_id = str(payload.get("imdb_id") or "").strip()
    release_name = str(payload.get("release_name") or "").strip()
    if not subtitle_content_b64:
        return jsonify({"error": "subtitle_content_b64 is required."}), 400
    if not imdb_id:
        return jsonify({"error": "imdb_id is required."}), 400
    if not release_name:
        return jsonify({"error": "release_name is required."}), 400

    try:
        subtitle_bytes = base64.b64decode(subtitle_content_b64.encode("ascii"), validate=True)
    except Exception:
        return jsonify({"error": "subtitle_content_b64 is not valid base64."}), 400

    job_id = uuid.uuid4().hex
    working_dir = job_dir(job_id)
    working_dir.mkdir(parents=True, exist_ok=True)
    subtitle_filename = sanitize_filename(str(payload.get("subtitle_filename") or "subtitle.srt"))
    subtitle_path = working_dir / subtitle_filename
    subtitle_path.write_bytes(subtitle_bytes)
    backup_subtitle_path = build_backup_subtitle_path(job_id, imdb_id, release_name, subtitle_filename)
    backup_subtitle_path.write_bytes(subtitle_bytes)

    log_path = working_dir / "job.log"
    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": "",
        "finished_at": "",
        "error": "",
        "returncode": None,
        "subtitle_filename": subtitle_filename,
        "subtitle_path": str(subtitle_path),
        "log_path": str(log_path),
        "stored_subtitle_path": str(backup_subtitle_path),
        "language": str(payload.get("language") or "uk"),
        "imdb_id": imdb_id,
        "fps": str(payload.get("fps") or "25.000"),
        "release_name": release_name,
        "machine_translated": bool(payload.get("machine_translated", True)),
        "auto_submit": bool(payload.get("auto_submit", False)),
    }
    write_job(job)

    JOB_RUNTIME[job_id] = {
        "subtitle_path": subtitle_path,
        "username": str(payload.get("username") or ""),
        "password": str(payload.get("password") or ""),
        "comment": str(payload.get("comment") or ""),
    }
    JOB_QUEUE.put(job_id)

    return jsonify({"job_id": job_id, "status": "queued"}), 202


@app.get("/api/upload-jobs/<job_id>")
def get_upload_job(job_id: str):
    auth = require_auth()
    if auth is not None:
        return auth

    job = read_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(job_response(job))


def main() -> int:
    ensure_dirs()
    worker = threading.Thread(target=worker_loop, name="upload-worker", daemon=True)
    worker.start()

    host = os.getenv("HELPER_HOST", "0.0.0.0")
    port = int(os.getenv("HELPER_PORT", "8097"))
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

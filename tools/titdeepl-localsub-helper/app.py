#!/usr/bin/env python3
"""HTTP helper that queues OpenSubtitles upload jobs for TitDeepL LocalSub."""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from string import hexdigits
from pathlib import Path

from flask import Flask, jsonify, request, send_file

APP_ROOT = Path(__file__).resolve().parent
UPLOADER_PATH = APP_ROOT / "uploader.py"
DATA_DIR = Path(os.getenv("HELPER_DATA_DIR", APP_ROOT / ".data")).expanduser().resolve()
JOBS_DIR = DATA_DIR / "jobs"
EMBEDDED_JOBS_DIR = DATA_DIR / "embedded-dual-jobs"
DEVICE_LOGS_DIR = DATA_DIR / "device-logs"
BACKUP_SUBTITLES_DIR = DATA_DIR / "saved-subtitles"
BROWSER_PROFILE_DIR = DATA_DIR / "browser-profile"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
JOB_QUEUE: queue.Queue[str] = queue.Queue()
EMBEDDED_QUEUE: queue.Queue[str] = queue.Queue()
JOB_RUNTIME: dict[str, dict[str, object]] = {}
JOB_LOCK = threading.Lock()
EMBEDDED_LOCK = threading.Lock()

app = Flask(__name__)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def ensure_dirs() -> None:
    for path in (
        DATA_DIR,
        JOBS_DIR,
        EMBEDDED_JOBS_DIR,
        DEVICE_LOGS_DIR,
        BACKUP_SUBTITLES_DIR,
        BROWSER_PROFILE_DIR,
        SCREENSHOT_DIR,
    ):
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


def clean_episode_number(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        number = int(float(text))
    except Exception:
        return ""
    return str(number) if number >= 0 else ""


def clean_positive_int(value: object, default: int, minimum: int = 1, maximum: int = 36000) -> int:
    try:
        number = int(float(str(value).strip()))
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def build_episode_key(season: object = "", episode: object = "") -> str:
    season_number = clean_episode_number(season)
    episode_number = clean_episode_number(episode)
    if not season_number or not episode_number:
        return ""
    return "s%02de%02d" % (int(season_number), int(episode_number))


def extract_episode_key(value: str) -> str:
    text = str(value or "")
    match = re.search(r"(?i)(?:^|[^A-Za-z0-9])S(\d{1,2})\s*[._ -]*E(\d{1,3})(?:$|[^A-Za-z0-9])", text)
    if not match:
        match = re.search(r"(?i)(?:^|[^A-Za-z0-9])(\d{1,2})x(\d{1,3})(?:$|[^A-Za-z0-9])", text)
    if not match:
        return ""
    return build_episode_key(match.group(1), match.group(2))


def build_backup_subtitle_path(
    job_id: str,
    imdb_id: str,
    release_name: str,
    subtitle_filename: str,
    season: object = "",
    episode: object = "",
) -> Path:
    stem, ext = os.path.splitext(sanitize_filename(subtitle_filename))
    release_slug = slugify(release_name)[:80]
    imdb_slug = slugify(imdb_id)
    episode_key = build_episode_key(season, episode) or extract_episode_key(release_name) or extract_episode_key(stem)
    parts = [imdb_slug]
    if episode_key:
        parts.append(episode_key)
    parts.extend([release_slug, job_id[:8]])
    backup_name = "%s%s" % ("_".join(part for part in parts if part), ext or ".srt")
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


def build_lookup_candidates(*values: str) -> list[str]:
    candidates: list[str] = []
    for value in values:
        for variant in (value, strip_subtitle_suffixes(value)):
            slug = slugify(variant)[:80]
            if slug and slug not in candidates:
                candidates.append(slug)
    return candidates


def build_lookup_summary(
    imdb_id: str = "",
    release_name: str = "",
    source_filename: str = "",
    title: str = "",
    year: str = "",
    tvshow_title: str = "",
    season: str = "",
    episode: str = "",
) -> str:
    parts = []
    episode_key = build_episode_key(season, episode)
    if imdb_id:
        parts.append("imdb=%s" % imdb_id)
    if tvshow_title:
        parts.append("show=%s" % tvshow_title)
    if episode_key:
        parts.append("episode=%s" % episode_key.upper())
    if release_name:
        parts.append("release=%s" % release_name)
    if source_filename:
        parts.append("source=%s" % source_filename)
    if title:
        parts.append("title=%s" % title)
    if year:
        parts.append("year=%s" % year)
    return ", ".join(parts) if parts else "no lookup keys"


def score_text_match(text: str, candidates: list[str], exact_base: int, contains_base: int, exact_label: str, contains_label: str):
    text_lookup = text.lower()
    for index, candidate in enumerate(candidates):
        candidate_lookup = candidate.lower()
        exact_token = "_%s_" % candidate_lookup
        if text_lookup.startswith("%s_" % candidate_lookup) or exact_token in text_lookup:
            return exact_base - index, exact_label, candidate
    for index, candidate in enumerate(candidates):
        candidate_lookup = candidate.lower()
        if candidate_lookup and candidate_lookup in text_lookup:
            return contains_base - index, contains_label, candidate
    return 0, "", ""


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


def match_saved_subtitle(
    imdb_id: str,
    release_name: str,
    source_filename: str,
    title: str = "",
    year: str = "",
    tvshow_title: str = "",
    season: str = "",
    episode: str = "",
    language: str = "uk",
) -> dict[str, object] | None:
    imdb_slug = slugify(imdb_id) if imdb_id else ""
    requested_episode_key = (
        build_episode_key(season, episode)
        or extract_episode_key(release_name)
        or extract_episode_key(source_filename)
        or extract_episode_key(title)
    )
    is_episode_lookup = bool(tvshow_title or season or episode or requested_episode_key)
    candidates = build_lookup_candidates(
        requested_episode_key,
        ("%s.%s" % (tvshow_title, requested_episode_key)) if tvshow_title and requested_episode_key else "",
        ("%s.%s" % (title, requested_episode_key)) if title and requested_episode_key else "",
        release_name,
        source_filename,
        title,
        ("%s.%s" % (title, year)) if title and year else "",
        ("%s.%s" % (release_name, year)) if release_name and year else "",
    )
    if not imdb_slug and not candidates:
        return None

    best: dict[str, object] | None = None

    if imdb_slug:
        paths = list(BACKUP_SUBTITLES_DIR.glob(f"{imdb_slug}_*.srt"))
        fallback_paths = list(BACKUP_SUBTITLES_DIR.glob("*.srt"))
    else:
        paths = list(BACKUP_SUBTITLES_DIR.glob("*.srt"))
        fallback_paths = paths

    for path in paths:
        stem = path.stem
        score = 0
        matched_by = ""
        matched_value = ""

        if imdb_slug and stem.startswith("%s_" % imdb_slug):
            score, matched_by, matched_value = score_text_match(
                stem,
                candidates,
                exact_base=400,
                contains_base=300,
                exact_label="release_exact_imdb",
                contains_label="release_contains_imdb",
            )
            if not score:
                if is_episode_lookup:
                    continue
                score = 250
                matched_by = "imdb_only"
                matched_value = imdb_slug

        if requested_episode_key:
            stored_episode_key = extract_episode_key(stem)
            job = find_job_by_prefix(extract_job_prefix_from_backup(path))
            job_episode_key = ""
            if job:
                job_episode_key = build_episode_key(job.get("season", ""), job.get("episode", ""))
            if stored_episode_key and stored_episode_key != requested_episode_key:
                continue
            if job_episode_key and job_episode_key != requested_episode_key:
                continue
            if not stored_episode_key and not job_episode_key:
                continue
        else:
            job = find_job_by_prefix(extract_job_prefix_from_backup(path))

        if not score:
            continue

        stat = path.stat()
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

    if best is not None or imdb_slug:
        return best

    for path in fallback_paths:
        stem = path.stem
        score, matched_by, matched_value = score_text_match(
            stem,
            candidates,
            exact_base=220,
            contains_base=160,
            exact_label="release_exact_fallback",
            contains_label="release_contains_fallback",
        )
        if not score:
            continue

        job = find_job_by_prefix(extract_job_prefix_from_backup(path))
        if requested_episode_key:
            stored_episode_key = extract_episode_key(stem)
            job_episode_key = ""
            if job:
                job_episode_key = build_episode_key(job.get("season", ""), job.get("episode", ""))
            if stored_episode_key and stored_episode_key != requested_episode_key:
                continue
            if job_episode_key and job_episode_key != requested_episode_key:
                continue
            if not stored_episode_key and not job_episode_key:
                continue

        stat = path.stat()
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
        "tvshow_title",
        "season",
        "episode",
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


LANGUAGE_ALIASES = {
    "ces": "cs",
    "cze": "cs",
    "cz": "cs",
    "cs": "cs",
    "slk": "sk",
    "slo": "sk",
    "sk": "sk",
    "uk": "uk",
    "ukr": "uk",
}
LANGUAGE_LABELS = {"cs": "Czech", "sk": "Slovak", "uk": "Ukrainian"}


def normalize_subtitle_language(language: object) -> str:
    return LANGUAGE_ALIASES.get(str(language or "").strip().lower(), "")


def embedded_job_dir(job_id: str) -> Path:
    return EMBEDDED_JOBS_DIR / job_id


def embedded_job_meta_path(job_id: str) -> Path:
    return embedded_job_dir(job_id) / "job.json"


def read_embedded_job(job_id: str) -> dict[str, object] | None:
    path = embedded_job_meta_path(job_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_embedded_job(job: dict[str, object]) -> None:
    path = embedded_job_meta_path(str(job["job_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job, indent=2, sort_keys=True), encoding="utf-8")


def update_embedded_job(job_id: str, **changes) -> dict[str, object]:
    with EMBEDDED_LOCK:
        job = read_embedded_job(job_id)
        if job is None:
            raise KeyError(job_id)
        job.update(changes)
        job["updated_at"] = now_iso()
        write_embedded_job(job)
        return job


def embedded_job_response(job: dict[str, object]) -> dict[str, object]:
    allowed = {
        "job_id",
        "status",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "error",
        "returncode",
        "imdb_id",
        "release_name",
        "tvshow_title",
        "season",
        "episode",
        "source_language",
        "ukrainian_language",
        "chunk_seconds",
        "duration_seconds",
        "latest_ready_seconds",
        "latest_version",
        "full_ready",
        "chunks",
        "selected_streams",
    }
    return {key: value for key, value in job.items() if key in allowed and value not in (None, "")}


def safe_run_text(command: list[str], timeout=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(APP_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )


def process_error_text(text: str, media_url: str = "") -> str:
    cleaned = str(text or "")
    if media_url:
        cleaned = cleaned.replace(media_url, "<media-url>")
    if len(cleaned) > 1600:
        cleaned = cleaned[-1600:]
    return cleaned.strip()


def subtitle_track_rank(track: dict[str, object]) -> tuple[int, int, int]:
    title = str(track.get("title") or "").lower()
    forced = bool(track.get("forced")) or "forced" in title
    impaired = bool(track.get("impaired")) or "sdh" in title or "impaired" in title
    try:
        index = int(track.get("index") or 0)
    except Exception:
        index = 0
    return (1 if forced else 0, 1 if impaired else 0, index)


def select_subtitle_stream(streams: list[dict[str, object]], language: str) -> dict[str, object]:
    candidates = [stream for stream in streams if stream.get("language") == language]
    if not candidates:
        label = LANGUAGE_LABELS.get(language, language.upper())
        raise RuntimeError("No %s embedded subtitle stream found." % label)
    candidates.sort(key=subtitle_track_rank)
    selected = candidates[0]
    if selected.get("index") in (None, ""):
        label = LANGUAGE_LABELS.get(language, language.upper())
        raise RuntimeError("%s embedded subtitle stream has no stream index." % label)
    return selected


def probe_media_for_subtitles(media_url: str) -> tuple[list[dict[str, object]], int]:
    command = [
        os.getenv("HELPER_FFPROBE", "ffprobe"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name:stream_tags=language,title:stream_disposition=forced,hearing_impaired",
        "-of",
        "json",
        media_url,
    ]
    try:
        result = safe_run_text(command, timeout=180)
    except FileNotFoundError:
        raise RuntimeError("ffprobe was not found on helper.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffprobe timed out while reading media subtitles.")
    if result.returncode != 0:
        raise RuntimeError("ffprobe failed: %s" % process_error_text(result.stderr, media_url))
    try:
        parsed = json.loads(result.stdout or "{}")
    except Exception as exc:
        raise RuntimeError("ffprobe returned invalid JSON: %s" % exc)

    streams: list[dict[str, object]] = []
    for stream in parsed.get("streams") or []:
        if stream.get("codec_type") != "subtitle":
            continue
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        language = normalize_subtitle_language(tags.get("language"))
        if language not in {"cs", "sk", "uk"}:
            continue
        streams.append(
            {
                "index": stream.get("index"),
                "language": language,
                "raw_language": str(tags.get("language") or "").strip(),
                "title": str(tags.get("title") or "").strip(),
                "codec": str(stream.get("codec_name") or "").strip(),
                "forced": bool(disposition.get("forced")),
                "impaired": bool(disposition.get("hearing_impaired")),
            }
        )

    duration_seconds = 0
    try:
        duration_seconds = int(float((parsed.get("format") or {}).get("duration") or 0))
    except Exception:
        duration_seconds = 0
    return streams, duration_seconds


def _parse_srt_timestamp(value: str) -> int:
    text = str(value or "").strip().replace(",", ".")
    parts = text.split(":")
    if len(parts) != 3:
        return 0
    hours, minutes, seconds = parts
    second_parts = seconds.split(".")
    whole_seconds = second_parts[0]
    milliseconds = (second_parts[1] if len(second_parts) > 1 else "0").ljust(3, "0")[:3]
    return int(hours) * 3600000 + int(minutes) * 60000 + int(whole_seconds) * 1000 + int(milliseconds)


def _is_srt_timestamp(line: str) -> bool:
    return "-->" in str(line or "")


def parse_srt_events(path: Path) -> list[tuple[int, int, str]]:
    content = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = content.split("\n")
    events: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not _is_srt_timestamp(line):
            index += 1
            continue
        start_raw, end_raw = line.split("-->", 1)
        start_ms = _parse_srt_timestamp(start_raw)
        end_ms = _parse_srt_timestamp(end_raw)
        index += 1
        text_lines = []
        while index < len(lines):
            text_line = lines[index]
            if not text_line.strip():
                break
            if _is_srt_timestamp(text_line):
                break
            if text_line.strip().isdigit() and index + 1 < len(lines) and _is_srt_timestamp(lines[index + 1]):
                break
            text_lines.append(text_line.strip())
            index += 1
        if text_lines:
            events.append((start_ms, end_ms, "\n".join(text_lines)))
    return events


def ms_to_ass_timestamp(ms: int) -> str:
    ms = max(0, int(ms))
    hours = ms // 3600000
    ms %= 3600000
    minutes = ms // 60000
    ms %= 60000
    seconds = ms // 1000
    centiseconds = (ms % 1000) // 10
    return "%d:%02d:%02d.%02d" % (hours, minutes, seconds, centiseconds)


def strip_subtitle_tags(text: str) -> str:
    text = re.sub(r"\\[Nn]", "\n", str(text or ""))
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def escape_ass_text(text: str) -> str:
    return str(text or "").replace("\r", "").replace("\n", "\\N")


DUAL_ASS_HEADER = """\
[Script Info]
Title: TitDeepL Progressive Dual Subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: top-style,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,8,20,20,25,1
Style: bottom-style,Arial,54,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,20,20,25,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_dual_ass(source_srt: Path, ukrainian_srt: Path, output_path: Path) -> int:
    source_events = parse_srt_events(source_srt)
    ukrainian_events = parse_srt_events(ukrainian_srt)
    if not source_events and not ukrainian_events:
        raise RuntimeError("Extracted subtitle files are empty or unparseable.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    top_ctag = "{\\1c&HFFFFFF&}"
    bottom_ctag = "{\\1c&H00FFFF&}"
    lines = [DUAL_ASS_HEADER]
    ass_events = []
    for start_ms, end_ms, text in source_events:
        ass_events.append(
            (
                start_ms,
                "Dialogue: 0,%s,%s,top-style,,0,0,0,,%s%s\n"
                % (ms_to_ass_timestamp(start_ms), ms_to_ass_timestamp(end_ms), top_ctag, escape_ass_text(strip_subtitle_tags(text))),
            )
        )
    for start_ms, end_ms, text in ukrainian_events:
        ass_events.append(
            (
                start_ms,
                "Dialogue: 0,%s,%s,bottom-style,,0,0,0,,%s%s\n"
                % (ms_to_ass_timestamp(start_ms), ms_to_ass_timestamp(end_ms), bottom_ctag, escape_ass_text(strip_subtitle_tags(text))),
            )
        )
    ass_events.sort(key=lambda event: event[0])
    lines.extend(line for _, line in ass_events)
    output_path.write_text("".join(lines), encoding="utf-8", newline="")
    return len(source_events) + len(ukrainian_events)


def extract_dual_srt_chunk(
    media_url: str,
    source_stream: dict[str, object],
    ukrainian_stream: dict[str, object],
    seconds: int,
    source_path: Path,
    ukrainian_path: Path,
) -> None:
    command = [
        os.getenv("HELPER_FFMPEG", "ffmpeg"),
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-t",
        str(seconds),
        "-i",
        media_url,
        "-map",
        "0:%s" % source_stream.get("index"),
        "-c:s",
        "srt",
        str(source_path),
        "-map",
        "0:%s" % ukrainian_stream.get("index"),
        "-c:s",
        "srt",
        str(ukrainian_path),
    ]
    try:
        result = safe_run_text(command, timeout=max(240, seconds * 3))
    except FileNotFoundError:
        raise RuntimeError("ffmpeg was not found on helper.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg timed out while extracting first %s seconds." % seconds)

    missing = [str(path.name) for path in (source_path, ukrainian_path) if not path.is_file()]
    if missing:
        raise RuntimeError(
            "ffmpeg did not create subtitle output (%s): %s"
            % (", ".join(missing), process_error_text(result.stderr, media_url))
        )
    if result.returncode != 0:
        # Some remote streams close noisily after enough subtitle data was produced. Keep valid output.
        print("ffmpeg warning for embedded dual chunk %s: %s" % (seconds, process_error_text(result.stderr, media_url)), file=sys.stderr)


def embedded_chunk_seconds(job: dict[str, object]) -> list[int]:
    chunk_seconds = clean_positive_int(job.get("chunk_seconds"), 300, minimum=60, maximum=1800)
    duration_seconds = clean_positive_int(job.get("duration_seconds"), 0, minimum=0, maximum=36000)
    max_seconds = clean_positive_int(job.get("max_seconds"), duration_seconds or 7200, minimum=chunk_seconds, maximum=36000)
    if duration_seconds:
        max_seconds = min(max_seconds, duration_seconds + 30)

    chunks = []
    current = chunk_seconds
    while current < max_seconds:
        chunks.append(current)
        current += chunk_seconds
    if max_seconds not in chunks:
        chunks.append(max_seconds)
    return chunks


def process_embedded_dual_job(job_id: str) -> None:
    job = update_embedded_job(job_id, status="running", started_at=now_iso(), error="")
    media_url = str(job.get("media_url") or "").strip()
    if not media_url:
        raise RuntimeError("media_url is required.")

    working_dir = embedded_job_dir(job_id)
    chunks_dir = working_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    log_path = working_dir / "job.log"

    def log_line(message: str) -> None:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("[%s] %s\n" % (now_iso(), message))

    log_line("probing media")
    streams, duration_seconds = probe_media_for_subtitles(media_url)
    source_language = normalize_subtitle_language(job.get("source_language")) or "sk"
    ukrainian_language = normalize_subtitle_language(job.get("ukrainian_language")) or "uk"
    source_stream = select_subtitle_stream(streams, source_language)
    ukrainian_stream = select_subtitle_stream(streams, ukrainian_language)
    job = update_embedded_job(
        job_id,
        duration_seconds=duration_seconds,
        selected_streams={"source": source_stream, "ukrainian": ukrainian_stream},
        chunks=[],
    )

    chunks: list[dict[str, object]] = []
    chunk_plan = embedded_chunk_seconds(job)
    for seconds in chunk_plan:
        source_srt = chunks_dir / ("source_%06d.srt" % seconds)
        ukrainian_srt = chunks_dir / ("ukrainian_%06d.srt" % seconds)
        dual_ass = chunks_dir / ("dual_%06d.ass" % seconds)
        log_line("extracting first %s seconds" % seconds)
        extract_dual_srt_chunk(media_url, source_stream, ukrainian_stream, seconds, source_srt, ukrainian_srt)
        try:
            event_count = write_dual_ass(source_srt, ukrainian_srt, dual_ass)
        except RuntimeError as exc:
            if "empty or unparseable" in str(exc) and seconds != chunk_plan[-1]:
                log_line("no readable subtitle cues by %s seconds yet, trying next chunk" % seconds)
                continue
            raise
        chunk = {
            "seconds": seconds,
            "version": str(seconds),
            "path": str(dual_ass),
            "bytes": dual_ass.stat().st_size,
            "events": event_count,
            "created_at": now_iso(),
        }
        chunks.append(chunk)
        full_ready = bool(duration_seconds and seconds >= duration_seconds)
        update_embedded_job(
            job_id,
            chunks=chunks,
            latest_ready_seconds=seconds,
            latest_version=str(seconds),
            latest_subtitle_path=str(dual_ass),
            full_ready=full_ready,
        )
        if full_ready:
            break

    if not chunks:
        raise RuntimeError("No readable subtitle cues were extracted from the selected embedded streams.")

    update_embedded_job(job_id, status="finished", finished_at=now_iso(), returncode=0, full_ready=True)


def embedded_worker_loop() -> None:
    while True:
        job_id = EMBEDDED_QUEUE.get()
        try:
            process_embedded_dual_job(job_id)
        except Exception as exc:  # pragma: no cover - background error path
            try:
                update_embedded_job(job_id, status="failed", finished_at=now_iso(), error=str(exc), returncode=1)
            except Exception:
                pass
        finally:
            EMBEDDED_QUEUE.task_done()


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


def device_log_dir(log_id: str) -> Path:
    return DEVICE_LOGS_DIR / log_id


def read_device_log_metadata(log_id: str) -> dict[str, object] | None:
    path = device_log_dir(log_id) / "metadata.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def safe_log_metadata(payload: dict[str, object], saved_files: list[dict[str, object]], log_id: str) -> dict[str, object]:
    return {
        "log_id": log_id,
        "received_at": now_iso(),
        "device_name": str(payload.get("device_name") or "").strip(),
        "device_label": str(payload.get("device_label") or "").strip(),
        "platform": str(payload.get("platform") or "").strip(),
        "kodi_version": str(payload.get("kodi_version") or "").strip(),
        "build_version": str(payload.get("build_version") or "").strip(),
        "addon_version": str(payload.get("addon_version") or "").strip(),
        "notes": str(payload.get("notes") or "").strip()[:1000],
        "saved_files": saved_files,
    }


@app.post("/api/device-logs")
def receive_device_logs():
    auth = require_auth()
    if auth is not None:
        return auth

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected a JSON object."}), 400

    logs = payload.get("logs")
    if not isinstance(logs, list) or not logs:
        return jsonify({"error": "logs must be a non-empty list."}), 400

    device_name = str(payload.get("device_name") or payload.get("device_label") or "device").strip()
    log_id = "%s_%s_%s" % (time.strftime("%Y%m%d-%H%M%S", time.gmtime()), slugify(device_name)[:48], uuid.uuid4().hex[:8])
    target_dir = device_log_dir(log_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    max_total_bytes = clean_positive_int(os.getenv("HELPER_MAX_DEVICE_LOG_BYTES"), 10 * 1024 * 1024, minimum=1024, maximum=50 * 1024 * 1024)
    total_bytes = 0
    saved_files: list[dict[str, object]] = []
    used_names: set[str] = set()

    for index, entry in enumerate(logs[:12], start=1):
        if not isinstance(entry, dict):
            continue
        filename = sanitize_filename(str(entry.get("filename") or "kodi.log"))
        if filename in used_names:
            stem, ext = os.path.splitext(filename)
            filename = "%s_%02d%s" % (stem, index, ext)
        used_names.add(filename)

        content_b64 = str(entry.get("content_b64") or "").strip()
        if not content_b64:
            continue
        try:
            content = base64.b64decode(content_b64.encode("ascii"), validate=True)
        except Exception:
            return jsonify({"error": "Invalid base64 content for %s." % filename}), 400

        total_bytes += len(content)
        if total_bytes > max_total_bytes:
            return jsonify({"error": "Uploaded logs are too large."}), 413

        output_path = target_dir / filename
        output_path.write_bytes(content)
        saved_files.append(
            {
                "filename": filename,
                "bytes": len(content),
                "truncated": bool(entry.get("truncated", False)),
                "source_path": str(entry.get("source_path") or "").strip(),
            }
        )

    if not saved_files:
        return jsonify({"error": "No readable log files were included."}), 400

    metadata = safe_log_metadata(payload, saved_files, log_id)
    (target_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return jsonify({"ok": True, "log_id": log_id, "stored_dir": str(target_dir), "saved_files": saved_files}), 201


@app.get("/api/device-logs")
def list_device_logs():
    auth = require_auth()
    if auth is not None:
        return auth

    items = []
    for path in sorted(DEVICE_LOGS_DIR.iterdir(), key=lambda item: item.name, reverse=True):
        if not path.is_dir():
            continue
        metadata = read_device_log_metadata(path.name)
        if metadata:
            items.append(metadata)
        if len(items) >= 50:
            break
    return jsonify({"items": items})


@app.get("/api/device-logs/<log_id>")
def get_device_log(log_id: str):
    auth = require_auth()
    if auth is not None:
        return auth

    metadata = read_device_log_metadata(log_id)
    if metadata is None:
        return jsonify({"error": "Log bundle not found."}), 404
    return jsonify(metadata)


@app.get("/api/stored-subtitles/lookup")
def lookup_stored_subtitle():
    auth = require_auth()
    if auth is not None:
        return auth

    imdb_id = str(request.args.get("imdb_id") or "").strip()
    release_name = str(request.args.get("release_name") or "").strip()
    source_filename = str(request.args.get("source_filename") or "").strip()
    title = str(request.args.get("title") or "").strip()
    year = str(request.args.get("year") or "").strip()
    tvshow_title = str(request.args.get("tvshow_title") or "").strip()
    season = str(request.args.get("season") or "").strip()
    episode = str(request.args.get("episode") or "").strip()
    language = str(request.args.get("language") or "uk").strip() or "uk"
    lookup_summary = build_lookup_summary(
        imdb_id=imdb_id,
        release_name=release_name,
        source_filename=source_filename,
        title=title,
        year=year,
        tvshow_title=tvshow_title,
        season=season,
        episode=episode,
    )
    if not imdb_id and not any((release_name, source_filename, title, tvshow_title)):
        return jsonify({"found": False, "reason": "no_lookup_keys", "lookup_summary": lookup_summary})

    match = match_saved_subtitle(
        imdb_id,
        release_name,
        source_filename,
        title=title,
        year=year,
        tvshow_title=tvshow_title,
        season=season,
        episode=episode,
        language=language,
    )
    if match is None:
        return jsonify({"found": False, "reason": "no_match", "lookup_summary": lookup_summary})

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
        "lookup_summary": lookup_summary,
        "job_id": job.get("job_id", "") if job else "",
        "job_status": job.get("status", "") if job else "",
        "job_returncode": job.get("returncode", "") if job else "",
    }
    return jsonify(response)


@app.post("/api/embedded-dual-jobs")
def create_embedded_dual_job():
    auth = require_auth()
    if auth is not None:
        return auth

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected a JSON object."}), 400

    media_url = str(payload.get("media_url") or "").strip()
    if not media_url:
        return jsonify({"error": "media_url is required."}), 400

    source_language = normalize_subtitle_language(payload.get("source_language")) or "sk"
    ukrainian_language = normalize_subtitle_language(payload.get("ukrainian_language")) or "uk"
    if source_language not in {"cs", "sk"}:
        return jsonify({"error": "source_language must be cs or sk."}), 400
    if ukrainian_language != "uk":
        return jsonify({"error": "ukrainian_language must be uk."}), 400

    job_id = uuid.uuid4().hex
    working_dir = embedded_job_dir(job_id)
    working_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": "",
        "finished_at": "",
        "error": "",
        "returncode": None,
        "media_url": media_url,
        "source_language": source_language,
        "ukrainian_language": ukrainian_language,
        "chunk_seconds": clean_positive_int(payload.get("chunk_seconds"), 300, minimum=60, maximum=1800),
        "max_seconds": clean_positive_int(payload.get("max_seconds"), 7200, minimum=300, maximum=36000),
        "duration_seconds": 0,
        "latest_ready_seconds": 0,
        "latest_version": "",
        "latest_subtitle_path": "",
        "full_ready": False,
        "chunks": [],
        "selected_streams": {},
        "imdb_id": str(payload.get("imdb_id") or "").strip(),
        "release_name": str(payload.get("release_name") or "").strip(),
        "tvshow_title": str(payload.get("tvshow_title") or "").strip(),
        "season": clean_episode_number(payload.get("season")),
        "episode": clean_episode_number(payload.get("episode")),
        "title": str(payload.get("title") or "").strip(),
        "year": str(payload.get("year") or "").strip(),
        "log_path": str(working_dir / "job.log"),
    }
    write_embedded_job(job)
    EMBEDDED_QUEUE.put(job_id)
    return jsonify(embedded_job_response(job)), 202


@app.get("/api/embedded-dual-jobs/<job_id>")
def get_embedded_dual_job(job_id: str):
    auth = require_auth()
    if auth is not None:
        return auth

    job = read_embedded_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(embedded_job_response(job))


@app.get("/api/embedded-dual-jobs/<job_id>/subtitle")
def get_embedded_dual_subtitle(job_id: str):
    auth = require_auth()
    if auth is not None:
        return auth

    job = read_embedded_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found."}), 404

    version = str(request.args.get("version") or "latest").strip()
    path = ""
    if version == "latest":
        path = str(job.get("latest_subtitle_path") or "")
    else:
        for chunk in job.get("chunks") or []:
            if str(chunk.get("version") or "") == version or str(chunk.get("seconds") or "") == version:
                path = str(chunk.get("path") or "")
                break

    if not path:
        return jsonify({"error": "Subtitle chunk is not ready."}), 404

    subtitle_path = Path(path)
    try:
        subtitle_path.resolve().relative_to(embedded_job_dir(job_id).resolve())
    except Exception:
        return jsonify({"error": "Invalid subtitle path."}), 500
    if not subtitle_path.is_file():
        return jsonify({"error": "Subtitle file is missing."}), 404

    return send_file(str(subtitle_path), mimetype="text/plain; charset=utf-8", as_attachment=False)


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
    tvshow_title = str(payload.get("tvshow_title") or "").strip()
    season = clean_episode_number(payload.get("season"))
    episode = clean_episode_number(payload.get("episode"))
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
    backup_subtitle_path = build_backup_subtitle_path(
        job_id,
        imdb_id,
        release_name,
        subtitle_filename,
        season=season,
        episode=episode,
    )
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
        "tvshow_title": tvshow_title,
        "season": season,
        "episode": episode,
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
    embedded_worker = threading.Thread(target=embedded_worker_loop, name="embedded-dual-worker", daemon=True)
    embedded_worker.start()

    host = os.getenv("HELPER_HOST", "0.0.0.0")
    port = int(os.getenv("HELPER_PORT", "8097"))
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

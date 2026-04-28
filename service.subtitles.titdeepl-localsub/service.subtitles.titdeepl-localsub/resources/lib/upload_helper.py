# -*- coding: utf-8 -*-
"""HTTP client for the remote OpenSubtitles upload helper."""

import base64
import json
import os

try:
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    from urllib import urlencode
    from urllib2 import HTTPError, URLError, Request, urlopen


class HelperUploadError(Exception):
    pass


def _normalize_helper_url(helper_url):
    normalized = (helper_url or "").strip().rstrip("/")
    if not normalized:
        raise HelperUploadError("Remote helper URL is empty.")
    if not normalized.startswith(("http://", "https://")):
        normalized = "http://" + normalized
    return normalized


def _helper_headers(helper_token="", content_type="application/json"):
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    if helper_token:
        headers["Authorization"] = "Bearer %s" % helper_token.strip()
    return headers


def _request_helper_json(request, timeout):
    try:
        response = urlopen(request, timeout=timeout)
        body = response.read().decode("utf-8")
    except HTTPError as exc:
        try:
            details = exc.read().decode("utf-8")
        except Exception:
            details = str(exc)
        raise HelperUploadError("Helper returned HTTP %s: %s" % (exc.code, details))
    except URLError as exc:
        raise HelperUploadError("Could not reach helper: %s" % exc)
    except Exception as exc:
        raise HelperUploadError("Unexpected helper error: %s" % exc)

    try:
        parsed = json.loads(body)
    except Exception as exc:
        raise HelperUploadError("Helper returned invalid JSON: %s" % exc)

    if not isinstance(parsed, dict):
        raise HelperUploadError("Helper returned an unexpected response.")
    return parsed


def download_helper_cached_subtitle(
    helper_url,
    helper_token,
    output_dir,
    imdb_id="",
    release_name="",
    source_filename="",
    title="",
    year="",
    tvshow_title="",
    season="",
    episode="",
    timeout=15,
):
    helper_base = _normalize_helper_url(helper_url)
    query = urlencode(
        {
            "imdb_id": (imdb_id or "").strip(),
            "release_name": release_name or "",
            "source_filename": source_filename or "",
            "title": title or "",
            "year": year or "",
            "tvshow_title": tvshow_title or "",
            "season": season or "",
            "episode": episode or "",
            "language": "uk",
        }
    )
    request = Request(
        helper_base + "/api/stored-subtitles/lookup?" + query,
        headers=_helper_headers(helper_token, content_type=None),
    )
    parsed = _request_helper_json(request, timeout)
    if not parsed.get("found"):
        return parsed

    subtitle_content_b64 = str(parsed.get("subtitle_content_b64") or "").strip()
    if not subtitle_content_b64:
        raise HelperUploadError("Helper found a cached subtitle but did not return its content.")

    subtitle_filename = os.path.basename(str(parsed.get("subtitle_filename") or "helper_cached_uk.srt"))
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, subtitle_filename)
    try:
        subtitle_bytes = base64.b64decode(subtitle_content_b64.encode("ascii"), validate=True)
    except Exception as exc:
        raise HelperUploadError("Helper returned invalid cached subtitle data: %s" % exc)

    with open(output_path, "wb") as handle:
        handle.write(subtitle_bytes)

    parsed["path"] = output_path
    return parsed


def queue_helper_upload(
    helper_url,
    helper_token,
    subtitle_path,
    language="uk",
    imdb_id="",
    fps="25.000",
    release_name="",
    tvshow_title="",
    season="",
    episode="",
    username="",
    password="",
    machine_translated=True,
    auto_submit=False,
    comment="",
    timeout=30,
):
    if not os.path.isfile(subtitle_path):
        raise HelperUploadError("Subtitle file not found: %s" % subtitle_path)

    helper_base = _normalize_helper_url(helper_url)
    with open(subtitle_path, "rb") as handle:
        subtitle_content = base64.b64encode(handle.read()).decode("ascii")

    payload = {
        "subtitle_filename": os.path.basename(subtitle_path),
        "subtitle_content_b64": subtitle_content,
        "language": language,
        "imdb_id": imdb_id or "",
        "fps": fps,
        "release_name": release_name or os.path.basename(subtitle_path),
        "tvshow_title": tvshow_title or "",
        "season": season or "",
        "episode": episode or "",
        "username": username or "",
        "password": password or "",
        "machine_translated": bool(machine_translated),
        "auto_submit": bool(auto_submit),
        "comment": comment or "",
    }

    request = Request(
        helper_base + "/api/upload-jobs",
        data=json.dumps(payload).encode("utf-8"),
        headers=_helper_headers(helper_token),
    )
    return _request_helper_json(request, timeout)


def queue_helper_embedded_dual_job(
    helper_url,
    helper_token,
    media_url,
    source_language="sk",
    ukrainian_language="uk",
    imdb_id="",
    release_name="",
    tvshow_title="",
    season="",
    episode="",
    title="",
    year="",
    start_seconds=0,
    chunk_seconds=300,
    first_chunk_seconds=60,
    max_seconds=7200,
    timeout=30,
):
    helper_base = _normalize_helper_url(helper_url)
    payload = {
        "media_url": media_url or "",
        "source_language": source_language or "sk",
        "ukrainian_language": ukrainian_language or "uk",
        "imdb_id": imdb_id or "",
        "release_name": release_name or "",
        "tvshow_title": tvshow_title or "",
        "season": season or "",
        "episode": episode or "",
        "title": title or "",
        "year": year or "",
        "start_seconds": int(start_seconds or 0),
        "chunk_seconds": int(chunk_seconds or 300),
        "first_chunk_seconds": int(first_chunk_seconds or 60),
        "max_seconds": int(max_seconds or 7200),
    }
    request = Request(
        helper_base + "/api/embedded-dual-jobs",
        data=json.dumps(payload).encode("utf-8"),
        headers=_helper_headers(helper_token),
    )
    return _request_helper_json(request, timeout)


def get_helper_embedded_dual_job(helper_url, helper_token, job_id, timeout=15):
    helper_base = _normalize_helper_url(helper_url)
    request = Request(
        helper_base + "/api/embedded-dual-jobs/%s" % job_id,
        headers=_helper_headers(helper_token, content_type=None),
    )
    return _request_helper_json(request, timeout)


def download_helper_embedded_dual_subtitle(
    helper_url,
    helper_token,
    job_id,
    output_dir,
    version="latest",
    timeout=30,
):
    helper_base = _normalize_helper_url(helper_url)
    url = "%s/api/embedded-dual-jobs/%s/subtitle?%s" % (
        helper_base,
        job_id,
        urlencode({"version": version or "latest"}),
    )
    request = Request(url, headers=_helper_headers(helper_token, content_type=None))
    try:
        response = urlopen(request, timeout=timeout)
        content = response.read()
    except HTTPError as exc:
        try:
            details = exc.read().decode("utf-8")
        except Exception:
            details = str(exc)
        raise HelperUploadError("Helper returned HTTP %s: %s" % (exc.code, details))
    except URLError as exc:
        raise HelperUploadError("Could not reach helper: %s" % exc)
    except Exception as exc:
        raise HelperUploadError("Unexpected helper error: %s" % exc)

    os.makedirs(output_dir, exist_ok=True)
    safe_job_id = "".join(char for char in str(job_id) if char.isalnum())[:32] or "embedded"
    safe_version = "".join(char for char in str(version or "latest") if char.isalnum() or char in ("-", "_"))[:24] or "latest"
    output_path = os.path.join(output_dir, "embedded_dual_%s_%s.ass" % (safe_job_id[:8], safe_version))
    with open(output_path, "wb") as handle:
        handle.write(content)
    return output_path

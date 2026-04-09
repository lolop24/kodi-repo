# -*- coding: utf-8 -*-
"""Check and download Ukrainian subtitles from OpenSubtitles.com."""

import json
import os
import re

try:
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
except ImportError:
    from urllib2 import HTTPError, URLError, Request, urlopen
    from urllib import urlencode

API_URL = "https://api.opensubtitles.com/api/v1/"
API_KEY = "qo2wQs1PXwIHJsXvIiWXu1ZbVjaboPh6"
USER_AGENT = "TitulkyDeepLSub v0.2.0"
TIMEOUT = 10
DOWNLOAD_TIMEOUT = 30
RELEASE_STOP_TOKENS = {
    "1080p", "2160p", "720p", "480p", "576p",
    "webrip", "web", "webdl", "web-dl", "bluray", "brrip", "hdrip", "dvdrip", "remux",
    "x264", "x265", "h264", "h265", "hevc", "av1",
    "aac", "aac2", "aac5", "dd", "ddp", "ddp5", "ddp5.1", "ac3", "dts",
    "yts", "ytsmx", "ytslt", "amzn", "nf", "dsnp", "hmax", "proper", "repack",
}
LANGUAGE_HINT_TOKENS = {"cs", "cz", "sk", "uk", "ua", "eng", "en", "subs", "subtitles"}


def _api_get(endpoint, params=None):
    """GET request to OpenSubtitles REST API."""
    url = API_URL + endpoint
    if params:
        filtered = {k: v for k, v in params.items() if v}
        url = "%s?%s" % (url, urlencode(filtered))

    request = Request(url)
    request.add_header("Api-Key", API_KEY)
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")

    try:
        response = urlopen(request, timeout=TIMEOUT)
        return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, ValueError):
        return None


def _api_post(endpoint, body, auth_token=None):
    """POST request to OpenSubtitles REST API."""
    url = API_URL + endpoint
    data = json.dumps(body).encode("utf-8")

    request = Request(url, data=data)
    request.add_header("Api-Key", API_KEY)
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    if auth_token:
        request.add_header("Authorization", "Bearer %s" % auth_token)

    try:
        response = urlopen(request, timeout=DOWNLOAD_TIMEOUT)
        return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
            return json.loads(err_body)
        except Exception:
            return None
    except (URLError, ValueError):
        return None


def check_ukrainian_subtitles(imdb_id="", title="", year="", season="", episode=""):
    """Search OpenSubtitles for existing Ukrainian subtitles.

    Returns list of dicts with subtitle info. Empty list if none found.
    """
    results = []

    # Strategy 1: Search by IMDB ID (most precise)
    if imdb_id:
        clean_imdb = imdb_id.replace("tt", "")
        data = _api_get("subtitles", {
            "imdb_id": clean_imdb,
            "languages": "uk",
            "type": "movie" if not season else "episode",
            "season_number": season,
            "episode_number": episode,
        })
        if data and data.get("data"):
            results = data["data"]

    # Strategy 2: Search by title + year (fallback)
    if not results and title:
        data = _api_get("subtitles", {
            "query": title,
            "languages": "uk",
            "year": year,
            "season_number": season,
            "episode_number": episode,
        })
        if data and data.get("data"):
            results = data["data"]

    # Parse results into simplified format
    found = []
    for item in results:
        attrs = item.get("attributes", {})
        files = attrs.get("files", [])
        uploader = attrs.get("uploader", {})

        for f in files:
            found.append({
                "subtitle_id": item.get("id", ""),
                "file_id": f.get("file_id", ""),
                "file_name": f.get("file_name", ""),
                "release": attrs.get("release", ""),
                "uploader_name": uploader.get("name", "anonymous"),
                "uploader_id": uploader.get("uploader_id", ""),
                "downloads": attrs.get("download_count", 0),
                "rating": attrs.get("ratings", 0),
                "machine_translated": attrs.get("machine_translated", False),
                "ai_translated": attrs.get("ai_translated", False),
                "language": attrs.get("language", "uk"),
            })

    return found


def os_login(username, password):
    """Login to OpenSubtitles and return auth token."""
    result = _api_post("login", {"username": username, "password": password})
    if result and result.get("token"):
        return result["token"]
    return None


def download_subtitle(file_id, auth_token=None, output_dir=None):
    """Download a subtitle file from OpenSubtitles.

    Returns path to downloaded SRT file, or None on failure.
    """
    result = _api_post("download", {"file_id": int(file_id)}, auth_token=auth_token)
    if not result or not result.get("link"):
        return None

    download_url = result["link"]
    file_name = result.get("file_name", "subtitle.srt")

    # Download actual file content
    request = Request(download_url)
    request.add_header("User-Agent", USER_AGENT)
    try:
        response = urlopen(request, timeout=DOWNLOAD_TIMEOUT)
        content = response.read()
    except (HTTPError, URLError):
        return None

    if not content:
        return None

    # Save to output dir
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, file_name)
    else:
        output_path = file_name

    with open(output_path, "wb") as fh:
        fh.write(content)

    return output_path


def format_check_results(found):
    """Format found subtitles for display."""
    if not found:
        return "No Ukrainian subtitles found on OpenSubtitles."

    lines = ["Ukrainian subtitles already on OpenSubtitles:"]
    for i, item in enumerate(found[:5], 1):
        mt = " [MT]" if item.get("machine_translated") else ""
        ai = " [AI]" if item.get("ai_translated") else ""
        lines.append(
            "  %d. %s by %s (%d downloads)%s%s"
            % (
                i,
                item.get("release") or item.get("file_name") or "unknown",
                item.get("uploader_name", "anonymous"),
                item.get("downloads", 0),
                mt,
                ai,
            )
        )
    return "\n".join(lines)


def format_choice_list(found):
    """Format found subtitles as list items for Kodi select dialog."""
    items = []
    for item in found[:10]:
        mt = " [MT]" if item.get("machine_translated") else ""
        ai = " [AI]" if item.get("ai_translated") else ""
        label = "%s by %s (%d dl)%s%s" % (
            item.get("release") or item.get("file_name") or "unknown",
            item.get("uploader_name", "anonymous"),
            item.get("downloads", 0),
            mt,
            ai,
        )
        items.append(label)
    return items


def normalize_lookup_text(value):
    lowered = (value or "").lower()
    lowered = re.sub(r"[\[\(].*?[\]\)]", " ", lowered)
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def extract_title_candidate(value):
    stem = os.path.splitext(os.path.basename(value or ""))[0]
    stem = re.sub(r"[\[\(].*?[\]\)]", " ", stem)
    raw_tokens = [token for token in re.split(r"[.\-_\s]+", stem) if token]
    if not raw_tokens:
        return ""

    collected = []
    for token in raw_tokens:
        lower = token.lower()
        if re.match(r"^(19|20)\d{2}$", lower):
            break
        if lower in RELEASE_STOP_TOKENS:
            break
        if lower in LANGUAGE_HINT_TOKENS and collected:
            break
        collected.append(token)

    if not collected:
        collected = [token for token in raw_tokens if token.lower() not in RELEASE_STOP_TOKENS][:6]
    return " ".join(collected).strip()


def build_imdb_query_candidates(title="", original_title="", release_name="", filename=""):
    candidates = []
    seen = set()

    def add_candidate(value, source):
        normalized = " ".join((value or "").split())
        key = normalize_lookup_text(normalized)
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append({"value": normalized, "source": source})

    add_candidate(original_title, "original_title")
    add_candidate(title, "title")
    add_candidate(extract_title_candidate(release_name), "release_name")
    add_candidate(extract_title_candidate(filename), "filename")
    return candidates


def token_overlap_score(left, right):
    left_tokens = set(normalize_lookup_text(left).split())
    right_tokens = set(normalize_lookup_text(right).split())
    if not left_tokens or not right_tokens:
        return 0
    overlap = len(left_tokens & right_tokens)
    if not overlap:
        return 0
    return int((overlap * 100) / max(len(left_tokens), len(right_tokens)))


def resolve_imdb_id(imdb_id="", title="", original_title="", year="", release_name="", filename=""):
    if imdb_id:
        clean_imdb = str(imdb_id).replace("tt", "").strip()
        if clean_imdb.isdigit():
            return {
                "imdb_id": clean_imdb,
                "matched_by": "existing",
                "query": "",
                "feature_title": "",
                "release": "",
            }

    candidates = build_imdb_query_candidates(
        title=title,
        original_title=original_title,
        release_name=release_name,
        filename=filename,
    )
    if not candidates:
        return None

    best = None
    for query_candidate in candidates:
        query = query_candidate["value"]
        data = _api_get(
            "subtitles",
            {
                "query": query,
                "year": year,
                "type": "movie",
            },
        )
        for item in (data or {}).get("data") or []:
            attrs = item.get("attributes", {})
            feature = attrs.get("feature_details") or {}
            candidate_imdb = str(feature.get("imdb_id") or "").strip()
            if not candidate_imdb.isdigit():
                continue

            feature_title = feature.get("title") or feature.get("movie_name") or ""
            release = attrs.get("release") or ""
            score = 0
            matched_by = ""
            for reference_candidate in candidates:
                reference = reference_candidate["value"]
                reference_source = reference_candidate["source"]
                left = normalize_lookup_text(reference)
                right = normalize_lookup_text(feature_title)
                if left and right and left == right:
                    score = max(score, 120)
                    matched_by = "%s:title_exact" % reference_source
                else:
                    overlap = token_overlap_score(reference, feature_title)
                    if overlap >= 90:
                        score = max(score, 90)
                        matched_by = "%s:title_overlap" % reference_source
                    elif reference_source in ("release_name", "filename") and release and token_overlap_score(reference, release) >= 92:
                        score = max(score, 82)
                        matched_by = "%s:release_overlap" % reference_source

            feature_year = str(feature.get("year") or "").strip()
            if year and feature_year == str(year).strip():
                score += 5

            if score < 90:
                continue

            if best is None or score > int(best["score"]):
                best = {
                    "imdb_id": candidate_imdb,
                    "matched_by": matched_by or "query_match",
                    "query": query,
                    "feature_title": feature_title,
                    "release": release,
                    "score": score,
                }

    if best is None:
        return None
    best.pop("score", None)
    return best

# -*- coding: utf-8 -*-
"""Check and download Ukrainian subtitles from OpenSubtitles.com."""

import json
import os

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

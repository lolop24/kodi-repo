# -*- coding: utf-8 -*-
"""TitDeepL LocalSub: translate CZ/SK subtitles and queue remote uploads."""

import hashlib
import json
import os
import shutil
import sys
import time
import uuid
import zipfile

from urllib.parse import parse_qsl, quote, unquote

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib.deepl import DeepLError, DeepLTranslator
from resources.lib.generator import (
    SUPPORTED_EXTENSIONS,
    generate_translated_subtitle,
    guess_language_from_name,
    merge_two_subtitles_to_dual_ass,
    safe_stem,
)
from resources.lib.os_check import (
    check_ukrainian_subtitles,
    download_subtitle,
    format_choice_list,
    os_login,
    resolve_imdb_id,
)
from resources.lib.os_uploader import UploadError, extract_srt_from_ass
from resources.lib.settings_migrator import migrate_settings
from resources.lib.source_matching import build_source_score, normalize_name
from resources.lib.upload_helper import (
    HelperUploadError,
    download_helper_cached_subtitle,
    queue_helper_upload,
)


try:
    translatePath = xbmcvfs.translatePath
except AttributeError:
    translatePath = xbmc.translatePath


migrate_settings()
__addon__ = xbmcaddon.Addon()
__scriptid__ = __addon__.getAddonInfo("id")
__scriptname__ = __addon__.getAddonInfo("name")
__profile__ = translatePath(__addon__.getAddonInfo("profile"))
__workdir__ = os.path.join(__profile__, "generated")
__dialog__ = xbmcgui.Dialog()
try:
    __settings__ = __addon__.getSettings()
except AttributeError:
    __settings__ = None
KNOWN_SUBTITLE_ADDON_TEMP_DIRS = (
    ("TitulkyDualSub", "special://temp/tds/"),
    ("TitulkyDualSub", "special://profile/addon_data/service.subtitles.titulky-dualsub/temp"),
    ("OpenSubtitles", "special://profile/addon_data/service.subtitles.opensubtitles/temp"),
    ("Titulky.com", "special://profile/addon_data/service.subtitles.titulky.com/temp"),
    ("Edna.cz", "special://profile/addon_data/service.subtitles.edna.cz/temp"),
)
_SOURCE_LANG_MAP = {"0": "auto", "1": "cs", "2": "sk"}
_RESOLVED_IMDB_CACHE = {}


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[%s] %s" % (__scriptid__, message), level)


def get_setting(name, default=""):
    if __settings__ is not None:
        try:
            value = __settings__.getString(name)
            return value if value != "" else default
        except Exception:
            pass
    try:
        value = __addon__.getSettingString(name)
    except Exception:
        value = __addon__.getSetting(name)
    return value if value != "" else default


def get_setting_int(name, default=0):
    if __settings__ is not None:
        try:
            return int(__settings__.getInt(name))
        except Exception:
            pass
    try:
        return int(__addon__.getSettingInt(name))
    except Exception:
        value = __addon__.getSetting(name)
        try:
            return int(str(value).strip())
        except Exception:
            return default


def get_source_language():
    raw = str(get_setting_int("source_language", 0))
    return _SOURCE_LANG_MAP.get(raw, raw)


def get_setting_bool(name, default=False):
    if __settings__ is not None:
        try:
            return bool(__settings__.getBool(name))
        except Exception:
            pass
    try:
        return __addon__.getSettingBool(name)
    except Exception:
        value = __addon__.getSetting(name)
        if value == "":
            return default
        return value.lower() == "true"


def ensure_workdir(clear=False):
    if clear and os.path.isdir(__workdir__):
        shutil.rmtree(__workdir__, ignore_errors=True)
    os.makedirs(__workdir__, exist_ok=True)


def cleanup_workdir(max_age_seconds=48 * 60 * 60):
    if not os.path.isdir(__workdir__):
        return
    cutoff = time.time() - max_age_seconds
    for name in os.listdir(__workdir__):
        path = os.path.join(__workdir__, name)
        if not os.path.isfile(path):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            continue


def get_translator():
    api_key = get_setting("deepl_api_key").strip()
    base_url = get_setting("deepl_api_base_url", "").strip()
    if not api_key:
        raise DeepLError(__addon__.getLocalizedString(32033))
    if not base_url:
        if api_key.endswith(":fx"):
            base_url = "https://api-free.deepl.com"
        else:
            base_url = "https://api.deepl.com"
    return DeepLTranslator(auth_key=api_key, base_url=base_url)


def params():
    if len(sys.argv) < 3:
        return {}
    query = sys.argv[2].lstrip("?")
    return dict(parse_qsl(query))


def end_directory():
    xbmcplugin.endOfDirectory(int(sys.argv[1]))


def add_action_item(label, action, source_label="Action"):
    url = "plugin://%s/?action=%s" % (__scriptid__, action)
    list_item = xbmcgui.ListItem(label=source_label, label2=label)
    list_item.setProperty("sync", "false")
    list_item.setProperty("hearing_imp", "false")
    xbmcplugin.addDirectoryItem(
        handle=int(sys.argv[1]),
        url=url,
        listitem=list_item,
        isFolder=False,
    )


def add_download_item(label, source_path, source_label):
    url = "plugin://%s/?action=download&source=%s" % (
        __scriptid__,
        quote(source_path, safe=""),
    )
    list_item = xbmcgui.ListItem(label=source_label, label2=label)
    list_item.setProperty("sync", "false")
    list_item.setProperty("hearing_imp", "false")
    xbmcplugin.addDirectoryItem(
        handle=int(sys.argv[1]),
        url=url,
        listitem=list_item,
        isFolder=False,
    )


def download(path):
    list_item = xbmcgui.ListItem(label=os.path.basename(path))
    xbmcplugin.addDirectoryItem(
        handle=int(sys.argv[1]),
        url=path,
        listitem=list_item,
        isFolder=False,
    )


def split_kodi_path(path):
    path = path.rstrip("/\\")
    if not path:
        return "", ""
    idx = max(path.rfind("/"), path.rfind("\\"))
    if idx < 0:
        return "", path
    return path[:idx], path[idx + 1 :]


def jsonrpc(method, params=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    try:
        raw = xbmc.executeJSONRPC(json.dumps(payload))
        parsed = json.loads(raw)
    except Exception as exc:
        log("JSON-RPC %s failed: %s" % (method, exc), xbmc.LOGWARNING)
        return {}
    if parsed.get("error"):
        log("JSON-RPC %s error: %s" % (method, parsed.get("error")), xbmc.LOGWARNING)
        return {}
    return parsed.get("result") or {}


def get_video_info():
    info = {}
    info["title"] = xbmc.getInfoLabel("VideoPlayer.Title") or ""
    info["original_title"] = xbmc.getInfoLabel("VideoPlayer.OriginalTitle") or ""
    info["year"] = xbmc.getInfoLabel("VideoPlayer.Year") or ""
    info["imdb"] = xbmc.getInfoLabel("VideoPlayer.IMDBNumber") or ""
    info["filename"] = xbmc.getInfoLabel("Player.Filename") or ""
    info["filepath"] = xbmc.Player().getPlayingFile() if xbmc.Player().isPlaying() else ""

    active_players = jsonrpc("Player.GetActivePlayers")
    if isinstance(active_players, dict):
        active_players = active_players.get("result", [])
    if not isinstance(active_players, list):
        active_players = []

    player_id = None
    for player in active_players:
        if player.get("type") == "video":
            player_id = player.get("playerid")
            break

    if player_id is not None:
        item_result = jsonrpc(
            "Player.GetItem",
            {
                "playerid": player_id,
                "properties": ["title", "originaltitle", "year", "file", "imdbnumber", "uniqueid"],
            },
        )
        item = item_result.get("item") or {}
        if item.get("title") and not info["title"]:
            info["title"] = item.get("title", "")
        if item.get("originaltitle") and not info["original_title"]:
            info["original_title"] = item.get("originaltitle", "")
        if item.get("year") and not info["year"]:
            info["year"] = str(item.get("year"))
        file_path = item.get("file") or ""
        if file_path and not info["filepath"]:
            info["filepath"] = file_path
        if file_path and not info["filename"]:
            _, info["filename"] = split_kodi_path(file_path)
        if not info["imdb"]:
            info["imdb"] = str(item.get("imdbnumber") or "")
        if not info["imdb"]:
            unique_ids = item.get("uniqueid") or {}
            info["imdb"] = str(unique_ids.get("imdb") or "")

    return info


def current_subtitle_context():
    names = []
    languages = []
    try:
        current_name = xbmc.Player().getSubtitles()
    except Exception:
        current_name = ""
    if current_name:
        names.append(current_name)

    current_lang = xbmc.getInfoLabel("VideoPlayer.SubtitlesLanguage")
    if current_lang:
        languages.append(current_lang)

    active_players = jsonrpc("Player.GetActivePlayers")
    if isinstance(active_players, dict):
        active_players = active_players.get("result", [])
    if not isinstance(active_players, list):
        active_players = []

    player_id = None
    for player in active_players:
        if player.get("type") == "video":
            player_id = player.get("playerid")
            break

    if player_id is not None:
        props = jsonrpc(
            "Player.GetProperties",
            {"playerid": player_id, "properties": ["currentsubtitle", "subtitleenabled"]},
        )
        current = props.get("currentsubtitle") or {}
        for key in ("name", "label"):
            value = current.get(key)
            if value:
                names.append(value)
        if current.get("language"):
            languages.append(current["language"])

    deduped_names = list(dict.fromkeys(name for name in names if name))
    deduped_languages = list(dict.fromkeys(lang for lang in languages if lang))
    return {"names": deduped_names, "languages": deduped_languages}


def file_mtime(path):
    try:
        translated = translatePath(path) if path.startswith("special://") else path
        return os.path.getmtime(translated)
    except Exception:
        try:
            return os.path.getmtime(path)
        except Exception:
            return 0


def sorted_sources(candidates, video_name, subtitle_context):
    ordered = []
    for source in candidates:
        _, name = split_kodi_path(source["path"])
        score = build_source_score(name, video_name, subtitle_context.get("names"))
        enriched = dict(source)
        enriched["_score"] = score
        enriched["_mtime"] = file_mtime(source["path"])
        ordered.append(enriched)
    ordered.sort(
        key=lambda item: (
            item["_score"][0],
            item["_score"][1],
            item["_score"][2],
            item["_mtime"],
            item["label"].lower(),
        ),
        reverse=True,
    )
    for source in ordered:
        source.pop("_score", None)
        source.pop("_mtime", None)
    return ordered


def local_copy(source_path):
    _, name = split_kodi_path(source_path)
    ext = os.path.splitext(name)[1].lower()
    local_path = os.path.join(__workdir__, "src_%s%s" % (uuid.uuid4().hex, ext))
    if not xbmcvfs.copy(source_path, local_path):
        raise IOError("Failed to copy %s" % source_path)
    return local_path


def extract_first_supported_from_zip(zip_path):
    local_zip = local_copy(zip_path)
    with zipfile.ZipFile(local_zip) as archive:
        for member in archive.namelist():
            ext = os.path.splitext(member)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            target = os.path.join(__workdir__, "zip_%s%s" % (uuid.uuid4().hex, ext))
            with archive.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            return {"path": target, "name": os.path.basename(member)}
    raise IOError("No supported subtitle file found in ZIP")


def custom_subtitle_folder_sources(video_name, subtitle_context):
    subtitle_dir = translatePath("special://subtitles")
    results = []
    if not subtitle_dir or not xbmcvfs.exists(subtitle_dir):
        return results
    try:
        _, files = xbmcvfs.listdir(subtitle_dir)
    except Exception as exc:
        log("Failed to list current subtitle cache: %s" % exc, xbmc.LOGWARNING)
        return results
    for name in sorted(files):
        ext = os.path.splitext(name)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        path = os.path.join(subtitle_dir, name)
        label = "Custom subtitle folder: %s" % name
        results.append({"path": path, "label": label, "source_label": "Custom"})
    return sorted_sources(results, video_name, subtitle_context)


def sibling_sources(video_name, subtitle_context):
    video_path = xbmc.Player().getPlayingFile() or xbmc.getInfoLabel("Player.Filenameandpath")
    results = []
    if not video_path:
        return results
    directory, _ = split_kodi_path(video_path)
    if not directory:
        return results
    try:
        _, files = xbmcvfs.listdir(directory)
    except Exception as exc:
        log("Failed to list sibling subtitles for %s: %s" % (video_path, exc), xbmc.LOGWARNING)
        return results
    for name in sorted(files):
        ext = os.path.splitext(name)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        path = directory.rstrip("/\\") + "/" + name
        lang = guess_language_from_name(name) or "auto"
        label = "Nearby subtitle: %s" % name
        results.append({"path": path, "label": label, "source_label": lang.upper()})
    return sorted_sources(results, video_name, subtitle_context)


def temp_sources(video_name, subtitle_context, limit=20):
    temp_dir = translatePath("special://temp")
    results = []
    if not temp_dir or not os.path.isdir(temp_dir):
        return results
    current_names = subtitle_context.get("names") or []
    recent_cutoff = time.time() - (24 * 60 * 60)
    scanned = 0
    for root, _, files in os.walk(temp_dir):
        scanned += 1
        if scanned > 200:
            break
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            path = os.path.join(root, name)
            mtime = file_mtime(path)
            if mtime and mtime < recent_cutoff:
                continue
            score = build_source_score(name, video_name, current_names)
            if not any(score[:3]) and "subtitle" not in normalize_name(root):
                continue
            lang = guess_language_from_name(name) or "auto"
            results.append(
                {
                    "path": path,
                    "label": "Kodi temp subtitle: %s" % name,
                    "source_label": lang.upper(),
                }
            )
    return sorted_sources(results, video_name, subtitle_context)[:limit]


def known_subtitle_addon_temp_sources(video_name, subtitle_context):
    results = []
    for provider_name, special_path in KNOWN_SUBTITLE_ADDON_TEMP_DIRS:
        base_dir = translatePath(special_path)
        if not base_dir or not os.path.isdir(base_dir):
            continue
        try:
            names = sorted(os.listdir(base_dir))
        except Exception as exc:
            log("Failed to list %s temp subtitles: %s" % (provider_name, exc), xbmc.LOGWARNING)
            continue
        for name in names:
            ext = os.path.splitext(name)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            path = os.path.join(base_dir, name)
            lang = guess_language_from_name(name) or "auto"
            results.append(
                {
                    "path": path,
                    "label": "%s temp subtitle: %s" % (provider_name, name),
                    "source_label": lang.upper(),
                }
            )
    return sorted_sources(results, video_name, subtitle_context)


def _is_already_translated(path):
    _, name = split_kodi_path(path)
    lower = name.lower()
    if ".deepl." in lower:
        return True
    lang = guess_language_from_name(name)
    return lang == "uk"


def unique_sources():
    video_path = xbmc.Player().getPlayingFile() or xbmc.getInfoLabel("Player.Filenameandpath")
    _, video_name = split_kodi_path(video_path)
    subtitle_context = current_subtitle_context()
    sources = []
    seen = set()
    sources.extend(custom_subtitle_folder_sources(video_name, subtitle_context))
    sources.extend(known_subtitle_addon_temp_sources(video_name, subtitle_context))
    sources.extend(sibling_sources(video_name, subtitle_context))
    if not sources:
        sources.extend(temp_sources(video_name, subtitle_context))
    deduped = []
    for source in sources:
        path = source["path"]
        if path in seen:
            continue
        seen.add(path)
        if _is_already_translated(path):
            continue
        deduped.append(source)
    return deduped


def build_source_items():
    items = []
    action_label = __addon__.getLocalizedString(32040) or "Queue remote upload"
    for source in unique_sources():
        label = "%s -> Translate + %s" % (source["label"], action_label)
        items.append(
            {
                "label": label,
                "source_path": source["path"],
                "source_label": source["source_label"],
            }
        )
    return items


def source_cache_path(source_path, source_language, output_name_hint=None):
    _, name = split_kodi_path(output_name_hint or source_path)
    name = name or output_name_hint or source_path
    extension = os.path.splitext(name)[1].lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Unsupported subtitle format: %s" % extension)
    cache_basis = "%s|%s|%s|dualass" % (source_path, file_mtime(source_path), source_language)
    cache_key = hashlib.sha1(cache_basis.encode("utf-8")).hexdigest()[:12]
    target_name = "%s.%s.uk.deepl.ass" % (safe_stem(name), cache_key)
    return os.path.join(__workdir__, target_name)


def generate_or_load_translated_subtitle(source_path):
    ensure_workdir()
    cleanup_workdir()

    source_language = get_source_language()
    output_path = source_cache_path(source_path, source_language)
    if os.path.exists(output_path):
        return output_path

    translator = get_translator()
    _, source_name = split_kodi_path(source_path)
    local_source = local_copy(source_path)
    info = generate_translated_subtitle(
        local_source,
        __workdir__,
        translator,
        source_language,
        source_name_hint=source_name or source_path,
        output_name_hint=source_name or source_path,
        output_path=output_path,
    )
    return info["path"]


def check_and_maybe_download_os(source_path):
    video_info = ensure_video_imdb(get_video_info(), source_path)
    imdb_id = video_info.get("imdb", "")
    title = video_info.get("title", "")
    year = video_info.get("year", "")

    if not imdb_id and not title:
        log("OS check: no video info, skipping check")
        return None

    log("OS check: imdb=%s title=%s year=%s" % (imdb_id, title, year))

    try:
        found = check_ukrainian_subtitles(imdb_id=imdb_id, title=title, year=year)
    except Exception as exc:
        log("OS check failed: %s" % exc, xbmc.LOGWARNING)
        return None

    if not found:
        log("OS check: no Ukrainian subs found")
        return None

    log("OS check: found %d Ukrainian sub(s)" % len(found))

    choice_labels = format_choice_list(found)
    choice_labels.append("[DeepL] Translate with DeepL instead")

    selected = __dialog__.select(
        "Ukrainian subtitles on OpenSubtitles",
        choice_labels,
    )

    if selected < 0:
        return "CANCEL"

    if selected == len(choice_labels) - 1:
        return None

    chosen = found[selected]
    file_id = chosen.get("file_id")
    if not file_id:
        log("OS download: no file_id", xbmc.LOGWARNING)
        return None

    log("OS download: file_id=%s release=%s" % (file_id, chosen.get("release", "")))

    os_user = get_setting("os_username", "")
    os_pass = get_setting("os_password", "")
    auth_token = None
    if os_user and os_pass:
        try:
            auth_token = os_login(os_user, os_pass)
        except Exception as exc:
            log("OS login failed: %s, trying anonymous" % exc, xbmc.LOGWARNING)

    ensure_workdir()
    ua_path = download_subtitle(file_id, auth_token=auth_token, output_dir=__workdir__)
    if ua_path and os.path.isfile(ua_path):
        log("OS download OK: %s (%d bytes)" % (ua_path, os.path.getsize(ua_path)))
        return ua_path

    log("OS download failed", xbmc.LOGWARNING)
    __dialog__.notification(
        __scriptname__,
        "Download failed, falling back to DeepL",
        xbmcgui.NOTIFICATION_WARNING,
        3000,
    )
    return None


def build_release_name(video_info, fallback_path):
    release_name = video_info.get("filename", "") or ""
    if release_name:
        return release_name

    filepath = video_info.get("filepath", "") or ""
    if filepath:
        _, release_name = split_kodi_path(filepath)
        if release_name:
            return release_name

    title = video_info.get("title", "") or ""
    if title:
        return title

    return os.path.basename(fallback_path)


def ensure_video_imdb(video_info, fallback_path=""):
    imdb_id = str(video_info.get("imdb", "") or "").replace("tt", "").strip()
    if imdb_id.isdigit():
        video_info["imdb"] = imdb_id
        return video_info

    release_name = build_release_name(
        video_info,
        fallback_path or video_info.get("filepath", "") or video_info.get("filename", ""),
    )
    cache_key = "|".join(
        [
            str(video_info.get("title", "") or ""),
            str(video_info.get("original_title", "") or ""),
            str(video_info.get("year", "") or ""),
            str(release_name or ""),
            str(video_info.get("filename", "") or ""),
        ]
    )

    cached = _RESOLVED_IMDB_CACHE.get(cache_key)
    if cached:
        video_info["imdb"] = cached
        return video_info

    resolved = resolve_imdb_id(
        imdb_id=imdb_id,
        title=video_info.get("title", ""),
        original_title=video_info.get("original_title", ""),
        year=video_info.get("year", ""),
        release_name=release_name,
        filename=video_info.get("filename", ""),
    )
    if not resolved:
        log(
            "IMDb resolve failed [title=%s, original_title=%s, year=%s, release=%s, filename=%s]"
            % (
                video_info.get("title", ""),
                video_info.get("original_title", ""),
                video_info.get("year", ""),
                release_name,
                video_info.get("filename", ""),
            )
        )
        return video_info

    resolved_imdb = str(resolved.get("imdb_id", "")).strip()
    if resolved_imdb:
        video_info["imdb"] = resolved_imdb
        _RESOLVED_IMDB_CACHE[cache_key] = resolved_imdb
        log(
            "Resolved IMDb id via OpenSubtitles search: %s (query=%s, match=%s, feature=%s)"
            % (
                resolved_imdb,
                resolved.get("query", ""),
                resolved.get("matched_by", ""),
                resolved.get("feature_title", ""),
            )
        )
    return video_info


def build_helper_lookup_summary(video_info, release_name, source_name):
    details = []
    imdb_id = (video_info.get("imdb", "") or "").strip()
    title = (video_info.get("title", "") or "").strip()
    year = (video_info.get("year", "") or "").strip()
    if imdb_id:
        details.append("imdb=%s" % imdb_id)
    if release_name:
        details.append("release=%s" % release_name)
    if source_name:
        details.append("source=%s" % source_name)
    if title:
        details.append("title=%s" % title)
    if year:
        details.append("year=%s" % year)
    return ", ".join(details) if details else "no lookup metadata"


def helper_cache_message(prefix, details, reason=""):
    message = prefix
    if details:
        message = "%s [%s]" % (message, details)
    if reason:
        message = "%s: %s" % (message, reason)
    return message


def build_dual_subtitle_from_ukrainian(source_path, ukrainian_path):
    _, source_name = split_kodi_path(source_path)
    local_source = local_copy(source_path)
    output_name = "%s.dual.ass" % safe_stem(source_name or "subtitle")
    output_path = os.path.join(__workdir__, output_name)
    return merge_two_subtitles_to_dual_ass(local_source, ukrainian_path, output_path)


def check_helper_for_cached_translation(source_path):
    helper_url = get_setting("helper_url", "").strip()
    if not helper_url:
        log("Helper cache check skipped: helper URL is empty")
        return None

    video_info = ensure_video_imdb(get_video_info(), source_path)
    imdb_id = video_info.get("imdb", "").strip()
    helper_token = get_setting("helper_token", "").strip()
    release_name = build_release_name(video_info, source_path)
    _, source_name = split_kodi_path(source_path)
    title = video_info.get("title", "").strip()
    year = video_info.get("year", "").strip()
    lookup_details = build_helper_lookup_summary(video_info, release_name, source_name or source_path)
    if not imdb_id:
        log("Helper cache check: IMDb missing, trying fallback lookup [%s]" % lookup_details)

    if not imdb_id and not any((release_name, source_name, title)):
        log("Helper cache check skipped: no lookup metadata [%s]" % lookup_details, xbmc.LOGWARNING)
        return None

    try:
        cached = download_helper_cached_subtitle(
            helper_url=helper_url,
            helper_token=helper_token,
            output_dir=__workdir__,
            imdb_id=imdb_id,
            release_name=release_name,
            source_filename=source_name or source_path,
            title=title,
            year=year,
        )
    except HelperUploadError as exc:
        message = helper_cache_message("Saved translation lookup failed", lookup_details, str(exc))
        log("Helper cache check failed: %s" % message, xbmc.LOGWARNING)
        __dialog__.notification(__scriptname__, message, xbmcgui.NOTIFICATION_WARNING, 5000)
        return None

    if not cached or not cached.get("found"):
        reason = ""
        if isinstance(cached, dict):
            reason = str(cached.get("reason") or "").strip()
        message = helper_cache_message(
            "Helper cache check: no saved translation",
            lookup_details,
            reason or "no match on helper",
        )
        log(message)
        return None

    path = cached.get("path")
    if not path or not os.path.isfile(path):
        message = helper_cache_message(
            "Helper cache check: helper returned invalid file path",
            lookup_details,
            "missing downloaded cache file",
        )
        log(message, xbmc.LOGWARNING)
        __dialog__.notification(__scriptname__, message, xbmcgui.NOTIFICATION_WARNING, 5000)
        return None

    log(
        "Helper cache check: using %s (job_status=%s match=%s, query=%s)"
        % (
            os.path.basename(path),
            cached.get("job_status", "unknown"),
            cached.get("matched_by", "unknown"),
            cached.get("lookup_summary", lookup_details),
        )
    )
    __dialog__.notification(
        __scriptname__,
        "Using saved translation from LibreELEC helper [%s]"
        % cached.get("matched_by", "match"),
        xbmcgui.NOTIFICATION_INFO,
        3500,
    )
    return path


def notify_upload_success(response, auto_submit):
    message = __addon__.getLocalizedString(32044 if auto_submit else 32042)
    job_id = ""
    if isinstance(response, dict):
        job_id = response.get("job_id", "")
    if job_id:
        message = "%s [%s]" % (message, job_id[:8])
    __dialog__.notification(
        __scriptname__,
        message,
        xbmcgui.NOTIFICATION_INFO,
        5000,
    )


def try_upload_to_opensubtitles(ass_path):
    if not get_setting_bool("auto_upload", False):
        return

    helper_url = get_setting("helper_url", "").strip()
    if not helper_url:
        log("Upload skipped: helper URL is empty", xbmc.LOGWARNING)
        __dialog__.notification(
            __scriptname__,
            __addon__.getLocalizedString(32045),
            xbmcgui.NOTIFICATION_WARNING,
            3000,
        )
        return

    video_info = ensure_video_imdb(get_video_info(), ass_path)
    imdb_id = video_info.get("imdb", "").strip()
    if not imdb_id:
        details = build_helper_lookup_summary(video_info, build_release_name(video_info, ass_path), os.path.basename(ass_path))
        message = "%s: missing IMDb id [%s]" % ((__addon__.getLocalizedString(32043) or "Remote upload failed"), details)
        log("Upload skipped: missing IMDb id for current video [%s]" % details, xbmc.LOGWARNING)
        __dialog__.notification(__scriptname__, message, xbmcgui.NOTIFICATION_WARNING, 4000)
        return

    try:
        srt_path = extract_srt_from_ass(ass_path)
    except UploadError as exc:
        log("Upload: failed to extract SRT: %s" % exc, xbmc.LOGWARNING)
        __dialog__.notification(
            __scriptname__,
            "%s: %s" % (__addon__.getLocalizedString(32043), exc),
            xbmcgui.NOTIFICATION_ERROR,
            5000,
        )
        return

    helper_token = get_setting("helper_token", "").strip()
    os_user = get_setting("os_username", "")
    os_pass = get_setting("os_password", "")
    auto_submit = get_setting_bool("auto_submit", False)
    machine_translated = get_setting_bool("mark_machine_translated", True)
    release_name = build_release_name(video_info, ass_path)

    try:
        log("Queueing helper upload for %s" % os.path.basename(srt_path))
        response = queue_helper_upload(
            helper_url=helper_url,
            helper_token=helper_token,
            subtitle_path=srt_path,
            language="uk",
            imdb_id=imdb_id,
            fps="25.000",
            release_name=release_name,
            username=os_user,
            password=os_pass,
            machine_translated=machine_translated,
            auto_submit=auto_submit,
            comment="DeepL translated from Czech/Slovak",
        )
        log("Helper accepted upload job: %s" % response)
        notify_upload_success(response, auto_submit)
    except HelperUploadError as exc:
        log("Helper upload failed for %s: %s" % (release_name, exc), xbmc.LOGWARNING)
        __dialog__.notification(
            __scriptname__,
            "%s [%s]: %s" % (__addon__.getLocalizedString(32043), release_name, exc),
            xbmcgui.NOTIFICATION_ERROR,
            5000,
        )


def search():
    ensure_workdir()
    cleanup_workdir()
    if not get_setting("deepl_api_key").strip():
        add_action_item(__addon__.getLocalizedString(32033), "settings", "Setup")
        add_action_item("Open settings", "settings", "Settings")
        return

    items = build_source_items()
    if items:
        for item in items:
            add_download_item(item["label"], item["source_path"], item["source_label"])
    else:
        add_action_item(__addon__.getLocalizedString(32031), "noop", "Info")

    add_action_item("Browse subtitle file", "browse", "Browse")
    add_action_item("Open settings", "settings", "Settings")


def browse_and_generate():
    mask = ".zip|" + "|".join(SUPPORTED_EXTENSIONS)
    default_path = translatePath("special://subtitles")
    source = __dialog__.browse(
        1,
        "Choose subtitle file",
        "video",
        mask,
        False,
        False,
        default_path,
        False,
    )
    if not source or source == default_path:
        return None

    source_language = get_source_language()
    try:
        translator = get_translator()
        if source.lower().endswith(".zip"):
            prepared = extract_first_supported_from_zip(source)
            prepared_path = prepared["path"]
            prepared_name = prepared["name"]
        else:
            prepared_path = local_copy(source)
            _, prepared_name = split_kodi_path(source)
        return generate_translated_subtitle(
            prepared_path,
            __workdir__,
            translator,
            source_language,
            source_name_hint=prepared_name or source,
            output_name_hint=prepared_name or source,
        )
    except Exception as exc:
        __dialog__.ok(__scriptname__, "%s\n%s" % (__addon__.getLocalizedString(32030), exc))
        return None


def handle_action():
    action = params().get("action", "search")
    if action in ("search", "manualsearch"):
        search()
        return

    if action == "browse":
        ensure_workdir()
        info = browse_and_generate()
        if info:
            try_upload_to_opensubtitles(info["path"])
            download(info["path"])
        return

    if action == "download":
        source_path = unquote(params().get("source", ""))
        if not source_path:
            return

        ensure_workdir()
        cleanup_workdir()
        os_result = check_and_maybe_download_os(source_path)

        if os_result == "CANCEL":
            log("User cancelled")
            return

        if os_result and os.path.isfile(os_result):
            log("Using OS Ukrainian subtitle: %s" % os_result)
            try:
                dual_path = build_dual_subtitle_from_ukrainian(source_path, os_result)
                download(dual_path)
            except Exception as exc:
                __dialog__.ok(__scriptname__, "Merge failed:\n%s" % exc)
            return

        helper_result = check_helper_for_cached_translation(source_path)
        if helper_result and os.path.isfile(helper_result):
            try:
                dual_path = build_dual_subtitle_from_ukrainian(source_path, helper_result)
                download(dual_path)
            except Exception as exc:
                __dialog__.ok(__scriptname__, "Merge failed:\n%s" % exc)
            return

        progress = xbmcgui.DialogProgress()
        progress.create(__scriptname__, __addon__.getLocalizedString(32035))
        try:
            translated_path = generate_or_load_translated_subtitle(source_path)
            progress.close()
            try_upload_to_opensubtitles(translated_path)
            download(translated_path)
        except DeepLError as exc:
            progress.close()
            __dialog__.ok(__scriptname__, str(exc))
        except Exception as exc:
            progress.close()
            __dialog__.ok(
                __scriptname__,
                "%s\nSource: %s\nReason: %s"
                % (__addon__.getLocalizedString(32030), os.path.basename(source_path), exc),
            )
        return

    if action == "settings":
        __addon__.openSettings()
        add_action_item("Settings updated. Search again to refresh.", "search", "Info")
        return

    if action == "noop":
        return

    log("Unknown action: %s" % action, xbmc.LOGWARNING)


handle_action()
end_directory()

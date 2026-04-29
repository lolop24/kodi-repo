# -*- coding: utf-8 -*-
"""Send Kodi logs to the remote TitDeepL helper."""

import base64
import json
import os
import socket
import sys
import time

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    from urllib2 import HTTPError, URLError, Request, urlopen

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")
ADDON_VERSION = ADDON.getAddonInfo("version")
TITDEEPL_ADDON_ID = "service.subtitles.titdeepl-localsub"

try:
    translatePath = xbmcvfs.translatePath
except AttributeError:
    translatePath = xbmc.translatePath


class LogSendError(Exception):
    pass


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[%s] %s" % (ADDON_ID, message), level)


def get_setting(name, default=""):
    try:
        value = ADDON.getSettingString(name)
    except Exception:
        value = ADDON.getSetting(name)
    return value if value != "" else default


def get_setting_bool(name, default=False):
    try:
        return ADDON.getSettingBool(name)
    except Exception:
        value = ADDON.getSetting(name)
        if value == "":
            return default
        return str(value).lower() == "true"


def get_bounded_int(name, default, minimum, maximum):
    try:
        value = int(str(get_setting(name, str(default))).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def get_other_addon_setting(addon_id, setting_id):
    try:
        addon = xbmcaddon.Addon(addon_id)
        try:
            return addon.getSettingString(setting_id)
        except Exception:
            return addon.getSetting(setting_id)
    except Exception:
        return ""


def normalize_helper_url(helper_url):
    helper_url = (helper_url or "").strip().rstrip("/")
    if not helper_url:
        raise LogSendError("Helper URL is empty. Set it here or in TitDeepL LocalSub settings.")
    if not helper_url.startswith(("http://", "https://")):
        helper_url = "http://" + helper_url
    return helper_url


def helper_config():
    helper_url = get_setting("helper_url", "").strip() or get_other_addon_setting(TITDEEPL_ADDON_ID, "helper_url").strip()
    helper_token = get_setting("helper_token", "").strip() or get_other_addon_setting(TITDEEPL_ADDON_ID, "helper_token").strip()
    return normalize_helper_url(helper_url), helper_token


def platform_name():
    checks = (
        ("android", "System.Platform.Android"),
        ("xbox", "System.Platform.UWP"),
        ("windows", "System.Platform.Windows"),
        ("linux", "System.Platform.Linux"),
        ("osx", "System.Platform.OSX"),
        ("ios", "System.Platform.IOS"),
    )
    for name, condition in checks:
        try:
            if xbmc.getCondVisibility(condition):
                return name
        except Exception:
            continue
    return "unknown"


def device_name():
    configured = get_setting("device_name", "").strip()
    if configured:
        return configured
    friendly = xbmc.getInfoLabel("System.FriendlyName")
    if friendly:
        return friendly
    try:
        return socket.gethostname()
    except Exception:
        return "kodi-device"


def log_dir_paths():
    paths = []
    try:
        base = "special://logpath/"
        _, files = xbmcvfs.listdir(base)
    except Exception:
        files = []

    wanted = {"kodi.log"}
    if get_setting_bool("include_old_log", True):
        wanted.add("kodi.old.log")

    for name in files:
        lower = str(name or "").lower()
        if lower in wanted or "crash" in lower or lower.endswith(".stacktrace"):
            paths.append(base + name)

    for name in sorted(wanted):
        candidate = "special://logpath/" + name
        if candidate not in paths:
            paths.append(candidate)

    return paths


def read_local_tail(path, max_bytes):
    translated = translatePath(path)
    if not translated or not os.path.isfile(translated):
        return None
    size = os.path.getsize(translated)
    with open(translated, "rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        return handle.read(max_bytes), size > max_bytes


def read_xbmcvfs_tail(path, max_bytes):
    handle = None
    try:
        handle = xbmcvfs.File(path)
        try:
            size = handle.size()
        except Exception:
            size = 0
        if size and size > max_bytes:
            try:
                handle.seek(size - max_bytes)
            except Exception:
                pass
        try:
            data = handle.readBytes(max_bytes)
        except Exception:
            data = handle.read(max_bytes)
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        return data, bool(size and size > max_bytes)
    except Exception:
        return None
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def read_log_file(path, max_bytes):
    result = read_local_tail(path, max_bytes)
    if result is None:
        result = read_xbmcvfs_tail(path, max_bytes)
    if result is None:
        return None
    data, truncated = result
    if not data:
        return None
    filename = os.path.basename(path.rstrip("/\\")) or "kodi.log"
    return {
        "filename": filename,
        "source_path": path,
        "bytes": len(data),
        "truncated": truncated,
        "content_b64": base64.b64encode(data).decode("ascii"),
    }


def collect_logs():
    max_bytes = get_bounded_int("max_log_bytes", 1200000, 50000, 5000000)
    logs = []
    seen = set()
    for path in log_dir_paths():
        filename = os.path.basename(path.rstrip("/\\")).lower()
        if filename in seen:
            continue
        seen.add(filename)
        item = read_log_file(path, max_bytes)
        if item:
            logs.append(item)
    if not logs:
        raise LogSendError("No Kodi log files were readable from special://logpath/.")
    return logs


def helper_headers(helper_token):
    headers = {"Content-Type": "application/json"}
    if helper_token:
        headers["Authorization"] = "Bearer %s" % helper_token
    return headers


def build_payload(notes=""):
    build_version = xbmc.getInfoLabel("System.BuildVersion")
    return {
        "device_name": device_name(),
        "device_label": "%s %s" % (platform_name(), device_name()),
        "platform": platform_name(),
        "kodi_version": build_version,
        "build_version": build_version,
        "addon_version": ADDON_VERSION,
        "notes": notes or "Kodi log upload from Lolop Log Sender",
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "logs": collect_logs(),
    }


def post_json(url, helper_token, payload):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=helper_headers(helper_token),
    )
    try:
        response = urlopen(request, timeout=45)
        body = response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        try:
            details = exc.read().decode("utf-8", "replace")
        except Exception:
            details = str(exc)
        if exc.code == 401:
            raise LogSendError(
                "Helper authorization failed (401). Set the helper token in Log Sender or TitDeepL, "
                "or enable LAN read/log access on the helper."
            )
        raise LogSendError("Helper returned HTTP %s: %s" % (exc.code, details))
    except URLError as exc:
        raise LogSendError("Could not reach helper: %s" % exc)
    except Exception as exc:
        raise LogSendError("Unexpected helper error: %s" % exc)

    try:
        parsed = json.loads(body)
    except Exception as exc:
        raise LogSendError("Helper returned invalid JSON: %s" % exc)
    if not isinstance(parsed, dict) or not parsed.get("ok"):
        raise LogSendError("Helper rejected the log upload: %s" % body[:500])
    return parsed


def send_logs(notes="", show_dialog=False):
    helper_url, helper_token = helper_config()
    payload = build_payload(notes=notes)
    response = post_json(helper_url + "/api/device-logs", helper_token, payload)
    log_id = str(response.get("log_id") or "")
    log("Sent %d log file(s) to helper as %s" % (len(payload["logs"]), log_id))
    if show_dialog:
        xbmcgui.Dialog().ok(ADDON_NAME, "Logs sent to helper.\nLog id: %s" % (log_id or "unknown"))
    return response


def run_manual():
    progress = xbmcgui.DialogProgress()
    progress.create(ADDON_NAME, "Collecting Kodi logs...")
    try:
        progress.update(30, "Preparing log bundle...")
        response = send_logs(show_dialog=False)
        progress.close()
        log_id = str(response.get("log_id") or "unknown")
        xbmcgui.Dialog().ok(ADDON_NAME, "Logs sent to helper.\nLog id: %s" % log_id)
    except Exception as exc:
        progress.close()
        log("Log upload failed: %s" % exc, xbmc.LOGWARNING)
        xbmcgui.Dialog().ok(ADDON_NAME, "Log upload failed.\n%s" % exc)


if __name__ == "__main__":
    run_manual()

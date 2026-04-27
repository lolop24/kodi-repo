# -*- coding: utf-8 -*-
"""Background monitor for TitDeepL LocalSub helper-generated subtitles."""

import json
import os
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.settings_migrator import migrate_settings
from resources.lib.upload_helper import (
    HelperUploadError,
    download_helper_embedded_dual_subtitle,
    get_helper_embedded_dual_job,
)


try:
    translatePath = xbmcvfs.translatePath
except AttributeError:
    translatePath = xbmc.translatePath


migrate_settings()
ADDON = xbmcaddon.Addon()
SCRIPT_ID = ADDON.getAddonInfo("id")
SCRIPT_NAME = ADDON.getAddonInfo("name")
PROFILE_DIR = translatePath(ADDON.getAddonInfo("profile"))
WORK_DIR = os.path.join(PROFILE_DIR, "generated")
ACTIVE_JOB_PATH = os.path.join(PROFILE_DIR, "active_embedded_dual_job.json")


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[%s service] %s" % (SCRIPT_ID, message), level)


def get_setting(name, default=""):
    try:
        value = ADDON.getSettingString(name)
    except Exception:
        value = ADDON.getSetting(name)
    return value if value != "" else default


def read_active_job():
    try:
        with open(ACTIVE_JOB_PATH, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def write_active_job(record):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with open(ACTIVE_JOB_PATH, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)


def clear_active_job():
    try:
        os.remove(ACTIVE_JOB_PATH)
    except OSError:
        pass


def is_video_playing():
    player = xbmc.Player()
    try:
        return player.isPlayingVideo()
    except Exception:
        return player.isPlaying()


def current_playback_path():
    try:
        return xbmc.Player().getPlayingFile() or ""
    except Exception:
        return ""


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def notify_once(record, key, message, icon=xbmcgui.NOTIFICATION_WARNING):
    now = int(time.time())
    last = safe_int(record.get(key), 0)
    if now - last < 60:
        return record
    xbmcgui.Dialog().notification(SCRIPT_NAME, message, icon, 4500)
    record[key] = now
    write_active_job(record)
    return record


def load_new_helper_chunk(record):
    helper_url = str(record.get("helper_url") or get_setting("helper_url", "")).strip()
    helper_token = get_setting("helper_token", "").strip()
    job_id = str(record.get("job_id") or "").strip()
    if not helper_url or not job_id:
        clear_active_job()
        return

    job = get_helper_embedded_dual_job(helper_url, helper_token, job_id, timeout=20)
    status = str(job.get("status") or "").strip().lower()
    error = str(job.get("error") or "").strip()
    if status == "failed":
        clear_active_job()
        xbmcgui.Dialog().notification(
            SCRIPT_NAME,
            "Helper dual subtitles failed: %s" % (error or "unknown error"),
            xbmcgui.NOTIFICATION_ERROR,
            6000,
        )
        return

    ready_seconds = safe_int(job.get("latest_ready_seconds"), 0)
    last_loaded = safe_int(record.get("last_loaded_seconds"), 0)
    if ready_seconds <= last_loaded:
        if status == "finished" and job.get("full_ready"):
            clear_active_job()
        return

    version = str(job.get("latest_version") or ready_seconds)
    subtitle_path = download_helper_embedded_dual_subtitle(
        helper_url=helper_url,
        helper_token=helper_token,
        job_id=job_id,
        output_dir=WORK_DIR,
        version=version,
        timeout=45,
    )
    xbmc.Player().setSubtitles(subtitle_path)
    record["last_loaded_seconds"] = ready_seconds
    record["last_version"] = version
    record["updated_at"] = int(time.time())
    write_active_job(record)
    log("Loaded helper dual subtitle chunk %s seconds for job %s" % (ready_seconds, job_id[:8]))

    if status == "finished" and job.get("full_ready"):
        clear_active_job()


def main():
    os.makedirs(WORK_DIR, exist_ok=True)
    monitor = xbmc.Monitor()
    log("started")

    while not monitor.abortRequested():
        if monitor.waitForAbort(5):
            break

        record = read_active_job()
        if not record:
            continue

        if not is_video_playing():
            clear_active_job()
            continue

        expected_path = str(record.get("playback_path") or "").strip()
        current_path = current_playback_path()
        if expected_path and current_path and expected_path != current_path:
            clear_active_job()
            continue

        try:
            load_new_helper_chunk(record)
        except HelperUploadError as exc:
            record = notify_once(
                record,
                "last_helper_error_at",
                "Helper dual subtitle update failed: %s" % exc,
            )
            log("Helper dual subtitle update failed: %s" % exc, xbmc.LOGWARNING)
        except Exception as exc:
            record = notify_once(
                record,
                "last_unexpected_error_at",
                "Dual subtitle monitor failed: %s" % exc,
            )
            log("Dual subtitle monitor failed: %s" % exc, xbmc.LOGWARNING)

    log("stopped")


if __name__ == "__main__":
    main()

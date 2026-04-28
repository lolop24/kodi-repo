# -*- coding: utf-8 -*-
"""Background monitor for TitDeepL LocalSub helper-generated subtitles."""

import json
import os
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

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
MEDIA_RELAY_STATE_PATH = os.path.join(PROFILE_DIR, "media_relay_state.json")
MEDIA_RELAY_REGISTRY_PATH = os.path.join(PROFILE_DIR, "media_relay_registry.json")
MEDIA_RELAY_TOKEN = uuid.uuid4().hex + uuid.uuid4().hex


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


def read_json_file(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        return parsed if isinstance(parsed, dict) else default
    except Exception:
        return default


def write_json_file(path, data):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    temp_path = "%s.tmp" % path
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    try:
        os.replace(temp_path, path)
    except AttributeError:
        if os.path.exists(path):
            os.remove(path)
        os.rename(temp_path, path)


def remove_file(path):
    try:
        os.remove(path)
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


def helper_host_for_ip_detection():
    helper_url = get_setting("helper_url", "").strip()
    if helper_url and not helper_url.startswith(("http://", "https://")):
        helper_url = "http://" + helper_url
    try:
        parsed = urlparse(helper_url)
    except Exception:
        return "", 80
    return parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80)


def local_lan_ip():
    try:
        ip = xbmc.getIPAddress()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    targets = []
    host, port = helper_host_for_ip_detection()
    if host:
        targets.append((host, port))
    targets.append(("8.8.8.8", 80))

    for host, port in targets:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            sock.connect((host, port))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
        finally:
            try:
                if sock:
                    sock.close()
            except Exception:
                pass
    return ""


def media_relay_target(relay_id):
    registry = read_json_file(MEDIA_RELAY_REGISTRY_PATH, {}) or {}
    records = registry.get("records")
    if not isinstance(records, dict):
        return ""
    record = records.get(relay_id) or {}
    if not isinstance(record, dict):
        return ""
    target = str(record.get("target") or "").strip()
    parsed = urlparse(target)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https"):
        return ""
    if host not in ("127.0.0.1", "localhost", "::1") and not host.startswith("127."):
        return ""
    return target


def cleanup_media_relay_registry(max_age_seconds=6 * 60 * 60):
    registry = read_json_file(MEDIA_RELAY_REGISTRY_PATH, {}) or {}
    records = registry.get("records")
    if not isinstance(records, dict):
        return
    now = int(time.time())
    cleaned = {
        key: value
        for key, value in records.items()
        if isinstance(value, dict) and now - safe_int(value.get("created_at"), 0) < max_age_seconds
    }
    if len(cleaned) != len(records):
        write_json_file(MEDIA_RELAY_REGISTRY_PATH, {"records": cleaned, "updated_at": now})


class KodiMediaRelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log("media relay: " + (fmt % args))

    def do_HEAD(self):
        self.forward_media_request(head_only=True)

    def do_GET(self):
        self.forward_media_request(head_only=False)

    def forward_media_request(self, head_only=False):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/media/"):
            self.send_error(404, "Not found")
            return

        token = (parse_qs(parsed.query).get("token") or [""])[0]
        if token != MEDIA_RELAY_TOKEN:
            self.send_error(403, "Forbidden")
            return

        relay_id = parsed.path.rsplit("/", 1)[-1].strip()
        target = media_relay_target(relay_id)
        if not target:
            self.send_error(404, "Media relay target expired or missing")
            return

        headers = {"User-Agent": "Kodi TitDeepL LocalSub relay"}
        for name in ("Range", "Accept"):
            value = self.headers.get(name)
            if value:
                headers[name] = value
        if head_only and "Range" not in headers:
            headers["Range"] = "bytes=0-0"

        request = urlrequest.Request(target, headers=headers, method="GET")
        try:
            response = urlrequest.urlopen(request, timeout=60)
            status = getattr(response, "status", 200)
            response_headers = response.headers
        except HTTPError as exc:
            response = exc
            status = exc.code
            response_headers = exc.headers
        except URLError as exc:
            self.send_error(502, "Local Kodi stream is not reachable: %s" % exc.reason)
            return
        except Exception as exc:
            self.send_error(502, "Local Kodi stream relay failed: %s" % exc)
            return

        try:
            self.send_response(status)
            for name, value in response_headers.items():
                if name.lower() in ("connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"):
                    continue
                self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            if head_only:
                return
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                response.close()
            except Exception:
                pass


class KodiMediaRelayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_media_relay():
    ip = local_lan_ip()
    if not ip:
        log("media relay disabled: could not determine Kodi LAN IP", xbmc.LOGWARNING)
        remove_file(MEDIA_RELAY_STATE_PATH)
        return None
    try:
        server = KodiMediaRelayServer(("0.0.0.0", 0), KodiMediaRelayHandler)
    except Exception as exc:
        log("media relay disabled: could not bind server: %s" % exc, xbmc.LOGWARNING)
        remove_file(MEDIA_RELAY_STATE_PATH)
        return None

    port = server.server_address[1]
    state = {
        "base_url": "http://%s:%s" % (ip, port),
        "token": MEDIA_RELAY_TOKEN,
        "updated_at": int(time.time()),
    }
    write_json_file(MEDIA_RELAY_STATE_PATH, state)
    thread = threading.Thread(target=server.serve_forever, name="titdeepl-media-relay", daemon=True)
    thread.start()
    log("media relay started on http://%s:%s" % (ip, port))
    return server


def stop_media_relay(server):
    remove_file(MEDIA_RELAY_STATE_PATH)
    if server is None:
        return
    try:
        server.shutdown()
    except Exception:
        pass
    try:
        server.server_close()
    except Exception:
        pass
    log("media relay stopped")


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
    relay_server = start_media_relay()
    last_relay_cleanup = 0
    log("started")

    try:
        while not monitor.abortRequested():
            if monitor.waitForAbort(5):
                break

            now = int(time.time())
            if now - last_relay_cleanup > 60:
                cleanup_media_relay_registry()
                last_relay_cleanup = now

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
    finally:
        stop_media_relay(relay_server)
        log("stopped")


if __name__ == "__main__":
    main()

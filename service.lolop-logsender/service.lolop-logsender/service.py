# -*- coding: utf-8 -*-
"""Optional startup sender for Lolop Log Sender."""

import xbmc
import xbmcaddon

from default import ADDON_ID, send_logs


ADDON = xbmcaddon.Addon()


def setting_bool(name, default=False):
    try:
        return ADDON.getSettingBool(name)
    except Exception:
        value = ADDON.getSetting(name)
        if value == "":
            return default
        return str(value).lower() == "true"


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[%s service] %s" % (ADDON_ID, message), level)


def main():
    monitor = xbmc.Monitor()
    if not setting_bool("auto_send_on_startup", False):
        return
    if monitor.waitForAbort(20):
        return
    try:
        response = send_logs(notes="Automatic Kodi startup log upload", show_dialog=False)
        log("Startup logs sent as %s" % response.get("log_id", "unknown"))
    except Exception as exc:
        log("Startup log upload failed: %s" % exc, xbmc.LOGWARNING)


if __name__ == "__main__":
    main()

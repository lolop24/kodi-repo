# -*- coding: utf-8 -*-
"""Normalize addon_data settings so Kodi 21 can read them on LibreELEC."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcvfs


try:
    translatePath = xbmcvfs.translatePath
except AttributeError:  # pragma: no cover
    translatePath = xbmc.translatePath


ADDON_ID = "service.subtitles.titdeepl-localsub"
SOURCE_LANGUAGE_MAP = {
    "": "0",
    "0": "0",
    "auto": "0",
    "1": "1",
    "cs": "1",
    "czech": "1",
    "2": "2",
    "sk": "2",
    "slovak": "2",
}


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[%s] %s" % (ADDON_ID, message), level)


def normalize_source_language(value):
    normalized = str(value or "").strip().lower()
    return SOURCE_LANGUAGE_MAP.get(normalized, str(value or "").strip())


def migrate_settings():
    try:
        addon = xbmcaddon.Addon()
        profile_dir = translatePath(addon.getAddonInfo("profile"))
    except Exception as exc:
        log("Settings migrator: addon profile is not available yet: %s" % exc, xbmc.LOGWARNING)
        return

    settings_path = os.path.join(profile_dir, "settings.xml")
    if not os.path.isfile(settings_path):
        return

    try:
        tree = ET.parse(settings_path)
    except Exception as exc:
        log("Settings migrator: could not parse user settings: %s" % exc, xbmc.LOGWARNING)
        return

    root = tree.getroot()
    if root.tag != "settings":
        return

    changed = False
    if root.get("version") != "2":
        root.set("version", "2")
        changed = True

    for setting in root.findall("setting"):
        if setting.get("id") == "source_language":
            normalized = normalize_source_language(setting.text)
            if setting.text != normalized:
                setting.text = normalized
                changed = True

    if not changed:
        return

    try:
        tree.write(settings_path, encoding="utf-8", xml_declaration=False)
        log("Settings migrator: normalized addon_data/settings.xml to version 2")
    except Exception as exc:
        log("Settings migrator: could not write user settings: %s" % exc, xbmc.LOGWARNING)


if __name__ == "__main__":
    migrate_settings()

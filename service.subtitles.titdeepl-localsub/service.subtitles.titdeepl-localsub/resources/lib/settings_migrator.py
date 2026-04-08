# -*- coding: utf-8 -*-
"""Normalize legacy addon_data settings so Kodi can read them on LibreELEC."""

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
ADDON = xbmcaddon.Addon(ADDON_ID)
PROFILE_DIR = translatePath(ADDON.getAddonInfo("profile"))
SETTINGS_PATH = os.path.join(PROFILE_DIR, "settings.xml")
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
    if not os.path.isfile(SETTINGS_PATH):
        return

    try:
        tree = ET.parse(SETTINGS_PATH)
    except Exception as exc:
        log("Settings migrator: could not parse user settings: %s" % exc, xbmc.LOGWARNING)
        return

    root = tree.getroot()
    if root.tag != "settings":
        return

    changed = False
    if root.get("version") is not None:
        root.attrib.pop("version", None)
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
        tree.write(SETTINGS_PATH, encoding="utf-8", xml_declaration=False)
        log("Settings migrator: normalized addon_data/settings.xml")
    except Exception as exc:
        log("Settings migrator: could not write user settings: %s" % exc, xbmc.LOGWARNING)


if __name__ == "__main__":
    migrate_settings()

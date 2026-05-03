# -*- coding: utf-8 -*-

import hashlib
import json
import os
import time
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


ADDON = xbmcaddon.Addon()
TARGET_ADDON_ID = 'plugin.video.stream-cinema'
EXPORT_FILE = 'stream_cinema_credentials.json'

CORE_FIELDS = [
    'kraska.user',
    'kraska.pass',
    'kraska.token',
    'kraska.chsum',
    'system.uuid',
    'system.auth_token',
    'system.auth_token_updated',
]

EXTRA_FIELDS = [
    'kra_token',
    'kra_chsum',
    'kruser',
    'krpass',
    'ws_token',
    'wsuser',
    'wspass',
    'trakt.authorization',
    'trakt.token',
]

ALL_FIELDS = CORE_FIELDS + EXTRA_FIELDS


def L(string_id):
    return ADDON.getLocalizedString(string_id)


def log(message, level=xbmc.LOGINFO):
    xbmc.log('[SC Credentials Transfer] %s' % message, level)


def translate(path):
    return xbmcvfs.translatePath(path)


def profile_dir(addon_id):
    path = translate('special://profile/addon_data/%s' % addon_id)
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def target_settings_path():
    return os.path.join(profile_dir(TARGET_ADDON_ID), 'settings.xml')


def export_path():
    return os.path.join(profile_dir(ADDON.getAddonInfo('id')), EXPORT_FILE)


def load_settings_xml(path):
    if xbmcvfs.exists(path):
        try:
            return ET.parse(path).getroot()
        except Exception as exc:
            log('Failed to parse %s: %s' % (path, exc), xbmc.LOGWARNING)
    return ET.Element('settings')


def read_setting(root, setting_id):
    for element in root.findall('setting'):
        if element.get('id') == setting_id:
            return element.text or ''
    return ''


def write_settings(path, values):
    root = load_settings_xml(path)
    by_id = {
        element.get('id'): element
        for element in root.findall('setting')
        if element.get('id')
    }

    for setting_id, value in values.items():
        element = by_id.get(setting_id)
        if element is None:
            element = ET.SubElement(root, 'setting', {'id': setting_id})
        element.text = value

    tree = ET.ElementTree(root)
    tree.write(path, encoding='utf-8', xml_declaration=True)


def target_addon_version():
    try:
        return xbmcaddon.Addon(TARGET_ADDON_ID).getAddonInfo('version')
    except Exception:
        return ''


def checksum_credentials(username, password):
    if not username or not password:
        return ''
    raw = '%s|%s' % (password, username)
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def values_from_helper_settings():
    values = {}
    for field in ALL_FIELDS:
        value = ADDON.getSetting(field).strip()
        if value:
            values[field] = value

    if 'kraska.chsum' not in values:
        checksum = checksum_credentials(values.get('kraska.user', ''), values.get('kraska.pass', ''))
        if checksum:
            values['kraska.chsum'] = checksum

    if values.get('system.auth_token') and 'system.auth_token_updated' not in values:
        version = target_addon_version()
        if version:
            values['system.auth_token_updated'] = version

    return values


def export_current():
    root = load_settings_xml(target_settings_path())
    values = {}
    for field in ALL_FIELDS:
        value = read_setting(root, field)
        if value:
            values[field] = value

    if not values:
        xbmcgui.Dialog().notification(L(30001), L(30008), xbmcgui.NOTIFICATION_WARNING)
        return

    payload = {
        'addon': TARGET_ADDON_ID,
        'exported_at': int(time.time()),
        'settings': values,
    }
    with open(export_path(), 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)

    xbmcgui.Dialog().ok(L(30001), L(30009) % export_path())


def import_exported():
    path = export_path()
    if not xbmcvfs.exists(path):
        xbmcgui.Dialog().notification(L(30001), L(30010) % path, xbmcgui.NOTIFICATION_WARNING)
        return

    with open(path, 'r', encoding='utf-8') as handle:
        payload = json.load(handle)

    values = payload.get('settings', {})
    values = {key: str(value) for key, value in values.items() if key in ALL_FIELDS and value}
    if not values:
        xbmcgui.Dialog().notification(L(30001), L(30008), xbmcgui.NOTIFICATION_WARNING)
        return

    write_settings(target_settings_path(), values)
    xbmcgui.Dialog().ok(L(30001), L(30011) % len(values))


def apply_manual():
    values = values_from_helper_settings()
    if not values:
        xbmcgui.Dialog().notification(L(30001), L(30012), xbmcgui.NOTIFICATION_WARNING)
        return

    write_settings(target_settings_path(), values)
    xbmcgui.Dialog().ok(L(30001), L(30011) % len(values))


def open_settings():
    ADDON.openSettings()


def main():
    actions = [
        (L(30002), apply_manual),
        (L(30003), export_current),
        (L(30004), import_exported),
        (L(30005), open_settings),
    ]
    choice = xbmcgui.Dialog().select(L(30001), [label for label, _ in actions])
    if choice < 0:
        return
    actions[choice][1]()


if __name__ == '__main__':
    main()

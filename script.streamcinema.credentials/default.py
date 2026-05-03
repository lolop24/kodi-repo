# -*- coding: utf-8 -*-

import hashlib
import hmac
import json
import os
import time
import xml.etree.ElementTree as ET
import base64

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


ADDON = xbmcaddon.Addon()
TARGET_ADDON_ID = 'plugin.video.stream-cinema'
EXPORT_FILE = 'stream_cinema_credentials.json'
PERSONAL_CREDENTIAL_BLOB = (
    'eyJjdCI6IjhLdSsrMzBlaGdTSVphWVhuYlhPc1RLcmFvR3pqUWphajIzVFRvK2xySHIwcUx0VjNLb2V3Y3p0MG9CaFVnbWhDbWZ2bEVYS2JERVVtTDhGSWcwdjRPU1hzaHRIQnM2aUhiVDdXMktjQ3l6YU93azRYVmw0UGV1dURsYWszTUtWZjlGVFpaNkU4WkV5a001aHUxZ1Ywbzg4dVYyTW1rTU84dWw0UGhGcmUzSmUyN3lSUUpaVHF1OUdtUld1VExCTzljVlF1ZmN1cjVDeGxienFJems5SkZwdmxXUnhQRXUzdXV2bi9RTTNIZEloUG4ybmZGbkV2SVdwZG53QTlDNjV5bldkWTg4bEgvOTJuckRkMHl0d1ljdnBtUzRob3paNlBMNVhSU0lUVVNwSHBwTSswdEVQQ2gxVzh4blEreUNDSXY0L0dTUi9Pc2l1QmZtbTlnV1JObWIwaExxK010M0NvMU9iNEtCR3d0YTRGQjRWTXNtRkg3dGUiLCJpdGVyYXRpb25zIjoyMDAwMDAsImtkZiI6InBia2RmMi1zaGEyNTYiLCJub25jZSI6ImpXY1B5ck5oRXBwMDhlT2VsV0hVa3c9PSIsInNhbHQiOiJlL3QrZnVBZzhFZURXWC81S3JOTDNBPT0iLCJ0YWciOiJlRVBycGRGaUxTM25tT0pPUm14Vm9qeDFpcC9XRnl0K3owUWZSMnAxMkFFPSIsInYiOjF9'
)

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


def _b64decode(value):
    return base64.b64decode(value.encode('ascii'))


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
            value = element.get('value')
            return value if value is not None else (element.text or '')
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
        if element.get('value') is not None:
            element.set('value', value)
            element.text = None
        else:
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


def _derive_personal_key(username, password, salt, iterations):
    material = (username + '\0' + password).encode('utf-8')
    return hashlib.pbkdf2_hmac('sha256', material, salt, iterations, dklen=32)


def _xor_crypt(payload, key, nonce):
    stream = bytearray()
    counter = 0
    while len(stream) < len(payload):
        stream.extend(hashlib.sha256(key + nonce + counter.to_bytes(4, 'big')).digest())
        counter += 1
    return bytes([left ^ right for left, right in zip(payload, stream)])


def decrypt_personal_blob(username, password):
    if not PERSONAL_CREDENTIAL_BLOB:
        return None
    try:
        blob = json.loads(_b64decode(PERSONAL_CREDENTIAL_BLOB).decode('utf-8'))
        salt = _b64decode(blob['salt'])
        nonce = _b64decode(blob['nonce'])
        ciphertext = _b64decode(blob['ct'])
        tag = _b64decode(blob['tag'])
        key = _derive_personal_key(username, password, salt, int(blob['iterations']))
        expected_tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected_tag):
            return None
        payload = _xor_crypt(ciphertext, key, nonce)
        decoded = json.loads(payload.decode('utf-8'))
        return decoded.get('settings', {})
    except Exception as exc:
        log('Personal credential blob failed: %s' % exc, xbmc.LOGWARNING)
        return None


def ask_credentials():
    dialog = xbmcgui.Dialog()
    username = dialog.input(L(30040), '')
    if not username:
        return None, None
    input_type = getattr(xbmcgui, 'INPUT_ALPHANUM', 0)
    hidden = getattr(xbmcgui, 'ALPHANUM_HIDE_INPUT', 0)
    password = dialog.input(L(30041), '', input_type, hidden)
    if not password:
        return None, None
    return username.strip(), password


def apply_personal_blob():
    username, password = ask_credentials()
    if not username or not password:
        return

    values = decrypt_personal_blob(username, password)
    if not values:
        xbmcgui.Dialog().notification(L(30001), L(30042), xbmcgui.NOTIFICATION_ERROR)
        return

    checksum = checksum_credentials(username, password)
    values = {key: str(value) for key, value in values.items() if key in ALL_FIELDS and value}
    values.update({
        'kraska.user': username,
        'kraska.pass': password,
        'kraska.chsum': checksum,
        'kruser': username,
        'krpass': password,
        'wsuser': username,
        'wspass': password,
        'kra_chsum': checksum,
    })

    if values.get('system.auth_token') and 'system.auth_token_updated' not in values:
        version = target_addon_version()
        if version:
            values['system.auth_token_updated'] = version

    write_settings(target_settings_path(), values)
    xbmcgui.Dialog().ok(L(30001), L(30043) % len(values))


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
    if PERSONAL_CREDENTIAL_BLOB:
        apply_personal_blob()
        return

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

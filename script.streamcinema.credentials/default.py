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
STREAM_CINEMA_ADDON_ID = 'plugin.video.stream-cinema'
TITDEEPL_ADDON_ID = 'service.subtitles.titdeepl-localsub'
TARGET_ADDON_ID = STREAM_CINEMA_ADDON_ID
EXPORT_FILE = 'stream_cinema_credentials.json'
PERSONAL_CREDENTIAL_BLOB = (
    'eyJjdCI6IjVlK2hnUDNESjkxQmpwWmFwK3BDU2tZalk2TzkyanBONVpoYmZRaHlMNVY4bmhOMitlNUV6Ym1wbkROZzBjNnl1QU4yU29HbEpGQytVRXpaaHVnK1FtRE1XbUlqdlp3TzBPbmNzcUd3SDN1ZDRDZDJBa0J6SHVBdFphK0cvdVNTN1ljQktxcVFoYkgzMnVEa3hER1RaYVpKUGV6WFJZWGdSWm8wZ0ZvTEl6VHBHS0tFT0l5Y1VicG9pRkpmbGxXMDN6RUxObGFxd1VCNjBQZXZlNGRtTjZUWGxTNkI0allpNnVLemRkcnAxU2xvbEk3b3Y1QXphVGJVSGU0KzVFdUtmUDRRcnRwcCt6YmM1czhlcXhkVTkxN2hzdWJ0d1Y5djl6anJ0Z0ZINjRqdXVEWjdUb1lpOEh0WU92UEhlb21HdnBrbFlGMGRtb2ZSYm1nVmFyR2ZlemFTUDEvUEdQb21FOTIyZlNLSUZwUDNGS3VZME9hWDlidnYzN1lCdGw4SW0vdmY0MXdRMlY3NzZGOXR4d3dSQVlFU1VRcGFEblBNenltNkhHT1RBM0EzYWxxTlhydnJYRW9JVUF2bC9uUldUcDN1azFZMnZJMXZYK1ZISXUxZ1ZmYURqNUNvdkVRck1MdlNkTEZzWlhmNDRRbFdtSkZFVVpFUGxFTGFKd0RRalpJR0ZLZFdFbHVZR2ZYUkxESERhV2pma1dZckJaV0I2RlBPQ1JNTW9hK1FZWEMwai9aSjJHMUh3ZHN5VGVXMlY4cTF5TDlOMFJJQTBsUGZNYkw5cEZYTEFNQVIxd0JxWVpHK0dHNEh3S1RWY1h0cTAvQkRGZEd2YzdZa2k5UmsyV0NaaGorVkxqcHZLK01ra0hhb2JkMFk1MFcvWm9JdWVoaHZHclBsSTNIOGlIcHNINEhiWHo5eXA5SDV2SnBDZE5LRzl0bXNDb2M5TFVGSmRteTFqeHRnVStLR0VXTVB5N2VnRjVudmZyem9PcFArTGZxZGg0VHkyTS9aelZocW5kU1lrTXdtNE5rbUltem4yeTY2dUdkUzBMeGFDTWhJYSs4b3VGcE9wb0o3UEhZT3JId2FvQ1U1RzZRMFFHV2NUWUN4YW1qZWpzcjRlSjlCMkJEQktmblQ4dmowaStmaG13UVAwKzlQTXNKUzlXRVBwVkVBVDNaZndaczRyTU11UEJGL01Ma29nRFlObXZRNFhsdytaMkRHR1JMazBPZG5iNE1XdHFPM3FidEJjWTVLbkhra200ZXl0aVdSd2R0RFd1Ui9ZVGRDZVRCZWRIVnlpM2I2T2hPK0xiVi9NcUFaTzdXTjB1R0VaOVVla0VrY29FbWNiRGhHSjBSWkhTcmx5Unprc2RxT1lWRWNoM2taRkp0eXA1cmh6RkpUeVRDQ3lpaU9nNklGajEvcDVoUGUvRnhYdnpycVZsQlFDR3dZY0JzNGZjb0JBY2hPZXI5NkM2V3pFSjRSbWFxcjlQZVpzMk5VMi95dXZQQSszSG4zcFJ3emFnR1hETW9Cd2Rvb2NGVCs4bytBdDN2OWY4MERBdUdPVkR5L1E2U0FwSjNsYnplVEh0UnpMVWR0QWczMnprcndCLy9td3hvZnpEVWZBaGYzaWMrU1Evcis5TTlZUFhCUEdzNFF3WUc1a0hGTm45YjhLcFpnU3I3TVFqdHJqQ05qR204YmpTUXcvRXowOVdNK29FOFVwZ3dYMkJJbWNmcGtnaXhtbjZ0R3BGUkpadUZ2a2kxd1RCa1g2K1dta3FUazFlOEZYQkk2Y1IxL0lCQnkzSUdwNkdaL0tjVkMzV2NLRGI5M01ma2RpYVp1MDlwOUVxYm9Jc3p0M0pGeXdzK0pJUHlpdkFDNE0rSFJ4WE5WT1lFMWhUajFNZStPZWRyTjF3MVM2UlZ6RUZNaklLRUF0R2hnSWx1NzRnL3lRdTA3ZWpNK0o0ZzNXVkdrNktKc2JXbzNTNzNkZFY2NEtUa0pmOFlLenVIRVFJYmVmWEFjc3VPejBGQkFoV25TaUhHREVraGY5TUUxMDA3NGFSanlTYXZOWEl4VkRhWnJyZ0xuU2FqaWdSbmkwNnE0WERBNi81TGVvZlFoc3d6cnczbFFiaTBDNmFUTkN1WEdMVGJpa0RvcTAxV3hweVFYODNwc2VIMWRnMXRPNnRLMXY0UUtMamJBYUhpTDUxd1FoNVh3MmFLMERlRGJQM2Y0STY2czN3UGJwVktLV2tCNWo0dGI3ZFFZL2twSzZUR2QrRm0zOHh1NnJzMHlRZXlDKzB0Y1ZrUFo2RnoraTVUUVRaY0F6L2tQV2VUVXBhMUVIeDVnVlgzL0ZqVm8xYTArZFVnWkNBVHpwN01hTVBkcWVUYWVtVXlOVDVlQVdJSTlaMVgwMC9DRHZzNnFJdmRsVEJ5b25WbkliSlRUWE54Wk9vb2g1OG50TVdxZTdFbVFzNXVwYnRZSlZrWjZjZGl1V2hDTjlGNzc1S2FxVFdlaFNPak5ObXEzOEMzOHFIb2dzbFgzRFpwVG55OTlMSG9lOUF0QkFQN3JqdHVDYkRXdmd0Mm9UL3V2UlVMUkZxRVpLY0JPSXdtV3RTZzV3bTk0V25ZZ0F2ek5YWXY3QUk5QWdlaDJpcWFuMkcrQzJvTU0vKzdRK1cxcG82REhQejk2OWVKekN0STh3TVdtZmoyb2E4U1RhMzNBb3NyeGFDeFFqN3hXeTMySE4waEJCY2JYRFoyNm1RSzY3K0NvNWZnZlhpU0FMd2E4ZWdGTFpJRTZTMkFrV0V5V1pIdmxFSDNMZXRuWTVKREZBYTVENjlRZyt5Q01pMnlYejZ1aWRMTEhsU0lpZ3YvVldCbkwzamdmMkRTb1NtQmxsc1BRQkhFZERYRjNHbTF1K1FROHNMcXIrT3FVSUZCbEIrU2pxV0JBZHBSQ2IyRDNSQ2NjaGNuZ2p4QnFZRmFjUGtLMmFUd1BCK2g2T3pLNGFqZ3FnTEJPeGNUc0pyclQxOVBwa0pQUDRVYnNlMUkya0xBT1pmNkpLdHFJN0djZnhsUDlTblVHQmd2Zks2ekpxUmU4SUJDcElGb2cyWmlXajd1SmlFN1RaR2NyVDJML2RlNlEyWlFZZlhabnJtYmRuU1Q2cThqR2VHYzA1MXEzT29JZ1Y4THRYMElESklzWkZBY1pmVG5JUzd3cXB1OEN0V2lnRnhqRjJzM3JrSlVPRW9EdWMxYWhTN3d0RWdzQ1djRWl4Z1lxNWp1WVpkdVFuQTJwU0htT2tqeWZJd3d6OXpjOEx1bGZmU2hldmxaeFJKeXl1UTdqeEZYVUJrSVhjdERxWGdzS0N6WlRzczNyOVQzWHI2YklRZitJb2ppU3I3YkpyT0p0ZG9vWEd3ZU54NXEvS1oxczRDU3J6STByaFVGN0lqOWd0WmFxM1RIdUJTSDZuRjUzR3RLc3Y1NnJsR3FGV1F2Y3YwNldsdHNuNUk4VjR6K01yZkRSVmFwYm1CS0RoeUVKcVlpNTFXSndiaEc5R0UrZnh0eGp5bHRGV1dkbExaY2tOV1laaEJKdVBHYm1TUEtDZDVUcjErOUNQZjJRelIwZjdjV1JqRmZGL3V4cU1KbEsrYVFFQXRDcWU1NXBDdm9NSDRkNlA5djRIV2ZUdFB5QmwwMFRXSWJyZFl1a1BENjJNdz09IiwiaXRlcmF0aW9ucyI6MjAwMDAwLCJrZGYiOiJwYmtkZjItc2hhMjU2Iiwibm9uY2UiOiJVdUNaNmY3YURha2hrMkIxMlE3TU5RPT0iLCJzYWx0IjoieGxKOVJ2VlJsQWptdFZ1eExkTGsxdz09IiwidGFnIjoiNVhhRmRDM21jbll0RXNnaGljS0NNcmtoZXR5WWlmY1FoVzlSbU9xWU1FND0iLCJ2IjoyfQ=='
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
STREAM_CINEMA_USERNAME_FIELDS = ['kraska.user', 'kruser', 'wsuser']
STREAM_CINEMA_PASSWORD_FIELDS = ['kraska.pass', 'krpass', 'wspass']
STREAM_CINEMA_CHECKSUM_FIELDS = ['kraska.chsum', 'kra_chsum']
TITDEEPL_USERNAME_FIELDS = ['os_username']
TITDEEPL_PASSWORD_FIELDS = ['os_password']


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


def settings_path(addon_id):
    return os.path.join(profile_dir(addon_id), 'settings.xml')


def target_settings_path():
    return settings_path(TARGET_ADDON_ID)


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


def target_addon_version(addon_id=TARGET_ADDON_ID):
    try:
        return xbmcaddon.Addon(addon_id).getAddonInfo('version')
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
        if decoded.get('addons'):
            return decoded
        return {
            'v': decoded.get('v', 1),
            'addons': {
                STREAM_CINEMA_ADDON_ID: decoded.get('settings', {}),
            },
        }
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


def clean_payload_values(values):
    if not isinstance(values, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in values.items()
        if key and value is not None and str(value) != ''
    }


def add_entered_credentials(values, username, password, username_fields, password_fields):
    for field in username_fields:
        values[field] = username
    for field in password_fields:
        values[field] = password


def prepare_stream_cinema_values(values, username, password):
    prepared = clean_payload_values(values)
    add_entered_credentials(
        prepared,
        username,
        password,
        STREAM_CINEMA_USERNAME_FIELDS,
        STREAM_CINEMA_PASSWORD_FIELDS,
    )

    checksum = checksum_credentials(username, password)
    if checksum:
        for field in STREAM_CINEMA_CHECKSUM_FIELDS:
            prepared[field] = checksum

    if prepared.get('system.auth_token') and 'system.auth_token_updated' not in prepared:
        version = target_addon_version(STREAM_CINEMA_ADDON_ID)
        if version:
            prepared['system.auth_token_updated'] = version

    return prepared


def prepare_titdeepl_values(values, username, password):
    prepared = clean_payload_values(values)
    add_entered_credentials(
        prepared,
        username,
        password,
        TITDEEPL_USERNAME_FIELDS,
        TITDEEPL_PASSWORD_FIELDS,
    )
    return prepared


def prepare_personal_values(addon_id, values, username, password):
    if addon_id == STREAM_CINEMA_ADDON_ID:
        return prepare_stream_cinema_values(values, username, password)
    if addon_id == TITDEEPL_ADDON_ID:
        return prepare_titdeepl_values(values, username, password)
    return clean_payload_values(values)


def apply_personal_blob():
    username, password = ask_credentials()
    if not username or not password:
        return

    payload = decrypt_personal_blob(username, password)
    addons = payload.get('addons', {}) if payload else {}
    if not addons:
        xbmcgui.Dialog().notification(L(30001), L(30042), xbmcgui.NOTIFICATION_ERROR)
        return

    applied_addons = 0
    applied_settings = 0
    for addon_id, values in addons.items():
        prepared = prepare_personal_values(addon_id, values, username, password)
        if not prepared:
            continue
        write_settings(settings_path(addon_id), prepared)
        applied_addons += 1
        applied_settings += len(prepared)

    if not applied_addons:
        xbmcgui.Dialog().notification(L(30001), L(30008), xbmcgui.NOTIFICATION_WARNING)
        return

    xbmcgui.Dialog().ok(L(30001), L(30044) % (applied_settings, applied_addons))


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

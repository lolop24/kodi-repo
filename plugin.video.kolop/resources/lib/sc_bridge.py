# -*- coding: utf-8 -*-
"""
Helpers for building Stream Cinema search URLs and deriving stable search titles.
"""

import re
import unicodedata
from urllib.parse import urlencode

SC_ADDON_ID = 'plugin.video.stream-cinema'

CZ_SK = {'cesko', 'slovensko', 'ceskoslovensko', 'cssr'}
SERIE_SUFFIX = re.compile(r'\s*[-–]\s*(?:S[eé]ri[ea]|Season|Series)\s+\d+\s*$', re.IGNORECASE)


def _hexlify(value):
    return str(value).encode('utf-8').hex()


def _normalize_text(value):
    normalized = unicodedata.normalize('NFKD', str(value or ''))
    normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
    return normalized.lower().strip()


def _strip_serie_suffix(title):
    return SERIE_SUFFIX.sub('', str(title or '')).strip()


def build_sc_search_url(title, media_type='F'):
    search_id = 'search-series' if media_type == 'S' else 'search-movies'
    params = {
        'action': _hexlify('search_from_history'),
        'id': _hexlify(search_id),
        'search': _hexlify(title),
    }
    sorted_params = sorted(params.items(), key=lambda item: item[0])
    return 'plugin://%s/?%s' % (SC_ADDON_ID, urlencode(sorted_params))


def get_search_title(item, is_serie=False):
    country = _normalize_text(item.get('country', ''))
    is_czsk = any(name in country for name in CZ_SK)

    if is_czsk:
        title = item.get('title', '')
    else:
        title = item.get('english_title') or item.get('original_title') or item.get('title', '')

    if is_serie:
        title = _strip_serie_suffix(title)

    return title.strip()

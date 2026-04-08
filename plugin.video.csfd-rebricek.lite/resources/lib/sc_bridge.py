# -*- coding: utf-8 -*-
"""
Stream Cinema bridge v15 – SC search URL builder only.

No automatic SC API calls. Click on item → opens SC search.
Série items: strip "- Série X" suffix and search as seriál.
"""

import re
from urllib.parse import urlencode

SC_ADDON_ID = 'plugin.video.stream-cinema'

CZ_SK = {'česko', 'slovensko', 'československo', 'čssr'}

# Matches " - Série 6", " - Season 2", " - Séria 1", " - Series 3" etc.
_SERIE_SUFFIX = re.compile(r'\s*[-–]\s*(?:Séri[ea]|Season|Series)\s+\d+\s*$', re.IGNORECASE)


def _hexlify(value):
    return str(value).encode('utf-8').hex()


def _strip_serie_suffix(title):
    """Remove série/season suffix to get the base serial name."""
    return _SERIE_SUFFIX.sub('', title)


def build_sc_search_url(title, media_type='F'):
    """Build Kodi plugin URL that opens SC search for given title.

    media_type: 'F' = film (search-movies), 'S' = seriál/série (search-series)
    """
    search_id = 'search-series' if media_type == 'S' else 'search-movies'
    params = {
        'action': _hexlify('search_from_history'),
        'id': _hexlify(search_id),
        'search': _hexlify(title),
    }
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    return 'plugin://%s/?%s' % (SC_ADDON_ID, urlencode(sorted_params))


def get_search_title(item, is_serie=False):
    """Best search title for SC.

    For série: strips "- Série X" suffix so SC finds the base serial.
    English title for foreign, Czech title for CZ/SK.
    Priority: english_title > original_title > title
    """
    country = item.get('country', '').lower().strip()
    is_czsk = any(c in country for c in CZ_SK)

    if is_czsk:
        title = item['title']
    else:
        eng = item.get('english_title', '')
        orig = item.get('original_title', '')
        title = eng or orig or item['title']

    if is_serie:
        title = _strip_serie_suffix(title)

    return title

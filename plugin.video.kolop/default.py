# -*- coding: utf-8 -*-

import os
import sys
from urllib.parse import parse_qs, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_URL = sys.argv[0]
ADDON_HANDLE = int(sys.argv[1])
ADDON_ARGS = sys.argv[2].lstrip('?') if len(sys.argv) > 2 else ''

LIB_PATH = os.path.join(
    xbmcvfs.translatePath(ADDON.getAddonInfo('path')),
    'resources', 'lib'
)
if LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)

from sc_bridge import build_sc_search_url, get_search_title  # noqa: E402
from sc_csfd import (  # noqa: E402
    MAIN_MENU_SOURCES,
    SOURCE_CONFIG,
    build_source_items,
    fetch_single_reviews,
    get_source_content,
    get_source_label,
    prewarm_sources,
    resolve_source_key,
)

STAR_FULL = '\u2605'
STAR_EMPTY = '\u2606'


def L(string_id):
    return ADDON.getLocalizedString(string_id)


def log(message, level=xbmc.LOGINFO):
    xbmc.log('[Kolop] %s' % message, level)


def build_url(**params):
    return '%s?%s' % (ADDON_URL, urlencode(params))


def stars_str(count):
    count = max(0, min(5, int(count or 0)))
    return STAR_FULL * count + STAR_EMPTY * (5 - count)


def main_menu():
    xbmcplugin.setPluginCategory(ADDON_HANDLE, L(30001))
    xbmcplugin.setContent(ADDON_HANDLE, 'videos')

    for source_key in MAIN_MENU_SOURCES:
        content = get_source_content(source_key)
        mediatype = 'tvshow' if content == 'tvshows' else 'movie'
        default_thumb = 'DefaultTVShows.png' if mediatype == 'tvshow' else 'DefaultMovies.png'
        li = xbmcgui.ListItem(label=get_source_label(source_key))
        li.setInfo('video', {'title': get_source_label(source_key), 'mediatype': mediatype})
        li.setArt({'icon': default_thumb, 'thumb': default_thumb})
        xbmcplugin.addDirectoryItem(
            ADDON_HANDLE,
            build_url(action='source', source=source_key),
            li,
            isFolder=True
        )

    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


def _format_label(item):
    parts = []
    if item.get('position'):
        parts.append('%s.' % item['position'])
    parts.append(item.get('title', ''))
    if item.get('year'):
        parts.append('(%s)' % item['year'])
    if item.get('csfd_rating'):
        parts.append('[COLOR orange]%s[/COLOR]' % item['csfd_rating'])
    if item.get('csfd_votes'):
        parts.append('[COLOR grey][%s][/COLOR]' % item['csfd_votes'])
    return ' '.join([part for part in parts if part])


def _build_info(item):
    info = {
        'title': item.get('title', ''),
        'mediatype': item.get('mediatype', 'movie'),
    }

    if item.get('year'):
        try:
            info['year'] = int(item['year'])
        except (TypeError, ValueError):
            pass

    if item.get('csfd_rating_num') is not None:
        info['rating'] = float(item['csfd_rating_num']) / 10.0
    elif item.get('sc_rating') is not None:
        info['rating'] = float(item['sc_rating'])

    if item.get('csfd_votes_int') is not None:
        info['votes'] = int(item['csfd_votes_int'])

    if item.get('genres'):
        info['genre'] = item['genres']
    elif item.get('sc_genre'):
        info['genre'] = item['sc_genre']

    if item.get('country'):
        info['country'] = item['country']
    elif item.get('sc_country'):
        info['country'] = item['sc_country']

    plot = item.get('plot') or item.get('sc_plot') or ''
    if plot:
        info['plot'] = plot
        info['plotoutline'] = plot[:240]

    if item.get('original_title'):
        info['originaltitle'] = item['original_title']

    if item.get('tvshowtitle'):
        info['tvshowtitle'] = item['tvshowtitle']

    return info


def _build_art(item):
    art = {}
    poster = item.get('poster') or item.get('sc_poster')
    fanart = item.get('fanart') or item.get('sc_fanart') or poster
    if poster:
        art['poster'] = poster
        art['thumb'] = poster
        art['icon'] = poster
    if fanart:
        art['fanart'] = fanart
    return art


def _build_target_url(item):
    sc_url = item.get('sc_url', '')
    if sc_url:
        return sc_url
    is_series = item.get('search_media') == 'S'
    search_title = get_search_title(item, is_serie=is_series) or item.get('original_title') or item.get('title', '')
    return build_sc_search_url(search_title, item.get('search_media', 'F'))


def show_source(source_key, silent=False, item_limit=None):
    resolved_source = resolve_source_key(source_key)
    if resolved_source not in SOURCE_CONFIG:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False, cacheToDisc=False)
        return

    xbmcplugin.setPluginCategory(ADDON_HANDLE, get_source_label(resolved_source))
    xbmcplugin.setContent(ADDON_HANDLE, get_source_content(resolved_source))

    progress = None
    if not silent:
        progress = xbmcgui.DialogProgress()
        progress.create(L(30001), L(30007))

    def progress_cb(done, total):
        if not progress or total <= 0:
            return False
        if progress.iscanceled():
            return True
        percent = min(100, 10 + int((90 * done) / total))
        progress.update(percent, L(30008) % (done, total))
        return progress.iscanceled()

    try:
        items = build_source_items(
            resolved_source,
            progress_cb=None if silent else progress_cb,
            item_limit=item_limit
        )
    finally:
        if progress:
            progress.close()

    if not items:
        if not silent:
            xbmcgui.Dialog().notification(L(30001), L(30006), xbmcgui.NOTIFICATION_WARNING)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=True, cacheToDisc=False)
        return

    for item in items:
        li = xbmcgui.ListItem(label=_format_label(item))
        li.setInfo('video', _build_info(item))
        art = _build_art(item)
        if art:
            li.setArt(art)

        if item.get('csfd_url'):
            reviews_url = build_url(action='reviews', csfd_url=item['csfd_url'], title=item.get('title', ''))
            review_count = len(item.get('reviews', []))
            label = L(30004) % review_count if review_count else L(30005)
            li.addContextMenuItems([(label, 'RunPlugin(%s)' % reviews_url)])

        xbmcplugin.addDirectoryItem(
            ADDON_HANDLE,
            _build_target_url(item),
            li,
            isFolder=item.get('is_folder', True)
        )

    xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=True, cacheToDisc=False)


def show_reviews(csfd_url, title):
    reviews = fetch_single_reviews(csfd_url)
    if not reviews:
        xbmcgui.Dialog().notification(L(30001), L(30005), xbmcgui.NOTIFICATION_INFO)
        return

    lines = []
    for review in reviews:
        lines.append('-' * 50)
        lines.append('%s  %s' % (stars_str(review.get('stars', 0)), review.get('user', '?')))
        lines.append('')
        lines.append(review.get('text', ''))
        lines.append('')

    xbmcgui.Dialog().textviewer(
        L(30009) % (title, len(reviews)),
        '\n'.join(lines)
    )


def router():
    params = parse_qs(ADDON_ARGS)
    args = {key: values[0] if values else '' for key, values in params.items()}
    action = args.get('action', '')
    limit = args.get('limit', '')

    if action == 'source':
        show_source(args.get('source', 'newstream'), item_limit=limit)
    elif action == 'widget':
        show_source(args.get('source', 'newstream'), silent=True, item_limit=limit)
    elif action == 'prewarm':
        prewarm_sources(item_limit=limit or 25)
    elif action == 'reviews':
        show_reviews(args.get('csfd_url', ''), args.get('title', ''))
    else:
        main_menu()


if __name__ == '__main__':
    router()

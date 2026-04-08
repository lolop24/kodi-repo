# -*- coding: utf-8 -*-
"""
ČSFD Rebríček – Kodi addon (v12.10.0)

Flow:
- Menu: Filmy / Seriály / Série
- List: 30 items per page
  1. Fetch ČSFD details (plot + reviews, parallel, cached 3 days)
  2. Click → SC search by title (série search as seriál)
"""

import os
import sys
from datetime import date, timedelta
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
import xbmcvfs
from urllib.parse import urlencode, parse_qs

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

from csfd import (
    TYPE_FILM, TYPE_SERIES, TYPE_SERIE, ITEMS_PER_PAGE,
    fetch_ranking, fetch_ranking_cached,
    fetch_single_reviews, fetch_single_title_data,
)
from sc_bridge import CZ_SK, build_sc_search_url, get_search_title

STAR_FULL = '★'
STAR_EMPTY = '☆'
RATING_TYPE_ID = 'csfd'
RATING_TYPE_LABEL = 'CSFD'
MEDIA_TYPE_LABELS = {
    TYPE_FILM: 'Film',
    TYPE_SERIES: 'Serial',
    TYPE_SERIE: 'Serie',
}


def L(string_id):
    return ADDON.getLocalizedString(string_id)


def log(msg):
    xbmc.log('[CSFD Lite] %s' % msg, xbmc.LOGINFO)


def _default_year():
    """Dynamic default: today minus 7 months → use that year."""
    d = date.today() - timedelta(days=7 * 30)
    return str(d.year)


def get_year_from():
    try:
        val = ADDON.getSetting('year_from')
        if val:
            val = val.strip()
            if val.isdigit():
                y = int(val)
                if y == 0:
                    return _default_year()
                if 1900 < y <= 2030:
                    return str(y)
    except Exception as e:
        log('getSetting error: %s' % e)
    return _default_year()


def build_url(**params):
    return '%s?%s' % (ADDON_URL, urlencode(params))


def _open_sc_search(film, media_type):
    if media_type == TYPE_FILM:
        mt = 'F'
    else:
        mt = 'S'

    search_item = dict(film)
    country = str(search_item.get('country', '')).lower().strip()
    is_czsk = any(c in country for c in CZ_SK)

    if (not is_czsk and search_item.get('csfd_url')
            and not search_item.get('english_title')
            and not search_item.get('original_title')):
        log('On-demand title lookup for SC search: %s' % search_item.get('title', ''))
        search_item.update(fetch_single_title_data(search_item['csfd_url']))

    search_title = get_search_title(search_item, is_serie=(media_type == TYPE_SERIE))
    log('Opening SC search with title: %s' % search_title)
    xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % build_sc_search_url(search_title, mt))


def stars_str(n):
    n = max(0, min(5, n))
    return STAR_FULL * n + STAR_EMPTY * (5 - n)


def _votes_as_int(value):
    try:
        return int(str(value).replace(' ', '').replace('\xa0', ''))
    except (ValueError, TypeError):
        return None


def _rating_as_float(value):
    try:
        return float(str(value).rstrip('%').replace(',', '.')) / 10.0
    except (ValueError, TypeError):
        return None


def _split_values(value):
    return [part.strip() for part in str(value).split(',') if part.strip()]


def _apply_video_metadata(li, film, media_type):
    is_film = (media_type == TYPE_FILM)
    year = film.get('year', '')
    plot = film.get('plot', '')
    genres = film.get('genres', '')
    countries = film.get('country', '')
    rating_num = _rating_as_float(film.get('rating', ''))
    votes_int = _votes_as_int(film.get('votes', ''))

    tag = li.getVideoInfoTag()
    tag.setTitle(film.get('title', ''))
    tag.setMediaType('movie' if is_film else 'tvshow')
    if year:
        try:
            tag.setYear(int(year))
        except ValueError:
            pass
    if rating_num is not None:
        tag.setRating(rating_num, votes_int or 0, RATING_TYPE_ID, True)
    elif votes_int is not None:
        tag.setVotes(votes_int)
    if genres:
        tag.setGenres(_split_values(genres))
    if countries:
        tag.setCountries(_split_values(countries))
    if plot:
        tag.setPlot(plot)
        tag.setPlotOutline(plot[:200])
    elif film.get('origins_genres'):
        tag.setPlot(film['origins_genres'])

    li.setProperty('rating_type_id', RATING_TYPE_ID)
    li.setProperty('rating_type_label', RATING_TYPE_LABEL)
    li.setProperty('rating_origin', 'csfd.cz')
    li.setProperty('csfd_media_type', film.get('media_type', media_type))
    li.setProperty(
        'csfd_media_type_label',
        MEDIA_TYPE_LABELS.get(film.get('media_type', media_type), '')
    )


# ═══════════════════════════════════════════════════════════════

def main_menu():
    xbmcplugin.setPluginCategory(ADDON_HANDLE, L(30001))
    xbmcplugin.setContent(ADDON_HANDLE, 'videos')

    year = get_year_from()

    li = xbmcgui.ListItem(label='🎬 %s (%s)' % (L(30002), year))
    li.getVideoInfoTag().setTitle(L(30002))
    xbmcplugin.addDirectoryItem(
        ADDON_HANDLE, build_url(action='ranking', mtype=TYPE_FILM, page='1', year=year),
        li, isFolder=True)

    li = xbmcgui.ListItem(label='📺 %s (%s)' % (L(30003), year))
    li.getVideoInfoTag().setTitle(L(30003))
    xbmcplugin.addDirectoryItem(
        ADDON_HANDLE, build_url(action='ranking', mtype=TYPE_SERIES, page='1', year=year),
        li, isFolder=True)

    li = xbmcgui.ListItem(label='📺 %s (%s)' % (L(30004), year))
    li.getVideoInfoTag().setTitle(L(30004))
    xbmcplugin.addDirectoryItem(
        ADDON_HANDLE, build_url(action='ranking', mtype=TYPE_SERIE, page='1', year=year),
        li, isFolder=True)

    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


def show_ranking(media_type, page, year_param=''):
    year_from = year_param if year_param else get_year_from()
    is_film = (media_type == TYPE_FILM)
    if media_type == TYPE_SERIE:
        category = L(30004)
    elif media_type == TYPE_SERIES:
        category = L(30003)
    else:
        category = L(30002)

    log('show_ranking: type=%s year=%s page=%d' % (media_type, year_from, page))

    xbmcplugin.setPluginCategory(ADDON_HANDLE, '%s (%s)' % (category, year_from))
    xbmcplugin.setContent(ADDON_HANDLE, 'movies' if is_film else 'tvshows')

    # 1. Full ranking (cached 2h)
    all_items = fetch_ranking(media_type, year_from)

    if not all_items:
        xbmcgui.Dialog().notification(
            L(30001), L(30005),
            xbmcgui.NOTIFICATION_WARNING)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False, cacheToDisc=False)
        return

    # 2. Paginate
    total = len(all_items)
    start = (page - 1) * ITEMS_PER_PAGE
    end = min(start + ITEMS_PER_PAGE, total)
    page_items = all_items[start:end]

    if not page_items:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False, cacheToDisc=False)
        return

    log('Page %d: items %d-%d of %d' % (page, start + 1, end, total))

    # 3. Fetch ČSFD details (plot + reviews, parallel, cached 3 days)

    # 4. Build list — no SC API calls, search on click only
    for film in page_items:
        title = film['title']
        year = film.get('year', '')
        rating = film.get('rating', '')
        votes = film.get('votes', '')
        poster = film.get('poster_url', '')
        position = film.get('position', '')

        # For SC search: série → search as seriál ('S')
        # Label
        parts = []
        if position:
            parts.append('%s.' % position)
        parts.append(title)
        if year:
            parts.append('(%s)' % year)
        if rating:
            parts.append('[COLOR orange]%s[/COLOR]' % rating)
        if votes:
            parts.append('[COLOR grey][%s][/COLOR]' % votes)

        label = ' '.join(parts)

        li = xbmcgui.ListItem(label=label)

        _apply_video_metadata(li, film, media_type)

        if poster:
            li.setArt({'poster': poster, 'thumb': poster,
                       'icon': poster, 'fanart': poster})

        # Context menu: reviews
        rev_url = build_url(action='reviews',
                            csfd_url=film.get('csfd_url', ''),
                            title=title)
        li.addContextMenuItems([(L(30008), 'RunPlugin(%s)' % rev_url)])

        # Click → SC search (série: strip "- Série X" suffix, search as seriál)
        item_url = build_url(
            action='open_sc',
            mtype=media_type,
            title=title,
            country=film.get('country', ''),
            csfd_url=film.get('csfd_url', ''),
        )
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, item_url, li, isFolder=True)

    # 5. "Next" button
    if end < total:
        li = xbmcgui.ListItem(
            label='[COLOR blue]%s[/COLOR]' % (L(30010) % (total - end)))
        xbmcplugin.addDirectoryItem(
            ADDON_HANDLE,
            build_url(action='ranking', mtype=media_type, page=str(page + 1), year=year_from),
            li, isFolder=True)

    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


def show_widget_plain(media_type):
    """Home screen widget: top 30 items, no SC check, no progress dialog.

    Uses stale cache for instant display, background refresh if needed.
    cacheToDisc=False so Kodi calls addon each time → addon decides from cache.
    """
    is_film = (media_type == TYPE_FILM)
    year_from = get_year_from()

    xbmcplugin.setContent(ADDON_HANDLE, 'movies' if is_film else 'tvshows')

    all_items = fetch_ranking_cached(media_type, year_from)
    if not all_items:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=True, cacheToDisc=False)
        return

    items = all_items[:ITEMS_PER_PAGE]

    for film in items:
        title = film['title']
        year = film.get('year', '')
        poster = film.get('poster_url', '')
        li = xbmcgui.ListItem(label=title)
        _apply_video_metadata(li, film, media_type)
        if poster:
            li.setArt({'poster': poster, 'thumb': poster, 'icon': poster, 'fanart': poster})

        item_url = build_url(
            action='open_sc',
            mtype=media_type,
            title=title,
            country=film.get('country', ''),
            csfd_url=film.get('csfd_url', ''),
        )
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, item_url, li, isFolder=True)

    xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=True, cacheToDisc=False)


def show_reviews(csfd_url, title):
    if not csfd_url:
        return
    reviews = fetch_single_reviews(csfd_url)
    if not reviews:
        xbmcgui.Dialog().notification('ČSFD', L(30006), xbmcgui.NOTIFICATION_INFO)
        return
    lines = []
    for r in reviews:
        lines.append('─' * 50)
        lines.append('%s  %s' % (stars_str(r.get('stars', 0)), r.get('user', '?')))
        lines.append('')
        lines.append(r.get('text', ''))
        lines.append('')
    xbmcgui.Dialog().textviewer(
        L(30009) % (title, len(reviews)),
        '\n'.join(lines))


def open_sc_search(media_type, title, csfd_url='', country=''):
    _open_sc_search({
        'title': title,
        'csfd_url': csfd_url,
        'country': country,
    }, media_type)
    xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False, cacheToDisc=False)


# ═══════════════════════════════════════════════════════════════

def router():
    params = parse_qs(ADDON_ARGS)
    args = {k: v[0] if v else '' for k, v in params.items()}
    action = args.get('action', '')

    if action == 'ranking':
        show_ranking(args.get('mtype', TYPE_FILM), int(args.get('page', '1')), args.get('year', ''))
    elif action in ('widget', 'widget_film'):
        show_widget_plain(TYPE_FILM)
    elif action == 'widget_serial':
        show_widget_plain(TYPE_SERIES)
    elif action == 'widget_serie':
        show_widget_plain(TYPE_SERIE)
    elif action == 'open_sc':
        open_sc_search(
            args.get('mtype', TYPE_FILM),
            args.get('title', ''),
            args.get('csfd_url', ''),
            args.get('country', ''),
        )
    elif action == 'reviews':
        show_reviews(args.get('csfd_url', ''), args.get('title', ''))
    else:
        main_menu()


if __name__ == '__main__':
    router()

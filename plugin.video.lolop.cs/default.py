# -*- coding: utf-8 -*-
"""Lolop CS combined addon.

Routes:
- action= (none)            main menu (kolop SC sources + CSFD ranking entries)
- action=source             show kolop SC source (full UI with progress)
- action=widget             show kolop SC source for home widgets (silent)
- action=prewarm            background prewarm of kolop sources
- action=reviews            show CSFD reviews dialog
- action=ranking            CSFD ranking (Filmy/Serialy/Serie, paginated)
- action=widget_film        CSFD ranking widget (movies)
- action=widget_serial      CSFD ranking widget (TV series)
- action=widget_serie       CSFD ranking widget (seasons)
- action=open_sc            open Stream Cinema search for a CSFD item
"""

import os
import sys
from datetime import date, timedelta
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

# kolop SC sources
from sc_bridge import build_sc_search_url, get_search_title, CZ_SK, _normalize_text  # noqa: E402
from sc_csfd import (  # noqa: E402
    MAIN_MENU_SOURCES,
    SOURCE_CONFIG,
    build_source_items,
    fetch_single_reviews as kolop_fetch_single_reviews,
    get_source_content,
    get_source_label,
    prewarm_sources,
    resolve_source_key,
)
# CSFD rankings
from csfd import (  # noqa: E402
    TYPE_FILM, TYPE_SERIES, TYPE_SERIE, ITEMS_PER_PAGE,
    fetch_ranking, fetch_ranking_cached,
    fetch_single_reviews as csfd_fetch_single_reviews,
    fetch_single_title_data,
)

STAR_FULL = '\u2605'
STAR_EMPTY = '\u2606'
RATING_TYPE_ID = 'csfd'
RATING_TYPE_LABEL = 'CSFD'
MEDIA_TYPE_LABELS = {
    TYPE_FILM: 'Film',
    TYPE_SERIES: 'Serial',
    TYPE_SERIE: 'Serie',
}


def L(string_id):
    return ADDON.getLocalizedString(string_id)


def log(message, level=xbmc.LOGINFO):
    xbmc.log('[LolopCS] %s' % message, level)


def build_url(**params):
    return '%s?%s' % (ADDON_URL, urlencode(params))


def stars_str(count):
    count = max(0, min(5, int(count or 0)))
    return STAR_FULL * count + STAR_EMPTY * (5 - count)


# -----------------------------------------------------------------------
# Year handling for CSFD rankings
# -----------------------------------------------------------------------

def _default_year():
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


# -----------------------------------------------------------------------
# Main menu (combined: kolop sources + CSFD rankings)
# -----------------------------------------------------------------------

def main_menu():
    xbmcplugin.setPluginCategory(ADDON_HANDLE, L(30001))
    xbmcplugin.setContent(ADDON_HANDLE, 'videos')

    # Kolop SC sources
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

    # CSFD ranking entries
    year = get_year_from()
    for mtype, label_id, mediatype in (
        (TYPE_FILM, 30102, 'movie'),
        (TYPE_SERIES, 30103, 'tvshow'),
        (TYPE_SERIE, 30104, 'tvshow'),
    ):
        entry_label = '%s %s (%s)' % ('\U0001F3AC' if mtype == TYPE_FILM else '\U0001F4FA',
                                      L(label_id), year)
        li = xbmcgui.ListItem(label=entry_label)
        li.getVideoInfoTag().setTitle(L(label_id))
        li.setArt({'icon': 'DefaultMovies.png' if mediatype == 'movie' else 'DefaultTVShows.png'})
        xbmcplugin.addDirectoryItem(
            ADDON_HANDLE,
            build_url(action='ranking', mtype=mtype, page='1', year=year),
            li,
            isFolder=True
        )

    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


# -----------------------------------------------------------------------
# Kolop SC source listing
# -----------------------------------------------------------------------

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
            reviews_url = build_url(action='reviews', csfd_url=item['csfd_url'], title=item.get('title', ''), kind='kolop')
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


# -----------------------------------------------------------------------
# CSFD ranking listing (from csfd-rebricek logic)
# -----------------------------------------------------------------------

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


def _apply_csfd_metadata(li, film, media_type):
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


def _open_sc_search(film, media_type):
    if media_type == TYPE_FILM:
        mt = 'F'
    else:
        mt = 'S'

    search_item = dict(film)
    country = _normalize_text(search_item.get('country', ''))
    is_czsk = any(c in country for c in CZ_SK)

    if (not is_czsk and search_item.get('csfd_url')
            and not search_item.get('english_title')
            and not search_item.get('original_title')):
        log('On-demand title lookup for SC search: %s' % search_item.get('title', ''))
        search_item.update(fetch_single_title_data(search_item['csfd_url']))

    search_title = get_search_title(search_item, is_serie=(media_type == TYPE_SERIE))
    log('Opening SC search with title: %s' % search_title)
    xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % build_sc_search_url(search_title, mt))


def show_ranking(media_type, page, year_param=''):
    year_from = year_param if year_param else get_year_from()
    is_film = (media_type == TYPE_FILM)
    if media_type == TYPE_SERIE:
        category = L(30104)
    elif media_type == TYPE_SERIES:
        category = L(30103)
    else:
        category = L(30102)

    log('show_ranking: type=%s year=%s page=%d' % (media_type, year_from, page))

    xbmcplugin.setPluginCategory(ADDON_HANDLE, '%s (%s)' % (category, year_from))
    xbmcplugin.setContent(ADDON_HANDLE, 'movies' if is_film else 'tvshows')

    all_items = fetch_ranking(media_type, year_from)

    if not all_items:
        xbmcgui.Dialog().notification(L(30001), L(30105),
                                      xbmcgui.NOTIFICATION_WARNING)
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False, cacheToDisc=False)
        return

    total = len(all_items)
    start = (page - 1) * ITEMS_PER_PAGE
    end = min(start + ITEMS_PER_PAGE, total)
    page_items = all_items[start:end]

    if not page_items:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False, cacheToDisc=False)
        return

    for film in page_items:
        title = film['title']
        year = film.get('year', '')
        rating = film.get('rating', '')
        votes = film.get('votes', '')
        poster = film.get('poster_url', '')
        position = film.get('position', '')

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
        _apply_csfd_metadata(li, film, media_type)

        if poster:
            li.setArt({'poster': poster, 'thumb': poster,
                       'icon': poster, 'fanart': poster})

        rev_url = build_url(action='reviews',
                            csfd_url=film.get('csfd_url', ''),
                            title=title,
                            kind='csfd')
        li.addContextMenuItems([(L(30108), 'RunPlugin(%s)' % rev_url)])

        item_url = build_url(
            action='open_sc',
            mtype=media_type,
            title=title,
            country=film.get('country', ''),
            csfd_url=film.get('csfd_url', ''),
        )
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, item_url, li, isFolder=True)

    if end < total:
        li = xbmcgui.ListItem(
            label='[COLOR blue]%s[/COLOR]' % (L(30110) % (total - end)))
        xbmcplugin.addDirectoryItem(
            ADDON_HANDLE,
            build_url(action='ranking', mtype=media_type, page=str(page + 1), year=year_from),
            li, isFolder=True)

    xbmcplugin.endOfDirectory(ADDON_HANDLE, cacheToDisc=False)


def show_csfd_widget(media_type):
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
        poster = film.get('poster_url', '')
        li = xbmcgui.ListItem(label=title)
        _apply_csfd_metadata(li, film, media_type)
        if poster:
            li.setArt({'poster': poster, 'thumb': poster,
                       'icon': poster, 'fanart': poster})

        item_url = build_url(
            action='open_sc',
            mtype=media_type,
            title=title,
            country=film.get('country', ''),
            csfd_url=film.get('csfd_url', ''),
        )
        xbmcplugin.addDirectoryItem(ADDON_HANDLE, item_url, li, isFolder=True)

    xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=True, cacheToDisc=False)


def open_sc_search(media_type, title, csfd_url='', country=''):
    _open_sc_search({
        'title': title,
        'csfd_url': csfd_url,
        'country': country,
    }, media_type)
    xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False, cacheToDisc=False)


# -----------------------------------------------------------------------
# Reviews dialog (kolop or csfd source)
# -----------------------------------------------------------------------

def show_reviews(csfd_url, title, kind='kolop'):
    if not csfd_url:
        return
    if kind == 'csfd':
        reviews = csfd_fetch_single_reviews(csfd_url)
    else:
        reviews = kolop_fetch_single_reviews(csfd_url)

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


# -----------------------------------------------------------------------
# Router
# -----------------------------------------------------------------------

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
    elif action == 'ranking':
        show_ranking(args.get('mtype', TYPE_FILM),
                     int(args.get('page', '1')),
                     args.get('year', ''))
    elif action in ('widget_film',):
        show_csfd_widget(TYPE_FILM)
    elif action == 'widget_serial':
        show_csfd_widget(TYPE_SERIES)
    elif action == 'widget_serie':
        show_csfd_widget(TYPE_SERIE)
    elif action == 'open_sc':
        open_sc_search(
            args.get('mtype', TYPE_FILM),
            args.get('title', ''),
            args.get('csfd_url', ''),
            args.get('country', ''),
        )
    elif action == 'reviews':
        show_reviews(args.get('csfd_url', ''),
                     args.get('title', ''),
                     args.get('kind', 'kolop'))
    else:
        main_menu()


if __name__ == '__main__':
    router()

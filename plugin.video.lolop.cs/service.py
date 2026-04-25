# -*- coding: utf-8 -*-

import os
import sys

import xbmc
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()

LIB_PATH = os.path.join(
    xbmcvfs.translatePath(ADDON.getAddonInfo('path')),
    'resources',
    'lib',
)
if LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)

from sc_csfd import PREWARM_WIDGET_LIMIT, SC_ADDON_ID, log, prewarm_sources  # noqa: E402

ANDROID_STARTUP_DELAY = 8
DEFAULT_STARTUP_DELAY = 3
TARGET_SKIN_ID = 'skin.lolop.cs'


def _startup_delay():
    if xbmc.getCondVisibility('System.Platform.Android'):
        return ANDROID_STARTUP_DELAY
    return DEFAULT_STARTUP_DELAY


if __name__ == '__main__':
    monitor = xbmc.Monitor()
    if not xbmc.getCondVisibility('System.HasAddon(%s)' % SC_ADDON_ID):
        log('Skipping startup prewarm because %s is missing' % SC_ADDON_ID, xbmc.LOGWARNING)
        raise SystemExit(0)
    if xbmc.getSkinDir() != TARGET_SKIN_ID:
        log('Skipping startup prewarm because active skin is %s' % xbmc.getSkinDir())
        raise SystemExit(0)

    delay = _startup_delay()
    log('Startup prewarm scheduled in %ss' % delay)
    if not monitor.waitForAbort(delay):
        prewarm_sources(item_limit=PREWARM_WIDGET_LIMIT, monitor=monitor)

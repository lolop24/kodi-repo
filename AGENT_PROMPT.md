# Agent Prompt: 42po.lol Kodi Repository Management

## Čo je tento repozitár

Kodi addon repozitár dostupný na `https://42po.lol`. GitHub: `https://github.com/lolop24/kodi-repo`.
Lokálna cesta na Windows: `C:\Claude\kodi-repo\`

Obsahuje addony:
- `plugin.video.csfd-rebricek` — ČSFD rebríčky so Stream Cinema
- `service.subtitles.titulky-dualsub` — duálne titulky
- `repository.42polol` — samotný repo addon

---

## Ako pridať / aktualizovať addon

### Krok 1 — Priprav zip

Zip **musí** mať štruktúru:
```
plugin.video.nazov/
├── addon.xml        ← POVINNÉ
├── default.py
└── resources/
```

Názov zipu: `plugin.video.nazov-X.Y.Z.zip`

### Krok 2 — Skopíruj zip pomocou Write nástroja

```
C:\Claude\kodi-repo\<addon-id>\<addon-id>-<verzia>.zip
```

Príklady:
```
C:\Claude\kodi-repo\plugin.video.csfd-rebricek\plugin.video.csfd-rebricek-12.4.0.zip
C:\Claude\kodi-repo\service.subtitles.titulky-dualsub\service.subtitles.titulky-dualsub-1.3.0.zip
```

**DÔLEŽITÉ:** Použi `Write` nástroj (nie Bash cp/copy). Hook sa spustí automaticky iba pri `Write` nástroji.

### Krok 3 — Automatika

Po zapísaní zip súboru sa automaticky spustí hook ktorý:
1. Extrahuje `addon.xml` zo zipu → uloží do `C:\Claude\kodi-repo\<addon-id>\addon.xml`
2. Regeneruje `addons.xml` a `addons.xml.md5`
3. Commitne a pushne na GitHub
4. Zobrazí notifikáciu: `Kodi repo: <nazov>.zip pushnuty na GitHub`

Netreba robiť nič ďalšie.

---

## Manuálna regenerácia (ak hook zlyhal)

```bash
cd C:\Claude\kodi-repo
py update_repo.py
git add -A
git commit -m "Update repo"
git push
```

---

## Požiadavky na addon.xml

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="plugin.video.nazov"
       name="Zobrazený názov"
       version="1.0.0"
       provider-name="lolop24">
    <requires>
        <import addon="xbmc.python" version="3.0.0"/>
        <import addon="xbmc.addon" version="21.0.0"/>
    </requires>
    <extension point="xbmc.python.pluginsource" library="default.py">
        <provides>video</provides>
    </extension>
    <extension point="xbmc.addon.metadata">
        <summary lang="sk">Krátky popis</summary>
        <description lang="sk">Dlhší popis</description>
        <platform>all</platform>
        <license>GPL-2.0-only</license>
    </extension>
</addon>
```

Typy addonov:
| Typ | Extension point |
|-----|----------------|
| Video plugin | `xbmc.python.pluginsource` + `<provides>video</provides>` |
| Titulky | `xbmc.subtitle.module` |
| Služba | `xbmc.service` |

---

## Overenie po nahraní

```
https://42po.lol/addons.xml
```

Alebo spusti:
```bash
py C:\Claude\check_repo.py
```

---

## Štruktúra repozitára (aktuálna)

```
C:\Claude\kodi-repo\
├── addons.xml                    ← AUTO-GENEROVANÝ, nemeniť ručne
├── addons.xml.md5                ← AUTO-GENEROVANÝ, nemeniť ručne
├── index.html                    ← výpis pre Kodi browser
├── repository.42polol-1.0.0.zip  ← inštalačný zip (pre používateľov)
├── update_repo.py                ← skript na manuálnu regeneráciu
├── CNAME                         ← 42po.lol
├── plugin.video.csfd-rebricek/
│   ├── addon.xml                 ← extrahovaný z zipu
│   └── plugin.video.csfd-rebricek-12.3.0.zip
├── service.subtitles.titulky-dualsub/
│   ├── addon.xml
│   └── service.subtitles.titulky-dualsub-1.2.0.zip
└── repository.42polol/
    ├── addon.xml
    └── repository.42polol-1.0.0.zip
```

---

## Dôležité pravidlá

1. **NIKDY** neupravuj `addons.xml` ručne — generuje sa automaticky z `addon.xml` súborov
2. **VŽDY** použi `Write` nástroj na zápis zip (nie Bash) aby sa spustil hook
3. **Verzia** v názve zipu musí zodpovedať verzii v `addon.xml` vnútri zipu
4. Staré verzie zipov môžu ostať v priečinku — Kodi berie najnovšiu podľa `addons.xml`
5. Pri zmene `repository.42polol/addon.xml` treba pushnúť aj nový `repository.42polol-1.0.0.zip` (aktualizuj verziu!)

---

## Ako zistiť aktuálny stav

```bash
# Zisti čo je live na serveri
py C:\Claude\check_repo.py

# Zisti verzie v addons.xml
py -c "
from xml.etree import ElementTree as ET
tree = ET.parse('C:/Claude/kodi-repo/addons.xml')
for a in tree.getroot():
    print(a.get('id'), a.get('version'))
"
```

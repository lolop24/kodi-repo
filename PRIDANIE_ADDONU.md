# Ako pridať addon do 42po.lol Kodi repozitára

## Repozitár
- **URL:** http://42po.lol
- **GitHub:** https://github.com/lolop24/kodi-repo
- **Lokálna cesta:** `C:\Claude\kodi-repo\`

---

## Čo treba urobiť (3 kroky)

### 1. Priprav zip súbor

Zip musí mať túto štruktúru **zvnútra**:
```
plugin.video.nazov/
├── addon.xml        ← povinné
├── default.py
└── resources/
    └── ...
```

Názov zipu musí obsahovať verziu:
```
plugin.video.nazov-1.0.0.zip
```

### 2. Skopíruj zip do správneho priečinka

```
C:\Claude\kodi-repo\<addon-id>\<addon-id>-<verzia>.zip
```

**Príklady:**
```
C:\Claude\kodi-repo\plugin.video.csfd-rebricek\plugin.video.csfd-rebricek-12.0.1.zip
C:\Claude\kodi-repo\service.subtitles.titulky-dualsub\service.subtitles.titulky-dualsub-1.0.0.zip
```

> Priečinok sa vytvorí automaticky ak neexistuje.

### 3. Hotovo — zvyšok je automatické

Po skopírovaní zipu pomocou nástroja `Write` sa automaticky spustí hook ktorý:
- Extrahuje `addon.xml` zo zipu a zistí verziu
- Regeneruje `addons.xml` a `addons.xml.md5`
- Commitne a pushne na GitHub
- Zobrazí notifikáciu: `Kodi repo: <nazov>.zip pushnuty na GitHub`

---

## Požiadavky na addon.xml

Každý addon musí mať platný `addon.xml` s týmito povinnými poľami:

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="plugin.video.nazov"
       name="Zobrazený názov"
       version="1.0.0"
       provider-name="autor">
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

**Typy addonov podľa `extension point`:**
| Typ | Extension point |
|-----|----------------|
| Video plugin | `xbmc.python.pluginsource` + `<provides>video</provides>` |
| Titulky | `xbmc.subtitle.module` |
| Služba | `xbmc.service` |
| Program | `xbmc.python.pluginsource` + `<provides>executable</provides>` |

---

## Aktualizácia existujúceho addonu

Stačí nahrať nový zip s vyššou verziou do toho istého priečinka:
```
C:\Claude\kodi-repo\plugin.video.nazov\plugin.video.nazov-1.1.0.zip
```

Staré verzie môžu ostať — Kodi vždy nainštaluje najnovšiu.

---

## Overenie po nahraní

Po pushnutí skontroluj či sa verzia správne zobrazuje:
```
http://42po.lol/addons.xml
```

Alebo spusti kontrolný skript:
```
py C:\Claude\check_repo.py
```

---

## Štruktúra repozitára

```
C:\Claude\kodi-repo\
├── addons.xml                          ← auto-generovaný, nemeniť ručne
├── addons.xml.md5                      ← auto-generovaný, nemeniť ručne
├── index.html                          ← výpis súborov pre Kodi
├── repository.42polol-1.0.0.zip        ← inštalačný zip repozitára
├── update_repo.py                      ← skript na manuálnu regeneráciu
├── plugin.video.csfd-rebricek/
│   ├── addon.xml
│   └── plugin.video.csfd-rebricek-12.0.1.zip
├── service.subtitles.titulky-dualsub/
│   ├── addon.xml
│   └── service.subtitles.titulky-dualsub-1.0.0.zip
└── repository.42polol/
    ├── addon.xml
    └── repository.42polol-1.0.0.zip
```

---

## Manuálna regenerácia (ak je potrebná)

```bash
cd C:\Claude\kodi-repo
py update_repo.py
git add -A
git commit -m "Update repo"
git push
```

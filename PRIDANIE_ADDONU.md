# Ako spravne pridat alebo updatnut addon v tomto Kodi repo

Repo: [https://github.com/lolop24/kodi-repo](https://github.com/lolop24/kodi-repo)

Tento repozitar funguje ako jednoduchy Kodi add-on repository:
- kazdy addon ma vlastny priecinok v roote repa
- v tom priecinku musi byt `addon.xml`
- pre instalaciu z repozitara musi byt v tom istom priecinku aj verzionovany zip
- root `addons.xml` a `addons.xml.md5` sa generuju zo suborov `addon.xml`

`update_repo.py` iba regeneruje `addons.xml` a `addons.xml.md5`.
Commit a push na GitHub su v tomto repozitari manualny krok.

## Co musi mat novy addon

Minimalne:
- priecinok s nazvom addon id, napriklad `plugin.video.kolop`
- root `addon.xml`
- korektne nastavene `id`, `version`, `name`, `provider-name`
- spravny `extension point` pre typ addonu
- `LICENSE.txt`
- `resources/icon.png`
- `resources/fanart.jpg`

Odporucane:
- `README.md`
- screenshoty v `resources/`
- lokalizovane `summary` a `description`
- pre python addon `resources/settings.xml`, `resources/language/`, `resources/lib/`

## Co musi mat novy skin

Okrem vseobecnych veci vyssie:
- `addon.xml` s `extension point="xbmc.gui.skin"`
- aspon jeden `<res ... folder="xml" default="true" />`
- priecinok `xml/`
- validne XML bez warningov v Kodi logu
- lokalizovane labely, nie natvrdo zapisany text vsade v XML
- ak su obrazky mimo packed texture, nedavat ich do `media/`, ale napr. do `extras/`

## Typicke `extension point`

Video addon:
```xml
<extension point="xbmc.python.pluginsource" library="default.py">
    <provides>video</provides>
</extension>
```

Skin:
```xml
<extension point="xbmc.gui.skin" debugging="false">
    <res width="1920" height="1080" aspect="16:9" default="true" folder="xml" />
</extension>
```

## Spravny publish postup pre tento repo

1. Zvys verziu v `addon.xml`.
2. Uisti sa, ze addon priecinok obsahuje vsetko potrebne.
3. Vytvor verzionovany zip v priecinku addonu:
   - `plugin.video.kolop/plugin.video.kolop-1.1.0.zip`
   - `skin.kolop/skin.kolop-1.0.0.zip`
4. Spusti:

```powershell
python .\update_repo.py
```

5. Skontroluj zmeny:

```powershell
git status --short
git diff -- addons.xml addons.xml.md5
```

6. Commitni a pushni:

```powershell
git add addons.xml addons.xml.md5 <addon-priecinok>
git commit -m "Add/update <addon-id> <version>"
git push origin master
```

## Poznamky

- Bez noveho zipu Kodi neuvidi novu verziu na instalaciu, aj keby bol `addon.xml` uz pushnuty.
- Bez regenerovaneho `addons.xml` sa novy addon v repozitari neobjavi.
- Ak menis uz existujuci addon a nezvysis `version`, Kodi update vacsinou neponukne.
- Pre skiny je dobre drzat startup co najlahsi a widgety nacitavat lazy cez `plugin://...`.

## Zdroj dokumentacie Kodi

- [Addon.xml](https://kodi.wiki/view/Addon.xml)
- [Add-on structure](https://kodi.wiki/view/Add-on_structure)
- [Add-on rules](https://kodi.wiki/view/Add-on_rules)
- [Submitting Add-ons](https://kodi.wiki/view/Submitting_Add-ons)

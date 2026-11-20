## Malfoid Gallery

sync with what I have locally, saved via Twitter Media Harvest https://github.com/EltonChou/TwitterMediaHarvest

whip up a gallery view website because viewing thousands of images on google drive is horrible

## Builds

Main site reads only `Malfoid/`. 18+ site reads only `Malfoid H/`. `Malfoid AI/` is never built.

```
python3 tools/build_malfoid_gallery.py
python3 tools/build_malfoid_gallery.py --audience adult --site docs-18
```

`data/exclude.json` holds works kept off both builds. `data/audience.json` holds the 18+ works; the main build skips them and the 18+ build includes only them.


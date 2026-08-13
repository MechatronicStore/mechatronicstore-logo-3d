# Using the MechatronicStore logo

The code in this repository is MIT licensed. The brand is not. The name
"MechatronicStore", the wordmark and the "m" symbol — including the SVG files
in `assets/` — are trademarks of MechatronicStore (Chile).

## You may

* Run this tool and print parts carrying the MechatronicStore logo, for
  yourself, your workshop, your school or your classroom.
* Give those printed parts to other people.
* Modify the code however you like, including changing the size, depth or
  placement of the logo.

## You may not

* Present yourself, your shop or your project as MechatronicStore, or as an
  official partner, reseller or sponsee of it.
* Use the logo in your own brand, product line, packaging or app icon.
* Sell parts carrying the MechatronicStore logo without written permission.
* Modify the logo artwork itself and keep calling it the MechatronicStore logo
  (recolored, stretched or redrawn versions are not the mark).

## Want to do something not on that list?

Ask: **ventas@mechatronicstore.cl**. Commercial use is usually fine with a
short written OK.

## Using this tool with your own logo

Nothing here is MechatronicStore-specific. Point `--logo` at any SVG and the
pipeline works the same:

```bash
python logo3d.py --model part.stl --logo my-own-logo.svg
```

Flat, filled paths in a handful of solid colors give the best results.

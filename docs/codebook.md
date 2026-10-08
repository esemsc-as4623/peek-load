# Building-use codebook (DRAFT v0, needs human approval before any labelling)

Status: **draft, not approved**. The gold set and the Claude labelling prompt are both built from this
document, so changes after labelling starts invalidate labels. Approve or edit, then bump the version.

## Unit and question
One footprint polygon. Question: *what is the main use of this structure, as it relates to electricity demand?*
Label what the evidence supports. When the evidence can't separate two classes, choose `unknown`; don't guess.

## Classes (keep in sync with `LABEL_CLASSES` in `src/rtl/schemas.py`)

| class | definition | typical evidence | not this class |
|---|---|---|---|
| `residential` | Dwelling; people sleep here | Small-to-medium footprint in a cluster of similar buildings; no POI/tag; residential street pattern | A dwelling with a street-facing shop → `mixed_shop_house` |
| `mixed_shop_house` | Dwelling with a shop, kiosk, salon or small service in part of the building | Street-facing, on a main road or market street, a shop/POI matched to the footprint, elongated frontage | Stand-alone shop without a dwelling → `commercial` |
| `commercial` | Shops, markets, restaurants, bars, hotels, offices, banks, pharmacies, mobile-money and phone-charging kiosks | OSM `shop=*`/`amenity=restaurant/bank…`, Overture place, market cluster, large footprint on a main road | Workshops making or processing goods → `productive_use` |
| `institutional` | Schools, health facilities, government offices, churches, mosques, community halls | OSM/registry facility match, large rectangular blocks in a compound, regular multi-block layout | |
| `productive_use` | Small-scale production with motors or heat: grain mills, welding, carpentry, tailoring, agro-processing, cold storage, irrigation pump houses, water kiosks with pumps | OSM `craft=*`, `industrial=*`, Overture category, location near fields/irrigation or a trading centre | Retail without processing → `commercial` |
| `industrial_warehouse` | Factories, large warehouses, industrial-zone buildings | Very large footprint, industrial zone, `landuse=industrial` | |
| `ancillary` | Structures with negligible electricity demand: latrines, detached kitchens, animal sheds, small stores | Very small (often < 10 m²) next to a larger building in the same plot | A tiny kiosk on a road → `commercial` |
| `unknown` | Evidence doesn't support a class | — | Never use as a "probably residential" bucket |

## Decision rules
1. Use a direct, matched tag or registry record when one exists, and note it as evidence.
2. Without a tag, use footprint size, shape, height, neighbourhood and road context. Write down which cues you used.
3. Size alone never decides between `commercial` and `institutional`.
4. Under 10 m² with a larger building within 10 m → lean `ancillary`.
5. If two classes fit about equally, choose `unknown` and say which two.

## Confidence (gold labellers and Claude both report it)
- `high`: a direct tag or registry match, or unambiguous context
- `medium`: several consistent indirect cues
- `low`: a single weak cue (labels with low confidence are kept but reported separately)

## Open questions for approval
- Is `mixed_shop_house` worth separating from `residential` for demand purposes? (Recommendation: yes.
  Shop-houses are common in Rwandan trading centres and add lighting/fridge/TV load.)
- Should religious buildings be separate from `institutional`? Their load profile is weekly, not daily.

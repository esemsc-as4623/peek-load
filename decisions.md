# Open decisions and opportunities

*Companion to [`status_report.md`](status_report.md) · updated 2026-10-13*

Things we haven't settled, and places where the data could be pushed further. Each item says what the pipeline does
today, why it matters (with numbers where we have them), the options and a recommendation. Decisions already taken are
recorded in `data/gold/approvals.yaml`.

## The underlying problem: many allocations fit the totals

Most of what we know about demand is **aggregated**:

| Source | What it pins down | Level |
|---|---|---|
| 2022 census (RPHC5) | households | district × urban/rural |
| Establishment Census 2023 | establishments by activity | district × urban/rural (activity only nationally × setting) |
| EICV7 2024 | share of households lighting with grid power | district |
| DHS 2025 | household electricity access | ~560 displaced clusters |
| MTF 2022 | appliance ownership, hours, spending | national × setting × wealth group |
| QSEL / REG | mean consumption of customers | Kigali vs rest of country |

We want a **spatio-temporal** answer (per building, per hour, per season). Many building-level allocations reproduce
those totals equally well. The pipeline today picks one by rules (profiles from labels, area amounts spread by a
score or by density). That choice is defensible, but it is checked against the totals only in places.

**Recommendation: make the choice explicit and testable.** Treat building-level demand as the *least surprising*
allocation that matches every known total:
1. **Prior per building.** Today's estimate (class probabilities × profiles, grid probability, candidate scores).
2. **Constraints.** The totals above, each at its own level: households per district and setting, establishments per
   district × setting × activity, connection share per district, consumption per setting.
3. **Fit.** Find the allocation closest to the prior (minimum KL divergence) that satisfies the constraints. In
   practice this is iterative proportional fitting (raking), which is simple, fast and explainable. A Bayesian
   hierarchical version would add uncertainty bands.
4. **Report.** The fitted values, how far each constraint moved the prior (a diagnostic), and a range from sampling
   alternative allocations.

The two checks below show why this matters: one sector already fits, another doesn't.

## A. Needs a decision

| # | Item | What we do now | Why it matters | Options | Recommendation |
|---|---|---|---|---|---|
| A1 | **Households per building** | Every building labelled residential is one household (a shop-house is one household + one shop) | Nationally there are **0.52 census households per building** (3.31M / 6.43M), ranging from 0.41 (Gicumbi) to 0.73 (Nyarugenge). **Nyamirambo fits:** 15,076 modelled household units vs ~15,400 census-implied households. **Nasho does not:** 12,484 units vs ~8,500, about **46% too many**. Rural compounds have several structures per household, and the labeller calls many of them residential | (a) keep; (b) rake household units to census households per district × setting; (c) a compound rule: small residential structures next to a larger one become ancillary | **(b)** first, since it is a one-line constraint and uses data we already hold; (c) as a later refinement. Expect rural household demand to drop by roughly a third |
| A2 | **Bars/restaurants, guesthouses, salons** (new step 10) | Tagged buildings + census sections I and S per area, allocated by population density | The 0.10 rule removed these profiles from labelled buildings. Splits are assumptions: section I = **97% bars/restaurants, 3% accommodation**; section S = **60% hairdressing**. ~60,000 bars/restaurants at 2.1 kWh/day (fridge, lights, TV) is ~46 GWh/yr nationally | (a) keep the splits; (b) set them from the RDB accommodation register / census microdata; (c) a lighter **rural cabaret** profile (lights, radio, phone charging, no fridge) | (c) plus (a) for now: the urban profile is an upper bound for rural cabarets |
| A3 | **Shops: labels vs census** | Labelled commercial buildings get a kiosk profile; no census constraint | Labelled kiosk units vs census retail establishments allocated by density: Nyamirambo **976 vs ~1,990** (labels under-count), Nasho **434 vs ~310** (labels over-count). Services and productive use are census-based, but shops aren't | (a) keep; (b) treat retail like services (tags + census per area), dropping the labeller's commercial class; (c) rake labelled shop units to the census | (c): keeps the building-level signal where the labeller sees it, and fixes the total |
| A4 | **Rural productive use = mills** | All rural manufacturing (9,080 establishments) gets the 10 kW grain-mill profile | ~86 GWh/yr nationally; Nasho gets 62 mills (0.59 GWh, 31% of its building demand). Rural manufacturing also includes banana beer, bakeries, brick-making and crafts | (a) keep (owner's rule); (b) a mill share (e.g. 20–30%) with the rest as a light workshop profile; (c) take the share from the census microdata | (b) with a sensitivity range, until (c) is available |
| A5 | **Town trade shares** | Tailoring 34%, welding 33%, carpentry 33% | No source; welding dominates the energy (5.4 kWh/day vs 1.1 and 0.45) | (a) keep; (b) census microdata; (c) MTF enterprise activity (too few: n < 10 per trade) | (b) if obtainable, else keep and show the range |
| A6 | **Candidate eligibility and weights** | ≥ 30 m², not ancillary/religious/institutional or a facility; mills ≤ 100 m from a road; weights in `config/productive_use.yaml` | They decide *where* unlocated demand is drawn and which buildings are flagged | sign off, or adjust | Sign off as defaults; the dashboard sliders already let users vary them |
| A7 | **Applying the grid model** | P(connected) computed for every building, not used | Potential vs expected demand differ most in rural areas (Nasho mean P = 0.40) | (a) expected = potential × P for households only; (b) for all buildings; (c) also rake to EICV7 district shares | (a) + (c): establishments in a connected area are usually connected |
| A8 | **Health facilities** | One profile built from MTF health *centres* | It fails its check: 366 kWh/month vs centres' 1,367–4,101; health posts use far less | (a) keep; (b) split posts/centres using the MoH registry or facility names; (c) find a health-post source | (b) + (c) |
| A9 | **Use labels beyond the deep dives** | LLM labels for 44,556 buildings; the API credit has run out | National demand needs a use for 6.4M buildings; at $3.6 per 1,000 the LLM would cost ~$23k | (a) a classical classifier on context features, trained on tags (37k silver labels) plus LLM labels; (b) residential/ancillary by rule (size, compound position), with census-based area amounts for everything else | (b) then (a): the census already carries most non-residential demand |
| A10 | **Muhoza coverage** | 8,620 of 32,538 buildings labelled | Sector totals are partial | finish with A9's model, or label a random sample and scale | Use A9's model; keep the LLM labels as a check |

## B. Opportunities to impute and fit better

| # | Opportunity | Data we have | Approach | Effort |
|---|---|---|---|---|
| B1 | **Raking to known totals** (see the top of this file) | census households, Establishment Census, EICV7, DHS, QSEL/REG means | iterative proportional fitting per district × setting; report the adjustment factors | small |
| B2 | **Seasonal and weekly shape** | monthly ERA5-Land climate (built), school calendar, two harvest seasons (season A Dec–Feb, season B Jun–Jul) | seasonal RAMP variants: fans by cooling class, mills busy after harvests, schools closed in holidays | medium |
| B3 | **Growth over time** | yearly presence 2016–2023 per building | back-cast demand by year (with the 2019/2023 artefacts smoothed); project forward with district growth | medium |
| B4 | **Floors and floor area by ML** | 9,846 OSM-levelled buildings, heights, shapes | gradient boosting or ordinal regression in place of cut-points; scales commercial loads by floor area | small |
| B5 | **Ancillary structures** | footprint size, distance to the nearest larger building, compound polygons | rule or classifier for kitchens, latrines and stores; feeds A1 | small |
| B6 | **Uncertainty bands** | p10/p90 daily kWh per profile; class probabilities; allocation alternatives | Monte Carlo over profiles × labels × allocations → p10/p50/p90 per building and area | medium |
| B7 | **Wealth within districts** | Meta RWI (2.4 km tiles), DHS wealth index at clusters | calibrate the RWI wealth split to DHS wealth quintiles by district | small |
| B8 | **Night lights** | NASA VIIRS Black Marble (needs a free Earthdata login) | a grid-model feature and an activity check | small once downloaded |

## C. Data and housekeeping

| # | Item | Status |
|---|---|---|
| C1 | Gold labels are an uncertain reference (imagery shows vegetation and roof shape only) | recorded; use tags as silver labels, and treat agreement as consistency, not accuracy |
| C2 | 1991–2020 climate baseline | 2 of 60 Copernicus requests done; each takes ~40 h to start. Fallback: compute it from the hourly product (one request per year) |
| C3 | Evidence rows added after the blanket approval (11 Power Africa values) | covered by automated checks only |
| C4 | Low-confidence Google footprints (12.6%) count fully | consider weighting by confidence or checking against census households (A1) |
| C5 | Census urban/rural vs our urban rule differ (e.g. Kicukiro has 49 rural enterprises; our rule calls far more of Kirehe urban) | allocation uses our rule within each district's census setting totals; align the definitions if NISR's boundaries can be obtained |
| C6 | Licence review before publishing (ODbL share-alike, Meta RWI non-commercial) | pending |
| C7 | REG data (customers and consumption by village, feeder loads) | the single most valuable missing source: it would constrain both connection and consumption at village level |

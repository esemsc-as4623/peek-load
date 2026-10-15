# Open decisions and opportunities

*Companion to [`status_report.md`](status_report.md) · updated 2026-10-15*

Things we haven't settled, and places where the data could be pushed further. Each item says what the pipeline does
today, why it matters (with numbers where we have them), the options and a recommendation. Decisions already taken are
recorded in `data/gold/approvals.yaml`.

## The underlying problem: many allocations fit the totals

Most of what we know about demand is **aggregated**:

| Source | What it pins down | Level | Used as |
|---|---|---|---|
| 2022 census (RPHC5) | households | district × urban/rural | **constraint** (households per district) |
| Establishment Census 2023 | establishments by activity | district × urban/rural (activity nationally × setting) | **constraint** (shops, services, workshops) |
| LLM labels (deep dives) | use of every building in 3 sectors | building | training labels; class rates per setting |
| EICV7 2024 | share of households lighting with grid power | district | validation (grid model) |
| DHS 2025 | household electricity access | ~560 displaced clusters | grid-model label |
| MTF 2022 | appliance ownership, hours, spending | national × setting × wealth group | profiles |
| QSEL / REG | mean consumption of customers | Kigali vs rest of country | validation (profiles) |

Since 2026-10-15 the pipeline makes the choice explicit: a classifier gives every building use probabilities (the
*prior*), and census counts are placed on the most likely buildings (the *constraints*), rarest first, with spacing
caps. Every district now reproduces its census counts exactly, and QA checks it. What remains open is which further
totals to impose and how much to trust the prior.

## Resolved on 2026-10-15

| Was | Resolution | Evidence |
|---|---|---|
| A1 households per building | census households placed on the most likely homes, up to one per floor | without it, 1.7× too many households (`C4_households_fit.png`); Nasho now 9,046 households (was 12,484 units) |
| A3 shops: labels vs census | census retail (144k) placed on buildings, shop-houses where a household lands too | every district matches its census count |
| A9 use beyond the deep dives | classical classifier on all 6.4M buildings | B ranks held-out uses at AUC 0.78 (tags), 0.76 (LLM, held-out sector) |
| A10 Muhoza coverage | covered by the classifier and allocation | LLM labels kept as a check |
| Area-level "unlocated" amounts | removed: every unit of demand sits on a building | QA: weights = households + establishment, building by building |

## A. Needs a decision

| # | Item | What we do now | Why it matters | Options | Recommendation |
|---|---|---|---|---|---|
| A2 | **Services splits and the rural bar profile** | Census section I = 97% bars/restaurants, 3% accommodation; section S = 60% salons; one bar/restaurant profile (with a fridge) everywhere | ~60,200 bars/restaurants at 2.1 kWh/day is ~46 GWh/yr, 6% of national potential; Nasho gets 142 | (a) keep; (b) splits from the RDB register or census microdata; (c) a lighter rural cabaret profile | (c), and (b) when obtainable |
| A4 | **Rural manufacturing = grain mills** | All 9,080 rural manufacturing establishments get the 10 kW mill profile, one per ~0.7 km² cell at most | Nasho still gets **53 mills** (spacing stops clustering, not the count); ~86 GWh/yr nationally. Rural manufacturing also covers banana beer, bakeries, bricks, crafts | (a) keep (owner's rule); (b) a mill share (20–30%) with the rest as a light workshop; (c) the share from census microdata | (b) with a sensitivity range |
| A5 | **Town trade shares** | Tailoring 34%, welding 33%, carpentry 33% | No source; welding dominates the energy | keep, or census microdata | keep, show the range |
| A6 | **Allocation rules** (`config/allocation.yaml`) | Order rarest first; scores (viability × not-ancillary; P(commercial) × customers nearby; P(home)); spacing caps; ≥ 15 m² for establishments, ≥ 10 m² for homes; up to one household per floor | They decide *which* buildings carry each use; 6.5% of establishments needed a relaxed spacing cap | sign off, or adjust caps and scores | sign off as defaults; report sensitivity for the caps |
| A7 | **Uses the census doesn't count** (institutions, worship, stores) | LLM rate per setting × buildings, tags count towards it | Only 715 places of worship nationally (tags 562): the deep dives hold few churches, so the rate is near zero; Rwanda has thousands of churches. Institutions: 28,260 buildings (5,106 school, 2,532 health, 20,622 office: classroom blocks without a facility tag become offices) | (a) keep; (b) worship and school counts from registries (RGB, MINEDUC); (c) give buildings near a school facility the school profile | (b) + (c) |
| A8 | **Urban vs rural definitions** | Our rule (density ≥ 2,500/km² or a GHSL urban class) chooses household profiles and productive-use trades | Allocated urban household shares track the census in most districts (Musanze 0.49 vs 0.49) but not in the east (Kirehe 0.43 vs 0.07): our rule is loose there | (a) keep; (b) impose census urban/rural household shares per district; (c) NISR urban boundaries | (b): the census split is in hand and is one more constraint |
| A9 | **Applying the grid model** | P(connected) for every building, not used | Potential (782 GWh/yr) vs expected demand differ most in rural areas | expected = potential × P for households; rake to EICV7 district shares | do both |
| A10 | **Health facilities** | One profile built from MTF health *centres* | Fails its check; health posts use far less | split posts/centres by facility type; find a post source | split now (facility types are in the data) |
| A11 | **Validation after constraining** | Census households and establishments are now inputs | They can no longer validate the model; what's left: EICV7 grid shares (district), QSEL/REG consumption (setting), LLM labels (deep dives) | accept, or hold out a few districts' census counts as a test | hold out 5 districts once, report how close the unconstrained allocation lands |

## B. Opportunities to impute and fit better

| # | Opportunity | Data we have | Approach | Effort |
|---|---|---|---|---|
| B1 | **More constraints** | census urban/rural households, EICV7 grid shares, QSEL/REG consumption by setting | add them to the allocation (A8, A9), reporting how far each one moves the prior | small |
| B2 | **Seasonal and weekly shape** | monthly ERA5-Land climate, school calendar, harvest seasons (A: Dec–Feb, B: Jun–Jul) | seasonal RAMP variants: fans by cooling class, mills busy after harvests, schools closed in holidays | medium |
| B3 | **Growth over time** | yearly presence 2016–2023 per building | back-cast demand by year; project forward with district growth | medium |
| B4 | **Floors by ML** | 9,846 OSM-levelled buildings | a regression in place of the cut-points; floors cap households per building | small |
| B5 | **Classifier labels where it matters** | buildings where models A and B disagree, or near a census-placement threshold | label those (by hand or LLM) and retrain: few labels, most information | small |
| B6 | **Uncertainty bands** | p10/p90 daily kWh per profile; class probabilities; alternative allocations | re-run the allocation with sampled scores → p10/p50/p90 per building and area | medium |
| B7 | **Wealth within districts** | Meta RWI, DHS wealth index at clusters | calibrate the wealth split to DHS quintiles by district | small |
| B8 | **Night lights** | NASA VIIRS Black Marble (free Earthdata login) | classifier and grid-model feature; activity check | small once downloaded |

## C. Data and housekeeping

| # | Item | Status |
|---|---|---|
| C1 | Gold labels are an uncertain reference (imagery shows vegetation and roof shape only) | recorded; never trained on, reported as consistency |
| C2 | 1991–2020 climate baseline | 8 of 60 Copernicus requests done (1991–1994); fallback: compute from the hourly product |
| C3 | Evidence rows added after the blanket approval (11 Power Africa values) | covered by automated checks only |
| C4 | Low-confidence Google footprints (12.6%) are eligible like any other | consider down-weighting them in the allocation scores |
| C5 | Two tags on one building (a facility tag and a bar or guesthouse point within 30 m) | the building's own tag wins; the establishment is placed by the census step (82 cases) |
| C6 | Licence review before publishing (ODbL share-alike, Meta RWI non-commercial) | pending |
| C7 | REG data (customers and consumption by village, feeder loads) | still the most valuable missing source: village-level constraints for connection and consumption |

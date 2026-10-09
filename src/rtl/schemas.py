"""Table contracts: the interface between workstreams.

Every table written to data/interim or data/processed must validate against its schema here.
Workstreams may add columns only by changing this file (one reviewed change), never ad hoc.

Conventions
- One row per building, keyed by `bldg_id` = "RWA-" + 16 hex chars (see rtl.conform.ids).
- Geometries are stored in EPSG:4326; metric quantities are computed in EPSG:32735 (UTM 35S).
- Units are in column names (_m, _m2, _c, _kwh) so a column is self-describing.
- Nullable means "not observed", never "zero".
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry

BLDG_ID = r"^RWA-[0-9a-f]{16}$"
H3_R9 = r"^89[0-9a-f]{13}$"
H3_R7 = r"^87[0-9a-f]{13}$"

# Codebook classes (docs/codebook.md is the human-readable definition; keep these in sync).
LABEL_CLASSES = [
    "residential",
    "mixed_shop_house",
    "commercial",
    "institutional",
    "religious",
    "productive_use",
    "industrial_warehouse",
    "ancillary",
    "unknown",
]
LABEL_SOURCES = ["gold", "claude", "osm", "overture", "registry"]
FOOTPRINT_SOURCES = ["google", "microsoft", "osm"]
# QA flags are stored as a sorted, comma-joined string ("" if none).
QA_FLAGS = [
    "tiny", "invalid_fixed", "low_confidence", "overlaps_other_source", "nested_same_source", "outside_country",
]


def _ids(unique: bool = True) -> pa.Column:
    return pa.Column(str, pa.Check.str_matches(BLDG_ID), unique=unique, nullable=False)


def _flags_ok(s):
    allowed = set(QA_FLAGS)
    return s.fillna("").map(lambda v: all(f in allowed for f in v.split(",") if f))


# --------------------------------------------------------------------------- A1 footprints
buildings_base = pa.DataFrameSchema(
    {
        "bldg_id": _ids(),
        "source": pa.Column(str, pa.Check.isin(FOOTPRINT_SOURCES)),
        "source_confidence": pa.Column(float, pa.Check.in_range(0, 1), nullable=True),
        "area_m2": pa.Column(float, pa.Check.gt(0)),
        "perimeter_m": pa.Column(float, pa.Check.gt(0)),
        "compactness": pa.Column(float, pa.Check.in_range(0, 1.0001)),  # Polsby-Popper 4*pi*A/P^2
        "orientation_deg": pa.Column(float, pa.Check.in_range(0, 180), nullable=True),  # long-axis bearing mod 180
        "n_vertices": pa.Column(int, pa.Check.ge(3)),
        "lon": pa.Column(float, pa.Check.in_range(28.5, 31.2)),
        "lat": pa.Column(float, pa.Check.in_range(-3.0, -0.9)),
        "h3_r9": pa.Column(str, pa.Check.str_matches(H3_R9)),
        "qa_flags": pa.Column(str, pa.Check(_flags_ok, element_wise=False)),
        "geometry": pa.Column(Geometry(crs="EPSG:4326")),
    },
    strict=True,
    name="buildings_base",
)

building_height = pa.DataFrameSchema(
    {
        "bldg_id": _ids(),
        "height_m": pa.Column(float, pa.Check.in_range(0, 80), nullable=True),
        "height_source": pa.Column(str, nullable=True),
        "first_seen_year": pa.Column("Int64", pa.Check.in_range(2016, 2023), nullable=True),
        "est_floors": pa.Column("Int64", pa.Check.in_range(1, 30), nullable=True),
        "gfa_m2": pa.Column(float, pa.Check.gt(0), nullable=True),
        # raw annual presence at the centroid (0-1), kept so first_seen_year can be re-derived at any threshold
        **{f"presence_{y}": pa.Column(float, pa.Check.in_range(0, 1), nullable=True) for y in range(2016, 2024)},
    },
    strict=True,
    name="building_height",
)

# --------------------------------------------------------------------------- A2 context
building_context = pa.DataFrameSchema(
    {
        "bldg_id": _ids(),
        # admin hierarchy (names as in the chosen boundary source; codes where available)
        "adm1_name": pa.Column(str, nullable=True),  # province
        "adm2_name": pa.Column(str, nullable=True),  # district
        "adm3_name": pa.Column(str, nullable=True),  # sector
        "adm4_name": pa.Column(str, nullable=True),  # cell
        "adm5_name": pa.Column(str, nullable=True),  # village
        # OSM / Overture / registries (weak-label inputs, also context)
        "osm_building": pa.Column(str, nullable=True),
        "osm_amenity": pa.Column(str, nullable=True),
        "osm_shop": pa.Column(str, nullable=True),
        "osm_match": pa.Column(str, pa.Check.isin(["polygon", "point", "none"])),
        "overture_category": pa.Column(str, nullable=True),
        "overture_confidence": pa.Column(float, pa.Check.in_range(0, 1), nullable=True),
        "facility_type": pa.Column(str, nullable=True),
        # accessibility
        "dist_road_major_m": pa.Column(float, pa.Check.ge(0), nullable=True),
        "dist_road_any_m": pa.Column(float, pa.Check.ge(0), nullable=True),
        "dist_powerline_m": pa.Column(float, pa.Check.ge(0), nullable=True),
        "poi_count_50m": pa.Column(int, pa.Check.ge(0)),
        "poi_count_100m": pa.Column(int, pa.Check.ge(0)),
        "poi_count_250m": pa.Column(int, pa.Check.ge(0)),
        # rasters sampled at the footprint
        "ghsl_nres_share": pa.Column(float, pa.Check.in_range(0, 1), nullable=True),
        "ghsl_class": pa.Column(str, nullable=True),
        "pop_density_per_km2": pa.Column(float, pa.Check.ge(0), nullable=True),
        "rwi": pa.Column(float, nullable=True),
        "elevation_m": pa.Column(float, pa.Check.in_range(800, 4600), nullable=True),
        "h3_r7": pa.Column(str, pa.Check.str_matches(H3_R7)),
    },
    strict=True,
    name="building_context",
)

# Partial context tables: A2v (vector) and A2r (raster) each write one; the lead joins them on bldg_id
# into building_context. Column definitions are shared, so the final table can't drift from its parts.
CONTEXT_VECTOR_COLS = [
    "bldg_id", "adm1_name", "adm2_name", "adm3_name", "adm4_name", "adm5_name",
    "osm_building", "osm_amenity", "osm_shop", "osm_match", "overture_category", "overture_confidence",
    "facility_type", "dist_road_major_m", "dist_road_any_m", "dist_powerline_m",
    "poi_count_50m", "poi_count_100m", "poi_count_250m", "h3_r7",
]
CONTEXT_RASTER_COLS = ["bldg_id", "ghsl_nres_share", "ghsl_class", "pop_density_per_km2", "rwi", "elevation_m"]
context_vector = pa.DataFrameSchema(
    {c: building_context.columns[c] for c in CONTEXT_VECTOR_COLS}, strict=True, name="context_vector")
context_raster = pa.DataFrameSchema(
    {c: building_context.columns[c] for c in CONTEXT_RASTER_COLS}, strict=True, name="context_raster")

# --------------------------------------------------------------------------- A3 climate
_hourly = {f"t2m_h{h:02d}_c": pa.Column(float, pa.Check.in_range(-5, 45)) for h in range(24)}
climate_h3 = pa.DataFrameSchema(
    {
        "h3_r7": pa.Column(str, pa.Check.str_matches(H3_R7), unique=True),
        "elevation_m": pa.Column(float),
        "t2m_mean_c": pa.Column(float, pa.Check.in_range(0, 40)),
        "tmax_p95_c": pa.Column(float, pa.Check.in_range(0, 45)),
        "cdd18_per_year": pa.Column(float, pa.Check.ge(0)),
        "cdd22_per_year": pa.Column(float, pa.Check.ge(0)),
        "cdd24_per_year": pa.Column(float, pa.Check.ge(0)),
        "ghi_kwh_m2_day": pa.Column(float, pa.Check.in_range(0, 10), nullable=True),
        "cooling_class": pa.Column(str, pa.Check.isin(["low", "medium", "high"])),  # rule in config/params.yaml
        "years": pa.Column(str),
        "source": pa.Column(str),
        **_hourly,
    },
    strict=True,
    name="climate_h3",
)

# --------------------------------------------------------------------------- A4 evidence
evidence = pa.DataFrameSchema(
    {
        "evidence_id": pa.Column(str, unique=True),
        "parameter": pa.Column(str),  # e.g. "ownership_share.tv.rural_grid_households"
        "value": pa.Column(float, nullable=True),
        "value_low": pa.Column(float, nullable=True),
        "value_high": pa.Column(float, nullable=True),
        "unit": pa.Column(str),
        "population": pa.Column(str, nullable=True),
        "geography": pa.Column(str, nullable=True),
        "year": pa.Column("Int64", pa.Check.in_range(1990, 2026), nullable=True),
        "doc_id": pa.Column(str),
        "page": pa.Column("Int64", pa.Check.ge(1), nullable=True),
        "quote": pa.Column(str),
        "quote_verified": pa.Column(bool),  # quote found verbatim in the source text
        "model": pa.Column(str, nullable=True),
        "prompt_version": pa.Column(str, nullable=True),
        "human_verified": pa.Column(bool),
        "notes": pa.Column(str, nullable=True),
    },
    strict=True,
    name="evidence",
)

# --------------------------------------------------------------------------- A5 labels
labels = pa.DataFrameSchema(
    {
        "bldg_id": _ids(unique=False),  # one building can carry labels from several sources
        "label_source": pa.Column(str, pa.Check.isin(LABEL_SOURCES)),
        "label": pa.Column(str, pa.Check.isin(LABEL_CLASSES)),
        "confidence": pa.Column(float, pa.Check.in_range(0, 1), nullable=True),
        "probs_json": pa.Column(str, nullable=True),
        "abstain": pa.Column(bool),
        "model": pa.Column(str, nullable=True),
        "prompt_version": pa.Column(str, nullable=True),
        "labeler": pa.Column(str),
        "labeled_at": pa.Column("datetime64[ns, UTC]"),
    },
    strict=True,
    unique=["bldg_id", "label_source", "labeler"],
    name="labels",
)

SCHEMAS = {
    s.name: s
    for s in [buildings_base, building_height, context_vector, context_raster, building_context, climate_h3,
              evidence, labels]
}

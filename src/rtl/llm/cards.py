"""Context cards: what a labeller (human or Claude) sees for one building.

A card is a short text summary of the building's own attributes, its neighbourhood, the tags/places/facilities
near it, plus (optionally) a 150 x 150 m map rendered ONLY from open vector data (footprints, OSM roads,
POIs), so cards carry no imagery-licence restrictions. Human gold labellers and Claude see the identical card,
so their labels are comparable.

    pixi run cards                  # text cards + map images for the pilot sample (data/gold/sample_ids.csv)
    pixi run cards --deep-dives     # text cards for every building in the deep-dive sectors (no images)

Outputs: data/interim/cards/cards.parquet (bldg_id, text) and data/interim/cards/img/<bldg_id>.png.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor

import duckdb
import numpy as np
import pandas as pd
import shapely

from rtl.conform.buildings import OUT as BASE
from rtl.settings import GOLD_DIR, INTERIM_DIR, aoi

CARD_DIR = INTERIM_DIR / "cards"
IMG_DIR = CARD_DIR / "img"
LAYERS = INTERIM_DIR / "layers"
HALF_M = 75.0  # map half-width in metres
M_PER_DEG_LAT = 110_574.0


def m_per_deg_lon(lat: float) -> float:
    return 111_320.0 * np.cos(np.radians(lat))


def _bearing(dx: float, dy: float) -> str:
    deg = (np.degrees(np.arctan2(dx, dy)) + 360 + 22.5) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int(deg // 45)]


# --------------------------------------------------------------------------- data
def attributes(ids: pd.Series) -> pd.DataFrame:
    """Everything the text card needs, one row per building, joined in DuckDB."""
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3;")
    con.register("ids", pd.DataFrame({"bldg_id": ids}))
    return con.sql(f"""
        SELECT b.bldg_id, b.source, b.source_confidence, b.area_m2, b.perimeter_m, b.compactness, b.n_vertices,
               b.lon, b.lat, b.h3_r9, b.qa_flags, h.height_m, h.est_floors, h.gfa_m2, h.first_seen_year,
               c.* EXCLUDE (bldg_id), k.t2m_mean_c, k.tmax_p95_c
        FROM ids JOIN read_parquet('{BASE}') b USING (bldg_id)
        JOIN read_parquet('{INTERIM_DIR / "building_height.parquet"}') h USING (bldg_id)
        JOIN read_parquet('{INTERIM_DIR / "building_context.parquet"}') c USING (bldg_id)
        LEFT JOIN read_parquet('{INTERIM_DIR / "climate_h3.parquet"}') k ON k.h3_r7 = c.h3_r7
    """).df()


def neighbours(att: pd.DataFrame) -> pd.DataFrame:
    """Footprints around each target (h3 r9 cell + ring 1 covers > 150 m), for counts and the map."""
    con = duckdb.connect()
    con.sql("LOAD spatial; INSTALL h3 FROM community; LOAD h3;")
    con.register("t", att[["h3_r9"]].drop_duplicates())
    return con.sql(f"""
        WITH cells AS (SELECT DISTINCT unnest(h3_grid_disk(h3_r9, 1)) AS h FROM t)
        SELECT b.bldg_id, b.lon, b.lat, b.area_m2, ST_AsWKB(b.geometry) AS wkb, h.height_m,
               coalesce(c.facility_type, c.osm_amenity, c.osm_shop, c.overture_category,
                        nullif(c.osm_building, 'yes')) AS tag
        FROM read_parquet('{BASE}') b JOIN cells ON b.h3_r9 = cells.h
        LEFT JOIN read_parquet('{INTERIM_DIR / "building_height.parquet"}') h USING (bldg_id)
        LEFT JOIN read_parquet('{INTERIM_DIR / "building_context.parquet"}') c USING (bldg_id)
    """).df()


def points_layers() -> pd.DataFrame:
    """Named/tagged points from OSM POIs, Overture places and facility registries, with one 'kind' string."""
    q = f"""
        SELECT 'OSM' AS src, name, coalesce(poi_key || '=' || poi_value, 'poi') AS kind,
               ST_X(ST_Centroid(geometry)) AS lon, ST_Y(ST_Centroid(geometry)) AS lat
        FROM read_parquet('{LAYERS / "osm_pois.parquet"}')
        UNION ALL
        SELECT 'Overture', name, category || ' (conf ' || round(confidence, 2) || ')',
               ST_X(ST_Centroid(geometry)), ST_Y(ST_Centroid(geometry))
        FROM read_parquet('{LAYERS / "overture_places.parquet"}') WHERE confidence >= 0.3
        UNION ALL
        SELECT 'registry', name, facility_type, ST_X(ST_Centroid(geometry)), ST_Y(ST_Centroid(geometry))
        FROM read_parquet('{LAYERS / "facilities.parquet"}')
    """
    con = duckdb.connect()
    con.sql("LOAD spatial;")
    return con.sql(q).df()


# --------------------------------------------------------------------------- text
def _fmt(v, spec="{:.0f}", none="unknown"):
    return none if v is None or pd.isna(v) else spec.format(v)


def card_text(r: pd.Series, nb: pd.DataFrame, pts: pd.DataFrame) -> str:
    kx, ky = m_per_deg_lon(r.lat), M_PER_DEG_LAT
    dx, dy = (nb.lon - r.lon) * kx, (nb.lat - r.lat) * ky
    d = np.hypot(dx, dy)
    others = nb[(nb.bldg_id != r.bldg_id)].assign(d=d[nb.bldg_id != r.bldg_id])
    near50 = int((others.d <= 50).sum())
    bigger = others[(others.area_m2 > r.area_m2) & (others.d <= 25)].sort_values("d")
    plot_line = (f"nearest larger building {bigger.d.iloc[0]:.0f} m away ({bigger.area_m2.iloc[0]:.0f} m²)"
                 if len(bigger) else "no larger building within 25 m")

    px, py = (pts.lon - r.lon) * kx, (pts.lat - r.lat) * ky
    pd_ = np.hypot(px, py)
    near = pts.assign(d=pd_, dx=px, dy=py)[pd_ <= 100].sort_values("d").head(12)
    places = [f'- {p.src}: "{p["name"] or "(unnamed)"}", {p.kind}, {p.d:.0f} m {_bearing(p.dx, p.dy)}'
              for _, p in near.iterrows()]

    tags = [f"OSM building={r.osm_building}" if r.osm_building else None,
            f"OSM amenity={r.osm_amenity}" if r.osm_amenity else None,
            f"OSM shop={r.osm_shop}" if r.osm_shop else None,
            (f"Overture place: {r.overture_category} (conf {r.overture_confidence:.2f})"
             if r.overture_category else None),
            f"facility registry: {r.facility_type}" if r.facility_type else None]
    tags = [t for t in tags if t] or ["none"]

    return "\n".join([
        f"Building {r.bldg_id}: {r.adm5_name} village, {r.adm4_name} cell, {r.adm3_name} sector, "
        f"{r.adm2_name} district, Rwanda",
        f"Footprint: {r.area_m2:.0f} m², perimeter {r.perimeter_m:.0f} m, compactness {r.compactness:.2f} "
        f"(1 = circle, ~0.79 = square), {r.n_vertices} vertices; detected by {r.source}"
        + (f" (confidence {r.source_confidence:.2f})" if pd.notna(r.source_confidence) else "")
        + (f"; QA flags: {r.qa_flags}" if r.qa_flags else ""),
        f"Height: {_fmt(r.height_m, '{:.1f} m')}, est. floors {_fmt(r.est_floors)}, floor area "
        f"{_fmt(r.gfa_m2, '{:.0f} m²')}; first detected in imagery: "
        f"{_fmt(r.first_seen_year, '{:.0f}', 'not determined')}",
        f"Tags matched to this footprint ({r.osm_match} match): " + "; ".join(tags),
        f"Same plot: {plot_line}; {near50} other buildings within 50 m; "
        f"tagged places within 50/100/250 m: {r.poi_count_50m}/{r.poi_count_100m}/{r.poi_count_250m}",
        f"Settlement (GHSL 10 m): {r.ghsl_class}; non-residential share within 50 m: "
        f"{_fmt(r.ghsl_nres_share, '{:.0%}')}; population density {_fmt(r.pop_density_per_km2, '{:,.0f}/km²')}; "
        f"relative wealth index {_fmt(r.rwi, '{:+.2f}')} (Meta RWI, roughly -1.5 poor to +2 rich)",
        f"Access: nearest road {_fmt(r.dist_road_any_m, '{:.0f} m')}, nearest major road "
        f"{_fmt(r.dist_road_major_m, '{:.0f} m')}, nearest power line {_fmt(r.dist_powerline_m, '{:.0f} m')}; "
        f"elevation {_fmt(r.elevation_m, '{:.0f} m')}, mean temperature {_fmt(r.t2m_mean_c, '{:.1f} °C')}",
        "Named/tagged places within 100 m:" + ("\n" + "\n".join(places) if places else " none"),
    ])


# --------------------------------------------------------------------------- v2 cues
M_PER_DEG = 111_000.0  # near the equator a degree is ~111 km in both directions (cos 2deg = 0.999)
MARKET_WORDS = ("market", "marketplace")


def _s(v) -> str:
    """Text value or '' for None/NaN (OSM names are often missing)."""
    return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)


def v2_cues(att: pd.DataFrame, nb: pd.DataFrame, nb_by_t: pd.Series, pts: pd.DataFrame) -> list[str]:
    """Extra lines for card v2: cues the open vector data holds but v1 didn't show.

    land-use zone (smallest OSM landuse polygon containing the centroid), nearest road class and whether the
    building fronts it, footprint elongation, size relative to neighbours, distance to the nearest tagged
    place and to the nearest market. All distances are approximate metres (local degree scaling).
    """
    pt = shapely.points(att.lon, att.lat)
    con = duckdb.connect()
    con.sql("LOAD spatial;")
    lu = con.sql("SELECT landuse, name, ST_AsWKB(geometry) AS wkb "
                 f"FROM read_parquet('{LAYERS / 'osm_landuse.parquet'}')").df()
    lu_g = shapely.from_wkb(lu.wkb.map(bytes))
    li, lj = shapely.STRtree(lu_g).query(pt, predicate="within")
    lu_area = shapely.area(lu_g)
    best_lu = pd.DataFrame({"i": li, "j": lj, "a": lu_area[lj]}).sort_values("a").drop_duplicates("i").set_index("i").j
    roads = con.sql("SELECT highway, name, ST_AsWKB(geometry) AS wkb "
                    f"FROM read_parquet('{LAYERS / 'osm_roads.parquet'}')").df()
    r_g = shapely.from_wkb(roads.wkb.map(bytes))
    (ri, rj), rd = shapely.STRtree(r_g).query_nearest(pt, return_distance=True, all_matches=False)
    road_of = pd.Series(rj, index=ri)
    road_d = pd.Series(rd, index=ri) * M_PER_DEG
    p_g = shapely.points(pts.lon, pts.lat)
    (pi, pj), pdist = shapely.STRtree(p_g).query_nearest(pt, return_distance=True, all_matches=False)
    poi_of, poi_d = pd.Series(pj, index=pi), pd.Series(pdist, index=pi) * M_PER_DEG
    mk = pts.kind.str.lower().str.contains("|".join(MARKET_WORDS), na=False) | \
        pts["name"].fillna("").str.lower().str.contains("market")
    mk_g = p_g[mk.to_numpy()]
    if len(mk_g):
        (mi, _mj), md = shapely.STRtree(mk_g).query_nearest(pt, return_distance=True, all_matches=False)
        mkt_d = pd.Series(md, index=mi) * M_PER_DEG
    else:
        mkt_d = pd.Series(dtype=float)

    lines = []
    for i, r in enumerate(att.itertuples(index=False)):
        kx = m_per_deg_lon(r.lat)
        sub = nb.iloc[nb_by_t.get(i, np.array([], int))]
        own = sub[sub.bldg_id == r.bldg_id]
        if len(own):
            g = shapely.from_wkb(bytes(own.wkb.iloc[0]))
            gm = shapely.transform(g, lambda c, kx=kx, r=r: np.column_stack([(c[:, 0] - r.lon) * kx,
                                                                              (c[:, 1] - r.lat) * M_PER_DEG_LAT]))
            env = shapely.get_coordinates(shapely.oriented_envelope(gm))
            sides = sorted(np.hypot(*np.diff(env[:3], axis=0).T))
            shape = f"{sides[1]:.0f} x {sides[0]:.0f} m (elongation {sides[1] / max(sides[0], 0.1):.1f})"
        else:
            shape = "unknown"
        d = np.hypot((sub.lon - r.lon) * kx, (sub.lat - r.lat) * M_PER_DEG_LAT)
        ring = sub[(d <= 50) & (sub.bldg_id != r.bldg_id)]
        if len(ring):
            rel = (f"{r.area_m2 / ring.area_m2.median():.1f}x the median neighbour within 50 m; "
                   f"larger than {(ring.area_m2 < r.area_m2).mean():.0%} of them")
        else:
            rel = "no neighbours within 50 m"
        j = best_lu.get(i)
        landuse = (f"{lu.landuse.iloc[j]}" + (f' ("{_s(lu.name.iloc[j])}")' if _s(lu.name.iloc[j]) else "")
                   if j is not None else "none mapped")
        rd_i = road_d.get(i, np.nan)
        road = roads.iloc[road_of[i]] if i in road_of.index else None
        fronts = pd.notna(r.dist_road_any_m) and r.dist_road_any_m <= 5  # footprint edge within 5 m of a road
        road_line = (f"{road.highway}{' (' + _s(road['name']) + ')' if _s(road['name']) else ''}, "
                     f"{rd_i:.0f} m from the building centre{'; the building fronts the road' if fronts else ''}"
                     if road is not None else "none")
        poi = pts.iloc[poi_of[i]] if i in poi_of.index else None
        poi_line = (f'{poi_d[i]:.0f} m ({poi.kind}{", " + _s(poi["name"]) if _s(poi["name"]) else ""})'
                    if poi is not None else "none")
        mkt = f"{mkt_d[i]:.0f} m" if i in mkt_d.index else "none mapped"
        lines.append("\n".join([
            f"Shape: footprint {shape}; size {rel}",
            f"OSM land-use zone: {landuse}",
            f"Nearest road: {road_line}",
            f"Nearest tagged place anywhere: {poi_line}; nearest market: {mkt}",
        ]))
    return lines


# --------------------------------------------------------------------------- v3 cues
def _orientation(geoms_m: np.ndarray) -> np.ndarray:
    """Long-axis bearing (0-180 deg) of each footprint's minimum rotated rectangle."""
    env = shapely.oriented_envelope(geoms_m)
    out = np.full(len(geoms_m), np.nan)
    for k, e in enumerate(env):
        c = shapely.get_coordinates(e)
        if len(c) >= 4:
            a, b = c[1] - c[0], c[2] - c[1]
            v = a if np.hypot(*a) >= np.hypot(*b) else b
            out[k] = np.degrees(np.arctan2(v[0], v[1])) % 180
    return out


def _robust_z(x: float, ref: np.ndarray) -> float:
    ref = ref[np.isfinite(ref)]
    if len(ref) < 5 or not np.isfinite(x):
        return np.nan
    mad = np.median(np.abs(ref - np.median(ref))) * 1.4826
    return float((x - np.median(ref)) / mad) if mad > 0 else np.nan


def neighbour_labels() -> pd.Series:
    """Most recent Claude label per building (any model, prompt v2+), if labels exist yet."""
    from rtl.settings import PROCESSED_DIR

    path = PROCESSED_DIR / "labels.parquet"
    if not path.exists():
        return pd.Series(dtype=str)
    lab = pd.read_parquet(path, columns=["bldg_id", "label", "prompt_version", "labeled_at"])
    lab = lab[lab.prompt_version >= "label_v2"].sort_values("labeled_at")
    return lab.drop_duplicates("bldg_id", keep="last").set_index("bldg_id").label


def v3_cues(att: pd.DataFrame, nb: pd.DataFrame, nb_by_t: pd.Series) -> list[str]:
    """Card v3 lines: regularity of the surroundings, how unusual the building is for its neighbourhood, and the
    labels/tags of nearby buildings. Neighbourhood = other footprints with centroids within 100 m."""
    claude = neighbour_labels()
    lines = []
    for i, r in enumerate(att.itertuples(index=False)):
        kx = m_per_deg_lon(r.lat)
        sub = nb.iloc[nb_by_t.get(i, np.array([], int))]
        d = np.hypot((sub.lon - r.lon) * kx, (sub.lat - r.lat) * M_PER_DEG_LAT)
        ring = sub[(d <= 100) & (sub.bldg_id != r.bldg_id)]
        if len(ring) < 5:
            lines.append(f"Neighbourhood (100 m): only {len(ring)} other buildings; too few to judge regularity")
            continue
        g = shapely.transform(shapely.from_wkb(np.array([bytes(w) for w in sub.wkb])),
                              lambda c, kx=kx, r=r: np.column_stack([(c[:, 0] - r.lon) * kx,
                                                                      (c[:, 1] - r.lat) * M_PER_DEG_LAT]))
        ori = pd.Series(_orientation(g), index=sub.bldg_id.to_numpy())
        own_o = ori.get(r.bldg_id, np.nan)
        diff = np.abs(((ori.loc[ring.bldg_id] - own_o + 45) % 90) - 45)  # grid alignment ignores 90 deg turns
        aligned = float((diff <= 15).mean()) if np.isfinite(own_o) else np.nan
        cv = float(ring.area_m2.std() / ring.area_m2.mean())
        z_area = _robust_z(np.log(r.area_m2), np.log(ring.area_m2.to_numpy()))
        z_h = _robust_z(r.height_m if pd.notna(r.height_m) else np.nan, ring.height_m.to_numpy(dtype=float))
        pct = float((ring.area_m2 < r.area_m2).mean())
        unusual = (abs(z_area) >= 2) or (np.isfinite(z_h) and abs(z_h) >= 2)
        tags = ring.tag.dropna().value_counts().head(5)
        cl = claude.reindex(ring.bldg_id).dropna().value_counts()
        lines.append("\n".join([
            f"Neighbourhood regularity (100 m): {len(ring)} buildings; sizes "
            f"{'uniform' if cv < 0.5 else 'mixed' if cv < 1.0 else 'very mixed'} (CV {cv:.2f}); "
            + (f"{aligned:.0%} share this building's alignment" if np.isfinite(aligned) else "alignment unknown"),
            f"How unusual this building is here: area z = {z_area:+.1f} (larger than {pct:.0%} of neighbours)"
            + (f", height z = {z_h:+.1f}" if np.isfinite(z_h) else "")
            + (" -> OUT OF DISTRIBUTION for this neighbourhood" if unusual else " -> typical for this neighbourhood"),
            "Tags on nearby buildings (100 m): " + (", ".join(f"{k} x{v}" for k, v in tags.items()) or "none")
            + ("; Claude labels of nearby buildings: " + ", ".join(f"{k} x{v}" for k, v in cl.items())
               if len(cl) else ""),
        ]))
    return lines


# --------------------------------------------------------------------------- map image
def render_map(args: tuple) -> str:
    """150 x 150 m map around one building from open vector data only. Runs in a worker process."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.plotting import plot_polygon

    bid, lon, lat, nb_wkb, nb_ids, roads_wkb, pts_xy, out = args
    kx, ky = m_per_deg_lon(lat), M_PER_DEG_LAT

    def to_m(g):
        return shapely.transform(g, lambda c: np.column_stack([(c[:, 0] - lon) * kx, (c[:, 1] - lat) * ky]))

    fig, ax = plt.subplots(figsize=(4, 4), dpi=128)  # 512 px
    ax.set_facecolor("#f4f3ef")
    for wkb in roads_wkb:
        g = to_m(shapely.from_wkb(wkb))
        for part in getattr(g, "geoms", [g]):
            x, y = part.xy
            ax.plot(x, y, color="#ffffff", lw=5, solid_capstyle="round", zorder=1)
            ax.plot(x, y, color="#b9b7ae", lw=0.6, zorder=2)
    for wkb, oid in zip(nb_wkb, nb_ids, strict=True):
        g = to_m(shapely.from_wkb(wkb))
        target = oid == bid
        plot_polygon(g, ax=ax, add_points=False, facecolor="#e34948" if target else "#8f8d86",
                     edgecolor="#0b0b0b" if target else "#6b6964", linewidth=1.4 if target else 0.4,
                     zorder=4 if target else 3)
    for x, y in pts_xy:
        ax.plot((x - lon) * kx, (y - lat) * ky, marker="o", ms=5, color="#2a78d6", mec="white", mew=0.8, zorder=5)
    ax.set_xlim(-HALF_M, HALF_M)
    ax.set_ylim(-HALF_M, HALF_M)
    ax.set_aspect("equal")
    ax.plot([-HALF_M + 8, -HALF_M + 58], [-HALF_M + 8] * 2, color="#0b0b0b", lw=2)
    ax.text(-HALF_M + 33, -HALF_M + 11, "50 m", ha="center", fontsize=7)
    ax.text(HALF_M - 6, HALF_M - 12, "N↑", ha="right", fontsize=8)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.savefig(out, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deep-dives", action="store_true", help="text cards for all deep-dive buildings, no images")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, help="only the first N buildings (for a quick look)")
    ap.add_argument("--version", type=int, default=1, choices=[1, 2, 3],
                    help="2 = v1 text + extra vector cues; 3 = v2 + neighbourhood regularity / unusualness / labels")
    args = ap.parse_args()

    if args.deep_dives:
        dd = aoi()["deep_dives"]
        cond = " OR ".join(f"(adm2_name = '{v['district']}' AND adm3_name = '{v['sector']}')" for v in dd.values())
        ids = duckdb.sql(f"SELECT bldg_id FROM read_parquet('{INTERIM_DIR / 'building_context.parquet'}') "
                         f"WHERE {cond}").df().bldg_id
        images = False
    else:
        ids = pd.read_csv(GOLD_DIR / "sample_ids.csv").bldg_id
        images = True
    if args.limit:
        ids = ids.head(args.limit)
    if args.version >= 2:
        images = False  # v2+ are text-only (the map added little in the v1 ablation)

    att = attributes(ids)
    nb = neighbours(att)
    pts = points_layers()
    # neighbours per target: candidates whose centroid is within ~110 m (bbox), via one vectorised STRtree query
    tree = shapely.STRtree(shapely.points(nb.lon, nb.lat))
    boxes = shapely.box(att.lon - 0.001, att.lat - 0.001, att.lon + 0.001, att.lat + 0.001)
    t_idx, n_idx = tree.query(boxes)
    nb_by_cell = pd.DataFrame({"t": t_idx, "n": n_idx}).groupby("t").n.apply(np.asarray)
    ptree = shapely.STRtree(shapely.points(pts.lon, pts.lat))
    pt_t, pt_n = ptree.query(boxes)
    pts_by_t = pd.DataFrame({"t": pt_t, "n": pt_n}).groupby("t").n.apply(np.asarray)

    texts = []
    for i, r in enumerate(att.itertuples(index=False)):
        r = pd.Series(r._asdict())
        sub_nb = nb.iloc[nb_by_cell.get(i, np.array([], int))]
        sub_pts = pts.iloc[pts_by_t.get(i, np.array([], int))]
        texts.append(card_text(r, sub_nb, sub_pts))
    if args.version >= 2:
        texts = [t + "\n" + extra for t, extra in zip(texts, v2_cues(att, nb, nb_by_cell, pts), strict=True)]
    if args.version >= 3:
        texts = [t + "\n" + extra for t, extra in zip(texts, v3_cues(att, nb, nb_by_cell), strict=True)]
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    cards = pd.DataFrame({"bldg_id": att.bldg_id, "text": texts})
    suffix = f"_v{args.version}" if args.version >= 2 else ""
    path = CARD_DIR / (f"cards_deep_dives{suffix}.parquet" if args.deep_dives else f"cards{suffix}.parquet")
    cards.to_parquet(path, index=False)
    print(f"{len(cards):,} text cards -> {path}; median length {cards.text.str.len().median():.0f} chars")

    if images:
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        duckdb.sql("LOAD spatial;")
        roads = duckdb.sql(f"SELECT ST_AsWKB(geometry) AS wkb, ST_XMin(geometry) x0, ST_XMax(geometry) x1, "
                           f"ST_YMin(geometry) y0, ST_YMax(geometry) y1 "
                           f"FROM read_parquet('{LAYERS / 'osm_roads.parquet'}')").df() if True else None
        rtree = shapely.STRtree(shapely.box(roads.x0, roads.y0, roads.x1, roads.y1))
        r_t, r_n = rtree.query(boxes)
        roads_by_t = pd.DataFrame({"t": r_t, "n": r_n}).groupby("t").n.apply(np.asarray)
        jobs = []
        for i, r in enumerate(att.itertuples(index=False)):
            out = IMG_DIR / f"{r.bldg_id}.png"
            if out.exists():
                continue
            sub = nb.iloc[nb_by_cell.get(i, np.array([], int))]
            sp = pts.iloc[pts_by_t.get(i, np.array([], int))]
            jobs.append((r.bldg_id, r.lon, r.lat, [bytes(w) for w in sub.wkb], list(sub.bldg_id),
                         [bytes(w) for w in roads.wkb.iloc[roads_by_t.get(i, np.array([], int))]],
                         list(zip(sp.lon, sp.lat, strict=True)), str(out)))
        with ProcessPoolExecutor(args.workers) as pool:
            for k, _ in enumerate(pool.map(render_map, jobs, chunksize=20)):
                if k % 1000 == 0:
                    print(f"  rendered {k:,}/{len(jobs):,}")
        print(f"{len(list(IMG_DIR.glob('*.png'))):,} map images in {IMG_DIR}")


if __name__ == "__main__":
    main()

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
        SELECT b.bldg_id, b.lon, b.lat, b.area_m2, ST_AsWKB(b.geometry) AS wkb
        FROM read_parquet('{BASE}') b JOIN cells ON b.h3_r9 = cells.h
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
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    cards = pd.DataFrame({"bldg_id": att.bldg_id, "text": texts})
    path = CARD_DIR / ("cards_deep_dives.parquet" if args.deep_dives else "cards.parquet")
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

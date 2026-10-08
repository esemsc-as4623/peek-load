"""A2v: Gridfinder predicted medium-voltage lines (Arderne et al. 2020), clipped to Rwanda.

OSM maps only ~120 power lines in Rwanda, so Gridfinder fills the gaps for `dist_powerline_m`. It is a model
prediction (night lights + roads), not a survey: context cards should call it a "predicted grid line".
The global grid.gpkg is 725 MB; GDAL reads only the Rwanda envelope through the GeoPackage's spatial index.
"""

from __future__ import annotations

import geopandas as gpd
import pyogrio

from rtl.ingest.admin import country_polygon
from rtl.manifest import fetch
from rtl.settings import INTERIM_DIR, aoi

OUT = INTERIM_DIR / "layers" / "gridfinder_mv.parquet"


def main() -> None:
    gpkg = fetch("gridfinder", filename="grid.gpkg")
    lines = pyogrio.read_dataframe(gpkg, bbox=tuple(aoi()["country"]["bbox"])).to_crs("EPSG:4326")
    rwa = country_polygon().to_crs("EPSG:4326")
    clipped = gpd.clip(lines, rwa).explode(index_parts=False)
    clipped = clipped[clipped.geom_type == "LineString"].reset_index(drop=True)
    clipped[["geometry"]].assign(source="gridfinder").to_parquet(OUT)
    km = clipped.to_crs(aoi()["country"]["metric_crs"]).length.sum() / 1e3
    print(f"gridfinder: {len(lines):,} lines in bbox -> {len(clipped):,} segments in Rwanda, {km:,.0f} km -> {OUT}")


if __name__ == "__main__":
    main()

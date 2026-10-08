"""A2: download the Geofabrik OpenStreetMap extract for Rwanda."""

from rtl.manifest import fetch

if __name__ == "__main__":
    fetch("osm_geofabrik")

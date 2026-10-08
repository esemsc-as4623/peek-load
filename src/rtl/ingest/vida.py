"""A1: download the VIDA combined (Google V3 + Microsoft + OSM) building footprints for Rwanda."""

from rtl.manifest import fetch

if __name__ == "__main__":
    fetch("vida_buildings")

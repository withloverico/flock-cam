#!/usr/bin/env python3
"""
Export GeoJSON layers for the interactive web map (web/index.html).

Reuses the data pipeline in flock_choropleth.py: it fetches ACS B03002
demographics, TIGER/Line tract + city geometry, and the Flock camera KMZ, runs
the same spatial filtering, then writes lightweight EPSG:4326 GeoJSON files that
Leaflet can load directly from GitHub Pages.

    export CENSUS_API_KEY=xxxxxxxx
    python build_web_data.py --kmz "East Bay Flock Camera Locations.kmz"

Outputs:
    docs/data/tracts.geojson   choropleth polygons (pct_black_latino)
    docs/data/cameras.geojson  filtered camera points with normalized fields
    docs/data/cities.geojson   city outlines + the Oakland/Berkeley border line
"""

from __future__ import annotations

import argparse
import json
import os

import geopandas as gpd
import pandas as pd

import flock_choropleth as fc

WGS84 = "EPSG:4326"


def _clean(value):
    """Return a trimmed string, or None for empty / NaN values."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    # Normalize the non-breaking hyphen used in the Oakland records.
    return text.replace("‐", "-")


def normalize_cameras(cameras: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Collapse the per-layer schemas into one consistent set of popup fields."""
    cams = cameras.to_crs(WGS84).copy()
    rows = []
    for _, r in cams.iterrows():
        geom = r.geometry
        rows.append(
            {
                "city": _clean(r.get("NAME")),
                "name": _clean(r.get("Name")),
                "intersection": _clean(r.get("Intersection")),
                "street1": _clean(r.get("Street_Name_1")),
                "street2": _clean(r.get("Street_Name_2")),
                "camera_number": _clean(r.get("Camera_Number")),
                "address": _clean(r.get("Address")),
                "note": _clean(r.get("FIELD4")),
                "source": _clean(r.get("source_layer")),
                "lat": round(geom.y, 6),
                "lon": round(geom.x, 6),
            }
        )
    return gpd.GeoDataFrame(rows, geometry=cams.geometry.values, crs=WGS84)


def build_cities_layer(cities: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """City outlines plus the shared Oakland/Berkeley border as one layer."""
    cities_wgs = cities.to_crs(WGS84)
    features = [
        {"role": "city", "name": n, "geometry": g}
        for n, g in zip(cities_wgs["NAME"], cities_wgs.geometry.boundary)
    ]
    border = fc._shared_border(cities_wgs)
    if border is not None and not border.is_empty:
        features.append({"role": "border", "name": "Oakland / Berkeley", "geometry": border})
    return gpd.GeoDataFrame(features, geometry="geometry", crs=WGS84)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kmz", default="East Bay Flock Camera Locations.kmz")
    parser.add_argument("--out-dir", default="docs/data")
    args = parser.parse_args(argv)

    api_key = os.environ.get("CENSUS_API_KEY")
    if not api_key:
        raise SystemExit("Set the CENSUS_API_KEY environment variable first.")

    os.makedirs(args.out_dir, exist_ok=True)

    print("Fetching demographics ...")
    demo = fc.fetch_demographics(api_key)
    print("Fetching geometry ...")
    tract_geom = fc.fetch_tract_geometry()
    cities = fc.fetch_city_boundaries()
    print("Loading cameras ...")
    cameras = fc.load_cameras(args.kmz)

    print("Building clipped tracts ...")
    tracts = fc.build_tracts(demo, tract_geom, cities)
    cameras_in = fc.filter_cameras(cameras, cities)

    # Tracts: keep only what the map needs, round the share, simplify geometry.
    tracts_wgs = tracts.to_crs(WGS84)[["GEOID", "pct_black_latino", "geometry"]].copy()
    tracts_wgs["pct_black_latino"] = tracts_wgs["pct_black_latino"].round(4)
    tracts_wgs["geometry"] = tracts_wgs.geometry.simplify(0.0001, preserve_topology=True)

    cams_norm = normalize_cameras(cameras_in)
    cities_layer = build_cities_layer(cities)

    out_tracts = os.path.join(args.out_dir, "tracts.geojson")
    out_cams = os.path.join(args.out_dir, "cameras.geojson")
    out_cities = os.path.join(args.out_dir, "cities.geojson")

    tracts_wgs.to_file(out_tracts, driver="GeoJSON")
    cams_norm.to_file(out_cams, driver="GeoJSON")
    cities_layer.to_file(out_cities, driver="GeoJSON")

    print("\nWrote:")
    for path in (out_tracts, out_cams, out_cities):
        kb = os.path.getsize(path) / 1024
        print(f"  {path}  ({kb:.0f} KB)")
    print(f"\nCameras exported: {len(cams_norm)}  |  Tracts: {len(tracts_wgs)}")


if __name__ == "__main__":
    main()

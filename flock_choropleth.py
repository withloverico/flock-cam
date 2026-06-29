#!/usr/bin/env python3
"""
Choropleth of Oakland + Berkeley showing the share of Black & Latino residents
by census tract, with Flock ALPR camera locations overlaid as points and an
optional kernel-density (heat) surface of the cameras.

Data sources
------------
* Demographics : Census ACS 5-year, table B03002 (Hispanic or Latino Origin by
                 Race), tract level, Alameda County CA.
                     pct_black_latino = (B03002_004E + B03002_012E) / B03002_001E
                 (non-Hispanic Black alone + Hispanic/Latino of any race; the two
                 fields do not overlap, so adding them is correct.)
* Geometry     : TIGER/Line tracts + places via pygris. Tracts are clipped to the
                 Oakland and Berkeley city polygons.
* Cameras      : local KMZ (zipped KML). All folders/layers are read, then the
                 points are spatially joined to the two city polygons so cameras
                 in San Leandro, Hayward, Richmond, El Cerrito, etc. are dropped.

Usage
-----
    export CENSUS_API_KEY=xxxxxxxx
    python flock_choropleth.py --kmz "East Bay Flock Camera Locations.kmz" --kde

Outputs (high-res PNG, 300 dpi):
    choropleth_black_latino_cameras.png
    choropleth_black_latino_camera_kde.png   (only with --kde)
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from aquarel import load_theme
from matplotlib.lines import Line2D
from scipy.stats import gaussian_kde
from shapely.geometry import box

STATE_FIPS = "06"
COUNTY_FIPS = "001"
ACS_YEAR = 2022
TIGER_YEAR = 2022
TARGET_CITIES = ["Oakland", "Berkeley"]

PLOT_CRS = "EPSG:26910"

ACS_FIELDS = ("NAME", "B03002_001E", "B03002_004E", "B03002_012E")

SEQ_CMAP = "magma_r"
CAMERA_COLOR = "#00E5FF"
THEME_NAME = "arctic_light"


def fetch_demographics(api_key: str, year: int = ACS_YEAR) -> pd.DataFrame:
    """Pull ACS B03002 at the tract level for Alameda County and compute the
    Black + Latino share. Returns a DataFrame keyed by 11-digit tract GEOID."""
    from census import Census

    c = Census(api_key)
    records = c.acs5.state_county_tract(
        ACS_FIELDS, STATE_FIPS, COUNTY_FIPS, "*", year=year
    )
    df = pd.DataFrame(records)

    df["GEOID"] = df["state"] + df["county"] + df["tract"]

    for col in ("B03002_001E", "B03002_004E", "B03002_012E"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    total = df["B03002_001E"]
    black_latino = df["B03002_004E"] + df["B03002_012E"]
    df["pct_black_latino"] = np.where(total > 0, black_latino / total, np.nan)

    print(f"  ACS {year}: {len(df)} tracts pulled for Alameda County.")
    return df[["GEOID", "B03002_001E", "pct_black_latino"]]


def fetch_tract_geometry(year: int = TIGER_YEAR) -> gpd.GeoDataFrame:
    """TIGER/Line tract boundaries for Alameda County."""
    from pygris import tracts

    gdf = tracts(state=STATE_FIPS, county=COUNTY_FIPS, year=year, cache=True)
    print(f"  TIGER {year}: {len(gdf)} tract polygons.")
    return gdf[["GEOID", "geometry"]]


def fetch_city_boundaries(year: int = TIGER_YEAR) -> gpd.GeoDataFrame:
    """Oakland + Berkeley city ('place') polygons from TIGER/Line."""
    from pygris import places

    pl = places(state=STATE_FIPS, year=year, cache=True)
    cities = pl[pl["NAME"].isin(TARGET_CITIES)][["NAME", "geometry"]].copy()
    found = sorted(cities["NAME"].tolist())
    print(f"  City boundaries: {found}")
    if len(cities) != len(TARGET_CITIES):
        missing = set(TARGET_CITIES) - set(found)
        raise RuntimeError(f"Could not find city boundaries for: {missing}")
    return cities.reset_index(drop=True)


def load_cameras(kmz_path: str) -> gpd.GeoDataFrame:
    """Read every folder/layer of points out of a KMZ (zipped KML)."""
    if not os.path.exists(kmz_path):
        raise FileNotFoundError(kmz_path)

    with zipfile.ZipFile(kmz_path) as z:
        kml_names = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise RuntimeError("No .kml entry found inside the KMZ.")
        kml_bytes = z.read(kml_names[0])

    tmp_kml = os.path.join(
        os.path.dirname(os.path.abspath(kmz_path)), "._cameras_tmp.kml"
    )
    with open(tmp_kml, "wb") as fh:
        fh.write(kml_bytes)

    try:
        import pyogrio

        layers = [row[0] for row in pyogrio.list_layers(tmp_kml)]
        frames = []
        for layer in layers:
            g = gpd.read_file(tmp_kml, layer=layer)
            g = g[g.geometry.notna() & (g.geom_type == "Point")].copy()
            if len(g):
                g["source_layer"] = layer
                frames.append(g)
        cams = gpd.GeoDataFrame(
            pd.concat(frames, ignore_index=True), crs=frames[0].crs
        )
    finally:
        if os.path.exists(tmp_kml):
            os.remove(tmp_kml)

    cams["geometry"] = cams.geometry.apply(
        lambda geom: geom if geom is None else geom.simplify(0)
    )
    cams = cams.set_geometry(gpd.GeoSeries(
        gpd.points_from_xy(cams.geometry.x, cams.geometry.y), crs=cams.crs
    ))
    print(f"  KMZ: {len(cams)} camera points across {len(layers)} folders.")
    return cams


def filter_cameras(
    cameras: gpd.GeoDataFrame, cities: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Spatial-join the cameras to the city polygons; keep only the ones that
    land inside Oakland or Berkeley and print an inside/outside summary."""
    cams = cameras.to_crs(cities.crs)
    joined = gpd.sjoin(cams, cities, how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]

    inside = joined[joined["NAME"].notna()].copy()
    outside_n = len(joined) - len(inside)

    print("\n=== Camera filtering summary ===")
    print(f"  Total cameras read         : {len(cameras)}")
    print(f"  Inside Oakland/Berkeley    : {len(inside)}")
    print(f"  Outside (dropped)          : {outside_n}")
    for name, n in inside["NAME"].value_counts().items():
        print(f"      - {name:<10}: {n}")
    print("================================\n")

    return inside.drop(columns=[c for c in ("index_right",) if c in inside])


def build_tracts(
    demographics: pd.DataFrame,
    tract_geom: gpd.GeoDataFrame,
    cities: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Join demographics onto tract geometry and clip to the city polygons."""
    merged = tract_geom.merge(demographics, on="GEOID", how="left")
    city_union = cities.to_crs(merged.crs).geometry.unary_union
    clipped = gpd.clip(merged, city_union)
    clipped = clipped[~clipped.geometry.is_empty & clipped.geometry.notna()]
    print(f"  Tracts after clip to cities: {len(clipped)}")
    return clipped


def _shared_border(cities_p):
    """The line where the Oakland and Berkeley polygons touch. A small buffer
    makes the intersection robust to tiny gaps/overlaps in the TIGER edges."""
    if len(cities_p) < 2:
        return None
    geoms = list(cities_p.geometry)
    a, b = geoms[0], geoms[1]
    tol = max(a.length, b.length) * 1e-5
    return a.buffer(tol).boundary.intersection(b.buffer(tol))


def _draw_base(ax, tracts_p, cities_p):
    """Shared base layer: choropleth + city outlines."""
    tracts_p.plot(
        column="pct_black_latino",
        cmap=SEQ_CMAP,
        linewidth=0.3,
        edgecolor="white",
        ax=ax,
        legend=True,
        legend_kwds={
            "label": "Share Black + Latino",
            "shrink": 0.55,
            "format": lambda x, _: f"{x:.0%}",
        },
        missing_kwds={"color": "lightgrey", "label": "No data"},
    )
    cities_p.boundary.plot(ax=ax, color="#222222", linewidth=1.2, zorder=4)

    border = _shared_border(cities_p)
    if border is not None and not border.is_empty:
        gpd.GeoSeries([border], crs=cities_p.crs).plot(
            ax=ax, color="#111111", linewidth=2.6, linestyle="--", zorder=6,
        )
    ax.set_axis_off()


def make_choropleth(tracts_p, cameras_p, cities_p, out_path: str):
    """Main figure: choropleth + camera points."""
    with load_theme(THEME_NAME):
        fig, ax = plt.subplots(figsize=(11, 12))
        _draw_base(ax, tracts_p, cities_p)
        cameras_p.plot(
            ax=ax,
            color=CAMERA_COLOR,
            markersize=22,
            marker="o",
            edgecolor="black",
            linewidth=0.5,
            alpha=0.95,
            zorder=5,
        )
        handles = [
            Line2D([0], [0], marker="o", color="none",
                   markerfacecolor=CAMERA_COLOR, markeredgecolor="black",
                   markersize=9, label=f"Flock ALPR camera (n={len(cameras_p)})"),
            Line2D([0], [0], color="#111111", linewidth=2.6, linestyle="--",
                   label="Oakland / Berkeley border"),
        ]
        ax.legend(handles=handles, loc="lower left", frameon=True)
        ax.set_title(
            "Flock ALPR Cameras and the Black + Latino Population\n"
            "Oakland & Berkeley, by Census Tract",
            fontsize=16, fontweight="bold", pad=14,
        )
        fig.tight_layout()
        fig.savefig(out_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
    print(f"  Saved: {out_path}")


def make_kde_overlay(tracts_p, cameras_p, cities_p, out_path: str):
    """Optional figure: choropleth + Gaussian KDE heat surface of cameras."""
    if len(cameras_p) < 3:
        print("  Skipping KDE (need >= 3 cameras).")
        return

    xs = cameras_p.geometry.x.values
    ys = cameras_p.geometry.y.values
    kde = gaussian_kde(np.vstack([xs, ys]))

    minx, miny, maxx, maxy = cities_p.total_bounds
    pad_x = (maxx - minx) * 0.03
    pad_y = (maxy - miny) * 0.03
    gx = np.linspace(minx - pad_x, maxx + pad_x, 320)
    gy = np.linspace(miny - pad_y, maxy + pad_y, 320)
    mesh_x, mesh_y = np.meshgrid(gx, gy)
    density = kde(np.vstack([mesh_x.ravel(), mesh_y.ravel()])).reshape(mesh_x.shape)

    city_union = cities_p.geometry.unary_union

    with load_theme(THEME_NAME):
        fig, ax = plt.subplots(figsize=(11, 12))
        _draw_base(ax, tracts_p, cities_p)

        heat = ax.contourf(
            mesh_x, mesh_y, density,
            levels=12, cmap="inferno", alpha=0.45, zorder=3,
        )
        from matplotlib.path import Path
        from matplotlib.patches import PathPatch

        def _ring_codes(coords):
            codes = [Path.MOVETO] + [Path.LINETO] * (len(coords) - 2) + [Path.CLOSEPOLY]
            return codes

        verts, codes = [], []
        polys = (city_union.geoms if city_union.geom_type == "MultiPolygon"
                 else [city_union])
        for poly in polys:
            ring = np.asarray(poly.exterior.coords)
            verts.extend(ring)
            codes.extend(_ring_codes(ring))
        clip_patch = PathPatch(Path(verts, codes), transform=ax.transData)
        ax.add_patch(clip_patch)
        clip_patch.set_visible(False)
        if hasattr(heat, "collections"):
            for coll in heat.collections:
                coll.set_clip_path(clip_patch)
        else:
            heat.set_clip_path(clip_patch)

        cameras_p.plot(
            ax=ax, color=CAMERA_COLOR, markersize=12, marker="o",
            edgecolor="black", linewidth=0.3, alpha=0.9, zorder=5,
        )
        cbar = fig.colorbar(heat, ax=ax, shrink=0.55, pad=0.02)
        cbar.set_label("Camera density (KDE)")

        ax.set_title(
            "Flock ALPR Camera Density over the Black + Latino Population\n"
            "Oakland & Berkeley, by Census Tract",
            fontsize=16, fontweight="bold", pad=14,
        )
        fig.tight_layout()
        fig.savefig(out_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
    print(f"  Saved: {out_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kmz", default="East Bay Flock Camera Locations.kmz",
        help="Path to the Flock camera KMZ file.",
    )
    parser.add_argument(
        "--kde", action="store_true",
        help="Also render the camera kernel-density overlay.",
    )
    parser.add_argument(
        "--out-main", default="choropleth_black_latino_cameras.png",
    )
    parser.add_argument(
        "--out-kde", default="choropleth_black_latino_camera_kde.png",
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get("CENSUS_API_KEY")
    if not api_key:
        sys.exit("Set the CENSUS_API_KEY environment variable first.")

    print("Fetching demographics ...")
    demo = fetch_demographics(api_key)

    print("Fetching geometry ...")
    tract_geom = fetch_tract_geometry()
    cities = fetch_city_boundaries()

    print("Loading cameras ...")
    cameras = load_cameras(args.kmz)

    print("Building clipped tracts ...")
    tracts_clipped = build_tracts(demo, tract_geom, cities)

    cameras_in = filter_cameras(cameras, cities)

    tracts_p = tracts_clipped.to_crs(PLOT_CRS)
    cities_p = cities.to_crs(PLOT_CRS)
    cameras_p = cameras_in.to_crs(PLOT_CRS)

    print("Rendering ...")
    make_choropleth(tracts_p, cameras_p, cities_p, args.out_main)
    if args.kde:
        make_kde_overlay(tracts_p, cameras_p, cities_p, args.out_kde)

    print("Done.")


if __name__ == "__main__":
    main()

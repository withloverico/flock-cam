# Flock ALPR Cameras and Black + Latino Population: Oakland & Berkeley

## Overview

This repository contains the code, input data, and rendered outputs used to
produce the choropleth maps in this work: the share of Black and Latino
residents by census tract across Oakland and Berkeley, with Flock ALPR camera
locations overlaid. The maps were produced programmatically in Python.

The repository contains:

| File | Description |
|------|-------------|
| `flock_choropleth.py` | The complete, self-contained script that fetches the data, performs the spatial filtering, and renders both figures. |
| `East Bay Flock Camera Locations.kmz` | The source camera-location data (a zipped KML containing ALPR camera points across multiple East Bay cities). |
| `choropleth_black_latino_cameras.png` | Main figure: tract-level Black + Latino share with camera points overlaid. |
| `choropleth_black_latino_camera_kde.png` | Optional figure: the same choropleth with a kernel-density (heat) surface of camera locations. |

## How the Plot Was Built

The visualization is assembled from three independent data sources, joined on a
common projected coordinate system so the layers align:

1. **Demographics.** Tract-level estimates are pulled live from the U.S. Census
   Bureau's American Community Survey (ACS) 5-year program, table **B03002**
   (*Hispanic or Latino Origin by Race*), for Alameda County, California. The
   shaded value is a **percentage**, computed as:

   ```
   pct_black_latino = (B03002_004E + B03002_012E) / B03002_001E
   ```

   where `B03002_004E` is non-Hispanic Black alone, `B03002_012E` is Hispanic or
   Latino of any race, and `B03002_001E` is total population. These two
   categories do not overlap, so adding them is correct.

2. **Geometry.** TIGER/Line census-tract boundaries and city ("place") polygons
   are retrieved via `pygris`. The Alameda County tracts are clipped to the
   union of the Oakland and Berkeley city boundaries, and the shared
   Oakland/Berkeley border is drawn as an emphasized dividing line.

3. **Camera locations.** The KMZ file is unzipped and every folder/layer of the
   embedded KML is read. Because the file contains cameras from several cities
   (Richmond, El Cerrito, etc.), the points are spatially joined
   (`geopandas.sjoin`, `within` predicate) against the Oakland and Berkeley
   polygons, and only the cameras that fall inside those two cities are kept.

All layers are reprojected to **EPSG:26910** (UTM Zone 10N, meters) before
plotting. The figures are styled with the `aquarel` `arctic_light` theme and
exported as 300-dpi PNGs.

## How to Reproduce the Plot

### 1. Clone the repository

```bash
git clone https://github.com/withloverico/flock-cam.git
cd flock-cam
```

### 2. Set up a Python environment

Python 3.11 is recommended. Create a virtual environment and install the
dependencies:

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install geopandas pygris census aquarel scipy seaborn \
            matplotlib shapely mapclassify pyogrio
```

### 3. Provide a Census API key

The script reads the key from an environment variable (it is never hard-coded).
A free key can be requested at <https://api.census.gov/data/key_signup.html>.

```bash
export CENSUS_API_KEY=your_key_here   # Windows: set CENSUS_API_KEY=your_key_here
```

### 4. Run the script

```bash
# Main choropleth only:
python flock_choropleth.py --kmz "East Bay Flock Camera Locations.kmz"

# Main choropleth + the kernel-density overlay:
python flock_choropleth.py --kmz "East Bay Flock Camera Locations.kmz" --kde
```

This regenerates:

- `choropleth_black_latino_cameras.png`
- `choropleth_black_latino_camera_kde.png` (only with `--kde`)

The script also prints a summary to the console, including how many cameras fell
inside versus outside the two cities after the spatial filter.

### Notes on reproducibility

- The demographic figures are fetched live from the Census API; results will
  match as long as the same ACS vintage is used (the script defaults to the
  **2022** ACS 5-year estimates).
- TIGER/Line geometry is downloaded and cached by `pygris` on first run; an
  internet connection is required for the initial fetch.
- Optional command-line flags `--out-main` and `--out-kde` let you change the
  output filenames.

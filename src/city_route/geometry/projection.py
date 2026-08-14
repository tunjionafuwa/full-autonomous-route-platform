from __future__ import annotations

import pyproj


def get_local_utm_crs(lon: float, lat: float) -> pyproj.CRS:
    """Infer a local UTM CRS for metric distance and angle calculations."""
    epsg = 32600 + int((lon + 180) / 6) + 1
    if lat < 0:
        epsg = 32700 + int((lon + 180) / 6) + 1
    return pyproj.CRS.from_epsg(epsg)

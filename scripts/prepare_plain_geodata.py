"""Build the offline plains planning grids from the local September 11 archive."""

import argparse
from collections import Counter
from contextlib import closing
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BASEMAP = ROOT / "front_design/radio-planning-20261022/src/assets/terrain/plain.json"
LOCAL_ARCHIVE = ROOT / "data/imported/data_zip_20260911/raw"
sys.path.insert(0, str(ROOT / "scripts/geo"))
from shapefile import Dbf, Shp, clip_line

DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "service", "living_street", "track", "road",
}


def file_sha256(path):
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_gz(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0, compresslevel=6) as archive:
            archive.write(data)
    temporary.replace(path)


def raster_grid(source, output, planning_bbox, scale, expected_dtype, expected_sha=None):
    digest = file_sha256(source)
    if expected_sha and digest != expected_sha:
        raise ValueError(f"DEM checksum differs from the offline basemap source: {source}")
    with Image.open(source) as image:
        px, py, _ = image.tag_v2[33550]
        col0, row0, _, lon0, lat0, _ = image.tag_v2[33922]
        coord_sys = image.tag_v2[34735]
        if (col0, row0) != (0, 0) or not any(
            coord_sys[index:index + 4] == (2048, 0, 1, 4326)
            for index in range(4, len(coord_sys), 4)
        ):
            raise ValueError(f"Expected a north-up EPSG:4326 source: {source}")
        west, south, east, north = planning_bbox
        # Enclose the exact 120 km bounds in full raster cells plus a native margin
        # for interpolation, terrain slope sampling, and exact-edge station input.
        margin = 16 * scale
        left = math.floor((west - lon0) / px) - margin
        top = math.floor((lat0 - north) / py) - margin
        cols = math.ceil((math.ceil((east - lon0) / px) + margin - left) / scale)
        rows = math.ceil((math.ceil((lat0 - south) / py) + margin - top) / scale)
        right, bottom = left + cols * scale, top + rows * scale
        if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
            raise ValueError(f"Source does not contain the planning area plus raster margin: {source}")
        nodata = int(image.tag_v2[42113])
        native = np.asarray(image.crop((left, top, right, bottom)))[::scale, ::scale]
        if native.shape != (rows, cols) or not np.all(np.isfinite(native)):
            raise ValueError(f"Invalid raster shape or values: {source}")
        dtype = np.dtype(expected_dtype)
        if int(native.min()) < np.iinfo(dtype).min or int(native.max()) > np.iinfo(dtype).max:
            raise ValueError(f"Raster values do not fit the runtime data type: {source}")
        raster = native.astype(dtype, copy=False)
        data = raster.tobytes(order="C")
        source_bbox = [lon0, lat0 - image.height * py, lon0 + image.width * px, lat0]
    output_binary = output.with_suffix(".bin.gz")
    metadata = {
        "name": output.name,
        "crs": "EPSG:4326",
        "width": cols,
        "height": rows,
        "dtype": dtype.name,
        "byteorder": "little",
        "nodata": nodata,
        "origin_lon": lon0 + left * px,
        "origin_lat": lat0 - top * py,
        "pixel_lon": scale * px,
        "pixel_lat": scale * py,
        "row_direction": "north_to_south",
        "bbox": [lon0 + left * px, lat0 - bottom * py, lon0 + right * px, lat0 - top * py],
        "source_bbox": source_bbox,
        "planning_bbox": planning_bbox,
        "source": f"2026-09-11 地形采集档案 / {source.parent.name}/{source.name}",
        "source_sha256": digest,
        "nodata_count": int(np.count_nonzero(raster == nodata)),
        "raw_bytes": len(data),
        "data_file": output_binary.name,
        "data_sha256": hashlib.sha256(data).hexdigest(),
        "downsample_factor": scale,
    }
    if dtype.name == "int16":
        if metadata["nodata_count"]:
            raise ValueError("DEM includes missing pixels in the runtime crop")
        metadata["elevation_min"] = int(raster.min())
        metadata["elevation_max"] = int(raster.max())
    else:
        histogram = np.bincount(raster.ravel())
        metadata["class_histogram"] = {str(code): int(count) for code, count in enumerate(histogram) if count}
    write_gz(output_binary, data)
    write_json(output.with_suffix(".json"), metadata)
    return metadata


def prepare_roads(source_base, output, bounds):
    features = []
    source_shp = source_base.with_suffix(".shp")
    source_dbf = source_base.with_suffix(".dbf")
    with closing(Shp(source_shp)) as shape, closing(Dbf(source_dbf, encoding="gbk")) as records:
        for index, lines in shape:
            record = records.record(index)
            road_class = record.get("fclass", "")
            if road_class not in DRIVABLE:
                continue
            properties = {"fclass": road_class}
            if name := record.get("name"):
                properties["name"] = name
            for line in lines:
                for segment in clip_line(line, bounds):
                    features.append({
                        "type": "Feature",
                        "properties": properties,
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[round(x, 6), round(y, 6)] for x, y in segment],
                        },
                    })
    if not features:
        raise ValueError("No drivable roads were found in the plains extent")
    collection = {
        "type": "FeatureCollection",
        "name": "roads_taihang",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "properties": {
            "source": "2026-09-11 地形采集档案 / road_taihang_bbox.shp（OpenStreetMap，ODbL 1.0）",
            "bbox": bounds,
            "source_sha256": file_sha256(source_shp),
        },
        "features": features,
    }
    write_gz(output, json.dumps(collection, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return len(features)


def road_extent(planning_bbox):
    west, south, east, north = planning_bbox
    # Include roads in the same external margin used by RoadDistanceField.
    lat_mid = (south + north) / 2
    dlat = 5_000 / 111_320
    dlon = dlat / math.cos(math.radians(lat_mid))
    return [west - dlon, south - dlat, east + dlon, north + dlat]


def render_basemap(dem, grid_dir, map_dir):
    with gzip.open(grid_dir / dem["data_file"], "rb") as handle:
        raster = np.frombuffer(handle.read(), dtype="<i2").reshape(dem["height"], dem["width"])
    size = (896, 896)
    sampled = Image.fromarray(raster.astype(np.float32)).resize(size, Image.Resampling.BILINEAR)
    heights = np.asarray(sampled)
    intervals = [10, 25, 45, 75, 115, 200]
    palette = np.array([[34, 82, 68], [61, 109, 77], [103, 135, 89],
                        [150, 151, 107], [184, 163, 118], [212, 192, 151]])
    colors = np.stack([np.interp(heights, intervals, channel) for channel in palette.T], axis=-1)
    dy, dx = np.gradient(heights)
    relief = np.clip(1 + (dx - dy) * 0.018, 0.84, 1.17)
    image = Image.fromarray(np.uint8(np.clip(colors * relief[..., None], 0, 255)))
    map_dir.mkdir(parents=True, exist_ok=True)
    image_path = map_dir / "sr4-terrain.png"
    temporary = image_path.with_suffix(".png.tmp")
    image.save(temporary, format="PNG", optimize=True)
    temporary.replace(image_path)
    write_json(map_dir / "sr4-terrain.json", {
        "extent": dem["bbox"],
        "size": list(size),
        "projection": dem["crs"],
        "source": dem["source"],
        "source_sha256": dem["source_sha256"],
        "image_sha256": file_sha256(image_path),
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=LOCAL_ARCHIVE)
    parser.add_argument("--basemap", type=Path, default=BASEMAP)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    geodata = args.workspace / "geodata"
    raw = geodata / "raw"
    manifest = json.loads(args.basemap.read_text(encoding="utf-8"))
    extent = manifest["extent"]
    # The controlled archive contains a 21682 x 17248 single-band land-cover image.
    Image.MAX_IMAGE_PIXELS = 400_000_000
    dem = raster_grid(args.archive / "terrain/dem_taihang.tif", raw / "dem/dem_current",
                      extent, 1, "<i2", manifest["sourceSha256"])
    landcover = raster_grid(args.archive / "landcover/landcover_taihang.tif",
                            raw / "landcover/landcover_taihang", extent, 3, "u1")
    shutil.copyfile(args.archive / "landcover/landcover_legend.csv",
                    raw / "landcover/landcover_legend.csv")
    road_segments = prepare_roads(args.archive / "road/road_taihang/road_taihang_bbox",
                                  raw / "roads/roads_taihang.geojson.gz", road_extent(extent))
    render_basemap(dem, raw / "dem", geodata / "maps")
    print(json.dumps({
        "dem": [dem["width"], dem["height"], dem["elevation_min"], dem["elevation_max"]],
        "landcover": landcover["class_histogram"],
        "drivable_road_segments": road_segments,
        "planning_bbox": extent,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

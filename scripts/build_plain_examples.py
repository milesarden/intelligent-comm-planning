#!/usr/bin/env python3
"""Build a separate plain-terrain resource baseline and matching example imports."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))

from build_example_import_data import (FAULT_HEADERS, LINK_HEADERS, TASK_HEADERS,
                                       load_json, status_files, write_csv, write_json)
import datapaths


PLAIN_NOTICE = "适用平原地形场景。站点、设备状态与故障为构造例程，地形采用本地高程数据。"
PREFIX = "PLAIN-EXAMPLE"
OBSERVED_AT = datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc)
TEST_DATA = ("node.csv", "device.csv", "device_model.csv", "antenna_model.csv",
             "frequency_resource.csv", "interference_source.csv", "task_scenario.csv",
             "comm_demand.csv", "link_metric.csv", "candidate_site.csv")


def read_table(root: Path, name: str) -> list[dict[str, str]]:
    with (root / datapaths.PATHS[name]).open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def build_resources(data: Path, geodata: Path) -> tuple[dict, dict]:
    import terrain

    dem = geodata / "raw/dem/dem_current.json"
    landcover = geodata / "raw/landcover/landcover_taihang.json"
    roads_path = geodata / "raw/roads/roads_taihang.geojson.gz"
    if not all(path.is_file() for path in (dem, landcover, roads_path)):
        raise FileNotFoundError(f"缺少平原真实地理输入：{geodata}")
    dem_meta = load_json(dem)
    bbox = tuple(dem_meta["planning_bbox"])
    model = terrain.DemTerrain(str(dem), str(landcover))
    terrain.BBOX = bbox
    terrain.BBOX_DATA = model.bbox
    terrain.default_terrain = lambda verbose=True: model
    import gen_test_data as gen
    import gen_algorithm_design_data as design
    from roads import RoadDistanceField

    gen.rng = random.Random(20261116)
    gen.T0 = OBSERVED_AT
    gen.sid = lambda prefix, number: f"PL-{prefix}-{number:04d}"
    gen.dpath = lambda name: str(data / datapaths.PATHS[name])
    lon0, lat0, lon1, lat1 = bbox
    gen.TASK_AREAS = [
        (f"平原{name}任务区", lon0 + x * (lon1 - lon0), lat0 + y * (lat1 - lat0), radius)
        for name, x, y, radius in (
            ("西北", .23, .74, .14), ("西南", .23, .26, .16),
            ("东北", .75, .74, .18), ("东南", .75, .26, .18),
        )
    ]
    # Structured fault cases and symptom dictionary are shared product inputs.
    # Leave their IDs unchanged because diagnosis rules refer to them.
    gen.gen_fault_cases = lambda links, devices: []
    gen.gen_fault_scenarios = lambda links, devices, nodes: []
    gen.main()
    nodes = read_table(data, "node.csv")
    devices = read_table(data, "device.csv")
    models = {row["model_id"]: row["device_class"] for row in read_table(data, "device_model.csv")}
    counts = {band: sum(models[device["model_id"]] == band for device in devices) for band in ("HF", "VUHF")}
    if counts["HF"] < 50 or counts["VUHF"] < 100:
        raise RuntimeError(f"平原资源不足：{counts}")
    road_field = RoadDistanceField(str(roads_path), bbox)
    if road_field.occupied == 0:
        raise RuntimeError("平原候选点缺少道路网格")
    scan = design.CANDIDATE.Constraints(**(design.CANDIDATE_SCAN | {
        "grid_step_m": 1500.0, "elevation_range_m": (10.0, 195.0),
    }))
    candidates, reject_counts, scanned = design.CANDIDATE.scan(model, road_field, bbox, nodes, scan)
    if not candidates:
        raise RuntimeError("平原内没有可用候选点")
    sites = [dict(
        site_id=f"PL-{row['site_id']}", lon=row["lon"], lat=row["lat"],
        elevation_m=row["elevation_m"], slope_deg=row["slope_deg"],
        landcover_type=row["landcover_type"], nearest_road_distance_m=row["nearest_road_distance_m"],
        nearest_node_distance_m=row["nearest_node_distance_m"],
        candidate_role=design._candidate_role(row), is_deployable="true",
        recommendation_level=row["recommendation_level"], score=row["score"],
        reject_reason="", source="平原原始DEM、地物与道路数据网格筛选",
        remark="网格1500m，坡度≤15°，道路距离≤2000m，站间距≥3000m",
    ) for row in candidates]
    gen.write_csv("candidate_site.csv", sites, list(sites[0]))
    # Only the scenario-specific tables are staged. Preserve the product's own
    # fault rules, cases, dictionary, and supported model library.
    for name in ("symptom_dict.csv", "fault_case.csv", "fault_scenario.json"):
        (data / datapaths.PATHS[name]).unlink()

    tables = {name: read_table(data, name) for name in TEST_DATA}
    digest = hashlib.sha256()
    for name in TEST_DATA[:6]:
        path = data / datapaths.PATHS[name]
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    revision = "resource-" + digest.hexdigest()[:16]
    summary = {
        "resourceRevision": revision, "bbox": bbox, "stationCount": len(nodes),
        "stationCountByBand": {band: sum(band in row["device_class"].split(";") for row in nodes)
                               for band in ("HF", "VUHF")},
        "deviceCountByBand": counts, "candidateSiteCount": len(sites),
        "candidateSitesScanned": scanned, "excludedCandidates": reject_counts,
    }
    return {"revision": revision, "tables": tables}, summary


def task_and_links(name: str, task_id: str, bbox: list[float], station_ids: list[str],
                   links: list[dict], preset: str = "balanced") -> tuple[dict, list[dict]]:
    task = dict.fromkeys(TASK_HEADERS, "")
    task.update(task_id=task_id, task_name=name,
                description=PLAIN_NOTICE, area_bbox=";".join(map(str, bbox)),
                station_ids=";".join(station_ids), preset_id=preset, frequency_id="available-hf")
    demands = []
    for number, link in enumerate(links, 1):
        row = dict.fromkeys(LINK_HEADERS, "")
        row.update(
            demand_id=f"{task_id}-D{number:03d}", task_id=task_id,
            src_node_id=link["node_a_id"], dst_node_id=link["node_b_id"],
            priority="P1" if number <= 2 else "P2", is_mandatory="true" if number <= 2 else "false",
            endpoint_mode="station", service_type="话音", preferred_class=link["device_class"],
            required_rate_kbps="4.8", max_delay_ms="250", min_reliability="0.90",
            strategy="MAX_RELIABILITY", need_backup="false",
        )
        demands.append(row)
    return task, demands


def write_task(output: Path, task: dict, demands: list[dict], revision: str) -> None:
    write_csv(output / "01_task_scenarios.csv", TASK_HEADERS, [task])
    write_csv(output / "02_task_links.csv", LINK_HEADERS, demands)
    write_json(output / "00_适用资源.json", {
        "资源版本": revision, "地形": "平原", "任务编号": task["task_id"],
        "范围WGS84": list(map(float, task["area_bbox"].split(";"))),
        "站点数量": len(task["station_ids"].split(";")), "通联需求": len(demands),
        "操作": "在平原网络规划页面同时选两个 CSV，校验通过后确认、保存并提交规划。",
    })


def planning_files(data: Path, output: Path, resource: dict, bbox: list[float]) -> dict:
    sys.path.insert(0, str(PROJECT / "scripts/planning"))
    from echelon import relation_allowed

    links = [row for row in read_table(data, "link.csv") if row["is_available"] == "true"]
    nodes = resource["tables"]["node.csv"]
    node_types = {row["node_id"]: row["node_subtype"] for row in nodes}
    fixed = {row["node_id"] for row in nodes if row["mobility"] == "FIXED"}
    def endpoints(link):
        return {link["node_a_id"], link["node_b_id"]}

    normal_links = [row for row in links if relation_allowed(
        node_types[row["node_a_id"]], node_types[row["node_b_id"]], row["device_class"])]
    # One high-margin link in each band, with a common station and compatible
    # network hierarchy. Scenario-level verification uses the planning engine.
    first_links = []
    for band in ("HF", "VUHF"):
        connect_to = endpoints(first_links[0]) if first_links else fixed
        options = [row for row in normal_links if row["device_class"] == band
                   and endpoints(row) & connect_to]
        first_links.append(max(options, key=lambda row: (
            float(row["link_margin_db"]) >= 20.0,
            -float(row["distance_m"]), float(row["link_margin_db"]))))
    station_ids = sorted({row[key] for row in first_links for key in ("node_a_id", "node_b_id")})
    task, demands = task_and_links("平原短波与超短波通信规划", PREFIX + "-PLAN", bbox,
                                   station_ids, first_links)
    write_task(output / "01_规划任务", task, demands, resource["revision"])

    # The load example intentionally uses every site and varied pairs. It is
    # evaluated separately from the short startup example.
    seen = {frozenset(endpoints(row)) for row in first_links}
    load_links = list(first_links)
    for row in sorted(normal_links, key=lambda item: (-float(item["link_margin_db"]), item["link_id"])):
        pair = frozenset(endpoints(row))
        if pair in seen:
            continue
        seen.add(pair)
        load_links.append(row)
        if len(load_links) == 36:
            break
    load_task, load_demands = task_and_links("平原全量站点通信规划", PREFIX + "-CAPACITY", bbox,
                                             [row["node_id"] for row in nodes], load_links)
    write_task(output / "05_全量规划任务", load_task, load_demands, resource["revision"])

    all_station_ids = [row["node_id"] for row in nodes]
    failed_ids = set(random.Random(20261116).sample(all_station_ids, math.ceil(len(nodes) * .20)))
    active_ids = [site for site in all_station_ids if site not in failed_ids]
    remaining_links = [row for row in load_links if not endpoints(row) & failed_ids]
    failure_task, failure_demands = task_and_links("平原模拟节点失效后重新规划", PREFIX + "-CAPACITY-FAILURE",
                                                  bbox, active_ids, remaining_links)
    # Preserve the required/optional classification from the original task.
    original = {(row["src_node_id"], row["dst_node_id"], row["preferred_class"]): row
                for row in load_demands}
    for row in failure_demands:
        baseline = original[(row["src_node_id"], row["dst_node_id"], row["preferred_class"])]
        row.update(is_mandatory=baseline["is_mandatory"], priority=baseline["priority"])
    failure_out = output / "06_失效后重新规划任务"
    write_task(failure_out, failure_task, failure_demands, resource["revision"])
    write_json(failure_out / "03_模拟失效节点清单.json", {
        "原站点数": len(nodes), "失效站点数": len(failed_ids), "剩余站点数": len(active_ids),
        "失效站点编号": sorted(failed_ids), "原需求数": len(load_demands), "保留需求数": len(failure_demands),
        "使用说明": "任务输入中去除这些站点的引用和相关需求，资源台账中保留原站点。用于复测剩余任务输入。",
    })
    return {"primaryStationIds": station_ids, "primaryLinks": first_links,
            "loadDemandCount": len(load_demands), "failedStationCount": len(failed_ids),
            "postFailureDemandCount": len(failure_demands)}


def fault_files(output: Path, network: dict) -> None:
    devices = network["devices"]
    links = network["links"]
    hf_device = next(row for row in devices if row["device_class"] == "HF")
    vuhf_link = next(row for row in links if row["device_class"] == "VUHF")
    vuhf_device = next(row for row in devices if row["id"] == vuhf_link["device_a"])
    # Use actual frozen IDs. A new plan's link IDs may differ even when its
    # endpoints match, so rebuild after saving the plan on the current instance.
    definitions = [
        ("短波功放故障", "DEVICE", hf_device["id"], "HW_FAILURE", "SY-0001;SY-0002"),
        ("超短波链路中断", "LINK", vuhf_link["id"], "LINK_DOWN", "SY-0005;SY-0007"),
        ("超短波频率配置不一致", "DEVICE", vuhf_device["id"], "SW_CONFIG", "SY-0012"),
        ("超短波电磁干扰", "LINK", vuhf_link["id"], "EMI", "SY-0009;SY-0011"),
    ]
    rows = [dict(scenario_key=f"{PREFIX}-FAULT-{index}", scenario_name=f"平原{name}", description=PLAIN_NOTICE,
                 object_type=kind, object_id=object_id, fault_type=fault_type, symptom_ids=symptoms,
                 occur_time="", interference_id="")
            for index, (name, kind, object_id, fault_type, symptoms) in enumerate(definitions, 1)]
    write_csv(output / "故障场景_四类例程.csv", FAULT_HEADERS, rows)
    write_json(output / "00_适用方案.json", {
        "适用冻结方案": network["identity"], "场景数": len(rows),
        "核对": "选择本清单中的冻结方案导入，生成四份独立结果。新冻结方案须用对应对象重建。",
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--network", type=Path, help="已保存的平原方案的冻结网络 JSON，只补生成四条故障例程")
    args = parser.parse_args()
    examples = args.workspace / "examples"
    output = examples / "平原场景例程导入数据"
    data = examples / "runtime-data/data"
    if args.network:
        from build_example_import_data import network_data
        fault_files(output / "03_故障场景", network_data(load_json(args.network)))
        return
    if data.exists():
        raise FileExistsError(f"例程资源目录已存在：{data}")
    resource, summary = build_resources(data, args.workspace / "geodata")
    extra = planning_files(data, output, resource, summary["bbox"])
    status_files(output / "02_设备状态", resource["revision"], resource["tables"]["device.csv"],
                 OBSERVED_AT, None, "平原场景独立资源台账")
    sys.path.insert(0, str(PROJECT / "algorithm-service"))
    from app.task_file_parser import parse_task_files
    files = [(path.name, path.read_bytes()) for path in sorted((output / "01_规划任务").glob("*.csv"))]
    template = parse_task_files(files, {"constraints": {
        "connectivityPercent": 100, "maxRelayHops": 2, "survivabilityPercent": 90,
    }})
    if template["issues"] or template["input"] is None:
        raise ValueError(f"规划快捷例程校验失败：{template['issues']}")
    write_json(data / "synthetic/tasks/plain_start_example.json", template["input"])
    write_json(examples / "result.json", summary | extra)
    print(json.dumps(summary | extra, ensure_ascii=False))


if __name__ == "__main__":
    main()

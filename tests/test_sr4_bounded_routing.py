"""Regression checks for final SR4 route and single-link outage limits."""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "planning")]

import plan_check
import routing
import sr4_flow
import actual_search
import feasibility


def graph(edges):
    topology = routing.RouteGraph()
    for link_id, start, end, band, delay in edges:
        topology.add_link(link_id, start, end, band, 25, 1000, delay)
    topology.seal_band_switches()
    return topology


def demand(demand_id, start, end, mandatory=True):
    return dict(demand_id=demand_id, src_node_id=start, dst_node_id=end,
                is_mandatory=str(mandatory).lower())


def test_fast_path_uses_best_admissible_label_at_shared_vertex():
    g = graph([
        ("a1", "A", "B", "HF", 1), ("a2", "B", "C", "HF", 1),
        ("a3", "C", "P", "HF", 1), ("a4", "P", "D", "VUHF", 1),
        ("direct-to-p", "A", "P", "HF", 50),
    ])
    raw = routing.plan_one(g, "A", "D", "MIN_DELAY")
    bounded = routing.plan_one(g, "A", "D", "MIN_DELAY", max_radio_hops=3)
    assert raw["hop_count"] == 4
    assert bounded["link_path"] == ["direct-to-p", "a4"]
    assert bounded["band_switches"] == 1


def test_route_limit_keeps_missing_optional_and_mandatory_visible():
    g = graph([
        ("a1", "A", "B", "HF", 1), ("a2", "B", "C", "HF", 1),
        ("a3", "C", "D", "HF", 1), ("a4", "D", "E", "HF", 1),
    ])
    demands = [demand("a", "A", "E", False), demand("b", "E", "A")]
    result = routing.plan_routes(g, demands, max_relay_hops=2)
    assert result["unreachable_demand_ids"] == ["a", "b"]
    assert all(row["primary"] is None for row in result["routes"])
    checked = plan_check.check(route_result=result)
    assert not checked["passed"]
    by_id = {issue["demand_id"]: issue for issue in checked["issues"]}
    assert by_id["a"]["severity"] == "MODERATE"
    assert by_id["b"]["severity"] == "SEVERE"
    assert "最多 2 跳中继" in by_id["a"]["detail"]


def test_single_edge_survival_requires_bounded_replacement_for_same_task():
    g = graph([
        ("direct", "A", "E", "HF", 1),
        ("a1", "A", "B", "HF", 1), ("a2", "B", "C", "HF", 1),
        ("a3", "C", "D", "HF", 1), ("a4", "D", "E", "HF", 1),
    ])
    demands = [demand("key", "A", "E"), demand("extra", "A", "D", False)]
    routes = routing.plan_routes(g, demands, default_strategy="MIN_DELAY", max_relay_hops=2)
    assert routes["routes"][0]["primary"]["link_path"] == ["direct"]
    assert routes["routes"][0]["backup"] is None
    metrics = sr4_flow._bounded_task_metrics(g, demands, routes, 2)
    assert metrics["mandatory_connectivity"] == 1.0
    assert metrics["survivability"] == 0.0
    assert metrics["worst_single_failure_links"] == ["direct"]


def test_backup_search_finds_shortcuts_even_when_failed_edge_is_not_a_bridge():
    links = [
        ("direct", "A", "E"), ("a1", "A", "B"), ("a2", "B", "C"),
        ("a3", "C", "D"), ("a4", "D", "E"),
    ]
    topology = graph([(*edge, "HF", 1) for edge in links])
    demands = [demand("key", "A", "E")]
    result = {
        "links": [{"link_id": lid, "node_a_id": a, "node_b_id": b, "link_margin_db": 25}
                  for lid, a, b in links],
        "params": {"fade_margin_db": 6.0},
        "routes": routing.plan_routes(topology, demands, max_relay_hops=2),
    }
    assert actual_search.failure_context(result["links"], demands, 6.0)["cuts"] == []
    candidates = actual_search._bounded_repair_pairs(result, demands)
    assert candidates[("A", "D")] == (1, 2)
    assert candidates[("B", "E")] == (1, 2)
    assert candidates[("A", "E")] == (1, 1)
    assert ("A", "B") not in candidates


def test_candidate_loss_reuse_keeps_distinct_device_budgets(monkeypatch):
    calculated = []

    def path_loss(_terrain, a, b, f, pattern):
        calculated.append((a, b, f, pattern))
        return 120.0, True, 0.0, 1000.0

    monkeypatch.setattr(feasibility, "hf_path_loss", path_loss)
    radio = {"tx_dbm": 52, "gain": 3, "height": 10, "sens": -110, "bw": 3,
             "pattern": "OMNI", "fmin": 3e3, "fmax": 7e3}
    station = feasibility.Station("base", 114, 37, "I_FIXED", {"HF": radio})
    first = feasibility.Station("first", 114.01, 37, "VEHICLE_A",
                                {"HF": radio | {"tx_dbm": 49}}, is_candidate=True)
    second = feasibility.Station("second", 114.01, 37, "VEHICLE_B",
                                 {"HF": radio | {"tx_dbm": 46}}, is_candidate=True)
    terrain = object()
    matrix = feasibility.FeasibilityMatrix([station, first, second], terrain, candidate_pairs=False)
    assert len(calculated) == 1
    assert set(matrix.neighbors(0, "HF", damaged=True)) == {1, 2}
    assert matrix.margin_of(1, 2, "HF") is None
    assert matrix.margin_of(0, 1, "HF") == feasibility.link_margin(terrain, station, first, "HF")[0]
    assert matrix.margin_of(0, 2, "HF") == feasibility.link_margin(terrain, station, second, "HF")[0]
    assert matrix.margin_of(0, 1, "HF") > matrix.margin_of(0, 2, "HF")


def test_deferred_blocked_candidate_preserves_damage_mode(monkeypatch):
    attempted = []

    def margin(_terrain, a, b, *_options):
        attempted.append((a.sid, b.sid))
        return 20, 1000, 100

    monkeypatch.setattr(feasibility, "link_margin", margin)
    fixed = feasibility.Station("fixed", 114, 37, "I_FIXED", {"HF": {}})
    allowed = feasibility.Station("allowed", 114.01, 37, "I_MOBILE",
                                   {"HF": {}}, is_candidate=True)
    blocked = feasibility.Station("blocked", 114.02, 37, "MANPACK",
                                   {"HF": {}}, is_candidate=True)
    for i in range(2):
        matrix = feasibility.FeasibilityMatrix([fixed, allowed, blocked], object(), candidate_pairs=False,
                                               defer_blocked_candidates=bool(i))
        if i == 1:
            assert attempted == [("fixed", "allowed")]
        assert list(matrix.neighbors(0, "HF")) == [1]
        assert list(matrix.neighbors(0, "HF", damaged=True)) == [1, 2]
        assert matrix.blocked_by_echelon(0, "HF") == 1 << 2
        assert matrix.margin_of(2, 0, "HF") == 20
        assert matrix.stats()["HF"]["physical"] == 2
        assert attempted == [("fixed", "allowed"), ("fixed", "blocked")]
        attempted.clear()


def test_over_limit_manual_route_cannot_be_silently_replaced():
    g = graph([
        ("direct", "A", "E", "HF", 1),
        ("a1", "A", "B", "HF", 1), ("a2", "B", "C", "HF", 1),
        ("a3", "C", "D", "HF", 1), ("a4", "D", "E", "HF", 1),
    ])
    request = [demand("opt", "A", "E", False)]
    result = routing.plan_routes(g, request, max_relay_hops=2,
                                 manual_paths=[{"demand_id": "opt", "node_path": list("ABCDE")}])
    assert result["unreachable_demand_ids"] == ["opt"]
    assert result["routes"][0]["manual_errors"] == ["人工路径中继跳数超过 2 跳"]
    assert not plan_check.check(route_result=result)["passed"]

from minicloud.green.planner import (
    PlanInstance,
    PlannerConfig,
    PlanNode,
    decide,
    plan_consolidation,
)


def laptops() -> list[PlanNode]:
    return [PlanNode(f"laptop-{i}", vcpus=8, memory_mib=16384) for i in (1, 2, 3)]


def small(instance_id: str, node: str) -> PlanInstance:
    return PlanInstance(instance_id, node, vcpus=1, memory_mib=1024)


def test_light_load_is_packed_onto_one_laptop():
    plan = plan_consolidation(
        laptops(), [small("a", "laptop-1"), small("b", "laptop-2"), small("c", "laptop-3")]
    )
    assert plan.keep_awake == ["laptop-1"]
    assert sorted(plan.to_sleep) == ["laptop-2", "laptop-3"]
    assert {(m.instance_id, m.target) for m in plan.migrations} == {
        ("b", "laptop-1"),
        ("c", "laptop-1"),
    }
    assert plan.watts_after < plan.watts_before


def test_the_busiest_laptop_is_kept_so_fewer_instances_move():
    instances = [small(x, "laptop-2") for x in "abc"] + [small("d", "laptop-3")]
    plan = plan_consolidation(laptops(), instances)
    assert plan.keep_awake == ["laptop-2"]
    assert [m.instance_id for m in plan.migrations] == ["d"]


def test_a_pinned_laptop_never_sleeps():
    cfg = PlannerConfig(pinned=frozenset({"laptop-1"}))
    plan = plan_consolidation(laptops(), [small("a", "laptop-3")], cfg)
    assert plan.keep_awake == ["laptop-1"]
    assert "laptop-1" not in plan.to_sleep
    assert plan.migrations[0].target == "laptop-1"


def test_the_most_efficient_laptop_wins_when_load_is_equal():
    nodes = [
        PlanNode("laptop-1", 8, 16384, idle_watts=12),
        PlanNode("laptop-2", 8, 16384, idle_watts=6),
        PlanNode("laptop-3", 8, 16384, idle_watts=9),
    ]
    assert plan_consolidation(nodes, []).keep_awake == ["laptop-2"]


def test_heavy_load_keeps_more_laptops_awake():
    # Each laptop may use (16384 - 1024) * 0.8 = 12288 MiB, so three 4 GiB instances at most.
    instances = [
        PlanInstance(f"i{n}", "laptop-1" if n < 4 else "laptop-2", vcpus=2, memory_mib=4096)
        for n in range(7)
    ]
    plan = plan_consolidation(laptops(), instances)
    assert len(plan.keep_awake) == 3
    assert plan.to_sleep == []


def test_an_impossible_plan_changes_nothing():
    plan = plan_consolidation(laptops(), [PlanInstance("huge", "laptop-1", 4, 40000)])
    assert plan.to_sleep == []
    assert plan.migrations == []


def test_decide_consolidates_only_below_the_low_watermark():
    light = [small("a", "laptop-1"), small("b", "laptop-2"), small("c", "laptop-3")]
    assert decide(laptops(), light) is not None  # fill about 8%

    medium = [PlanInstance(f"i{n}", f"laptop-{n % 3 + 1}", 1, 2048) for n in range(9)]
    assert decide(laptops(), medium) is None  # fill 50%: leave it alone


def test_decide_wakes_a_laptop_when_the_awake_ones_are_full():
    nodes = [PlanNode("laptop-1", 8, 16384), PlanNode("laptop-2", 8, 16384, awake=False)]
    instances = [PlanInstance(f"i{n}", "laptop-1", 1, 2048) for n in range(6)]  # fill 100%
    plan = decide(nodes, instances)
    assert plan is not None
    assert plan.to_wake == ["laptop-2"]
    assert plan.watts_after > plan.watts_before

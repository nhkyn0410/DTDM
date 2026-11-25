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


def test_light_load_lets_one_laptop_sleep():
    plan = plan_consolidation(
        laptops(), [small("a", "laptop-1"), small("b", "laptop-2"), small("c", "laptop-3")]
    )
    assert plan.keep_awake == ["laptop-1", "laptop-2"]
    assert plan.to_sleep == ["laptop-3"]
    assert [(m.instance_id, m.target) for m in plan.migrations] == [("c", "laptop-1")]
    assert plan.watts_after < plan.watts_before


def test_at_most_one_of_three_laptops_sleeps_so_the_cluster_keeps_quorum():
    # Found on the lab cluster: with two of three members asleep, Incus answers
    # "no available cowsql leader server found" to every call.
    cfg = PlannerConfig(pinned=frozenset({"laptop-1"}))
    plan = plan_consolidation(laptops(), [], cfg)  # nothing to run at all
    assert len(plan.keep_awake) == 2
    assert plan.keep_awake[0] == "laptop-1"
    assert len(plan.to_sleep) == 1


def test_no_second_laptop_sleeps_while_one_already_does():
    # laptop-3 sleeps, so Incus reports only two members with capacity.
    awake = [PlanNode(f"laptop-{i}", vcpus=8, memory_mib=16384) for i in (1, 2)]
    cfg = PlannerConfig(pinned=frozenset({"laptop-1"}), cluster_size=3)
    plan = plan_consolidation(awake, [small("a", "laptop-2")], cfg)
    assert plan.keep_awake == ["laptop-1", "laptop-2"]
    assert plan.to_sleep == []


def test_two_laptops_can_be_packed_onto_one():
    # A two-member cluster keeps its database on one member (laptop-1, the pinned one).
    two = laptops()[:2]
    plan = plan_consolidation(two, [small("a", "laptop-2")])
    assert plan.keep_awake == ["laptop-2"]  # the busier one: nothing moves
    assert plan.to_sleep == ["laptop-1"]


def test_the_busiest_laptops_are_kept_so_fewer_instances_move():
    instances = [small(x, "laptop-2") for x in "abc"] + [small("d", "laptop-3")]
    plan = plan_consolidation(laptops(), instances)
    assert plan.keep_awake == ["laptop-2", "laptop-3"]
    assert plan.to_sleep == ["laptop-1"]
    assert plan.migrations == []


def test_a_pinned_laptop_never_sleeps():
    cfg = PlannerConfig(pinned=frozenset({"laptop-1"}))
    two = laptops()[:2]
    plan = plan_consolidation(two, [small("a", "laptop-2")], cfg)
    assert plan.keep_awake == ["laptop-1"]
    assert plan.to_sleep == ["laptop-2"]
    assert plan.migrations[0].target == "laptop-1"


def test_the_most_efficient_laptops_win_when_load_is_equal():
    nodes = [
        PlanNode("laptop-1", 8, 16384, idle_watts=12),
        PlanNode("laptop-2", 8, 16384, idle_watts=6),
        PlanNode("laptop-3", 8, 16384, idle_watts=9),
    ]
    plan = plan_consolidation(nodes, [])
    assert plan.keep_awake == ["laptop-2", "laptop-3"]
    assert plan.to_sleep == ["laptop-1"]


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

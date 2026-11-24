import pytest

from minicloud.scheduler import (
    Allocation,
    NoCapacityError,
    NodeCapacity,
    PlacementPolicy,
    choose_node,
)

NODES = [
    NodeCapacity("laptop-1", 8, 16384),
    NodeCapacity("laptop-2", 8, 16384),
    NodeCapacity("laptop-3", 4, 8192),
]
BUSY = {"laptop-1": Allocation(vcpus=2, memory_mib=4096, instances=2)}


def test_spread_picks_the_least_loaded_laptop():
    assert choose_node(NODES, BUSY, 1, 1024, PlacementPolicy("spread")) == "laptop-2"


def test_binpack_fills_the_busiest_laptop_first():
    assert choose_node(NODES, BUSY, 1, 1024, PlacementPolicy("binpack")) == "laptop-1"


def test_offline_or_sleeping_laptops_are_skipped():
    nodes = [NodeCapacity("laptop-1", 8, 16384, online=False), NodeCapacity("laptop-2", 8, 16384)]
    assert choose_node(nodes, {}, 1, 1024, PlacementPolicy("binpack")) == "laptop-2"


def test_memory_kept_for_the_laptop_itself_is_never_given_out():
    nodes = [NodeCapacity("small", 2, 2048)]
    policy = PlacementPolicy(reserved_memory_mib=1024)
    assert choose_node(nodes, {}, 1, 1024, policy) == "small"
    with pytest.raises(NoCapacityError):
        choose_node(nodes, {}, 1, 1025, policy)


def test_cpu_overcommit_sells_more_vcpus_than_cores():
    nodes = [NodeCapacity("laptop", 2, 65536)]
    used = {"laptop": Allocation(vcpus=7, memory_mib=1024)}
    policy = PlacementPolicy(cpu_overcommit=4.0)
    assert choose_node(nodes, used, 1, 1024, policy) == "laptop"
    with pytest.raises(NoCapacityError):
        choose_node(nodes, used, 2, 1024, policy)

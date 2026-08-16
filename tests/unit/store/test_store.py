import pytest

from sherpa.core.models import (
    DependencyPlane,
    DependencyType,
    InventorySnapshot,
    Resource,
    ResourceDependency,
    ResourceType,
    ScanConfig,
    Workload,
)
from sherpa.core.store import InventoryStore


def make_config() -> ScanConfig:
    return ScanConfig(aws_accounts=["123456789012"], aws_regions=["us-east-1"])


def make_resource(rid: str, deps: list[ResourceDependency] | None = None) -> Resource:
    return Resource(
        id=rid,
        resource_type=ResourceType.EC2_INSTANCE,
        region="us-east-1",
        account_id="123456789012",
        name="test",
        dependencies=deps or [],
    )


class TestInventoryStore:
    def test_save_and_load_empty_snapshot(self):
        store = InventoryStore()
        config = make_config()
        snap = InventorySnapshot(config=config)
        store.save_snapshot(snap)
        loaded = store.load_snapshot(snap.snapshot_id)
        assert loaded is not None
        assert loaded.snapshot_id == snap.snapshot_id
        assert loaded.resource_count == 0

    def test_save_and_load_resources(self):
        store = InventoryStore()
        r = make_resource("arn:aws:ec2:us-east-1:123:instance/i-aaa")
        snap = InventorySnapshot(config=make_config(), resources=[r])
        store.save_snapshot(snap)
        loaded = store.load_snapshot(snap.snapshot_id)
        assert loaded is not None
        assert len(loaded.resources) == 1
        assert loaded.resources[0].id == r.id

    def test_load_unknown_snapshot_returns_none(self):
        store = InventoryStore()
        assert store.load_snapshot("nonexistent") is None

    def test_list_snapshots_returns_ids_in_order(self):
        store = InventoryStore()
        config = make_config()
        ids = []
        for _ in range(3):
            snap = InventorySnapshot(config=config)
            store.save_snapshot(snap)
            ids.append(snap.snapshot_id)
        assert store.list_snapshots() == ids

    def test_snapshot_immutability(self):
        """Second save of same snapshot_id should raise (primary key conflict)."""
        store = InventoryStore()
        snap = InventorySnapshot(config=make_config())
        store.save_snapshot(snap)
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            store.save_snapshot(snap)

    def test_dependency_graph_and_bfs(self):
        store = InventoryStore()
        dep = ResourceDependency(
            source_id="arn:aws:lambda:us-east-1:123:function/fn",
            target_id="arn:aws:sqs:us-east-1:123:queue/q",
            dependency_type=DependencyType.READS_FROM,
            plane=DependencyPlane.CLOUD,
        )
        fn = make_resource("arn:aws:lambda:us-east-1:123:function/fn", deps=[dep])
        q = make_resource("arn:aws:sqs:us-east-1:123:queue/q")
        snap = InventorySnapshot(config=make_config(), resources=[fn, q])
        store.save_snapshot(snap)
        reachable = store.bfs_dependencies(
            snap.snapshot_id, "arn:aws:lambda:us-east-1:123:function/fn"
        )
        assert "arn:aws:sqs:us-east-1:123:queue/q" in reachable

    def test_orphan_resources(self):
        store = InventoryStore()
        r1 = make_resource("arn:aws:ec2:us-east-1:123:instance/i-aaa")
        r2 = make_resource("arn:aws:ec2:us-east-1:123:instance/i-bbb")
        wl = Workload(name="app", resource_ids=[r1.id])
        snap = InventorySnapshot(config=make_config(), resources=[r1, r2], workloads=[wl])
        store.save_snapshot(snap)
        orphans = store.orphan_resources(snap.snapshot_id)
        assert orphans == [r2.id]

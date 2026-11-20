import json
import sqlite3
from contextlib import closing

from warehouse.catalog.bootstrap import initialize_registry
from warehouse.catalog.classification import Classification
from warehouse.catalog.partitions import PartitionDefinition
from warehouse.catalog.repository import CatalogRepository
from warehouse.catalog.sources import SourceRegistration
from warehouse.close.freeze import freeze_partition
from warehouse.facts.amendments import amend_row
from warehouse.facts.batches import post_batch
from warehouse.facts.repository import FactRepository
from warehouse.facts.revisions import read_current_revision
from warehouse.mirror.admission import install_release
from warehouse.mirror.read import audit_partition, audit_release
from warehouse.mirror.repository import MirrorRepository
from warehouse.release.issue import issue_release


REVIEWS = {
    "P": (1, "basis-P", None),
    "Q": (2, "basis-Q", None),
    "S": (3, "basis-S", "basis-Q"),
    "R": (2, "basis-R", None),
}
NOTICE = ("撤Q", "basis-Q")


def prepare_snapshots(source):
    initialize_registry(
        source,
        [PartitionDefinition("甲")],
        [SourceRegistration("甲表", "甲")],
        [Classification("甲", "cooling"), Classification("甲", "storm")],
    )
    post_batch(source, "甲", "甲表", "甲潮班", [("cooling", 4), ("storm", 0)])
    catalog = CatalogRepository(source)
    facts = FactRepository(source)
    snapshots = {1: freeze_partition(source, "甲巡核1", "甲", 1)}
    for revision, liters in ((2, 12), (3, 13)):
        row = read_current_revision(catalog, facts, "甲").rows[0]
        amend_row(source, "甲", row.identity, row, row.category, liters)
        snapshots[revision] = freeze_partition(source, f"甲巡核{revision}", "甲", revision)
    return snapshots


def observe_v1():
    with closing(sqlite3.connect(":memory:")) as source:
        snapshots = prepare_snapshots(source)
        issued = {
            name: issue_release(source, name, snapshots[revision].snapshot_id)
            for name, (revision, _, _) in REVIEWS.items()
        }
    with closing(sqlite3.connect(":memory:")) as destination:
        mirror = MirrorRepository(destination)
        currents = []
        for name in ("P", "Q", "R", "S"):
            install_release(mirror, issued[name][1])
            current = audit_partition(mirror, "甲")
            currents.append([current.manifest.release_id, current.tally.liters])
        historical = {name: audit_release(mirror, name).tally.liters for name in REVIEWS}
    result = {
        "v1_current": currents,
        "v1_historical_liters": historical,
        "same_q_r_ledger_digest": issued["Q"][0].ledger_sha256 == issued["R"][0].ledger_sha256,
    }
    expected = {
        "v1_current": [["P", 4], ["Q", 12], ["Q", 12], ["S", 13]],
        "v1_historical_liters": {"P": 4, "Q": 12, "S": 13, "R": 12},
        "same_q_r_ledger_digest": True,
    }
    if result != expected:
        raise RuntimeError(f"V1 rehearsal mismatch: {result!r}")
    return result


if __name__ == "__main__":
    print(json.dumps(observe_v1(), ensure_ascii=False, sort_keys=True))

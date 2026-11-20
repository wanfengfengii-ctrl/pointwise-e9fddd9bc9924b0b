import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

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
from warehouse.mirror.admission import install_release, install_review_message
from warehouse.mirror.read import (
    audit_partition,
    audit_release,
    audit_reviewed_partition,
    audit_reviewed_release,
)
from warehouse.mirror.repository import MirrorRepository
from warehouse.mirror.review_repository import MirrorReviewRepository
from warehouse.release.issue import (
    export_review_basis,
    issue_release,
    issue_revocation,
    issue_reviewed,
)
from warehouse.release.review_format import decode_review_message, encode_review_v2, encode_revocation


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


def issue_review_artifacts(source, snapshots):
    artifacts = {}
    for name, (revision, basis_id, parent) in REVIEWS.items():
        artifacts[name] = issue_reviewed(
            source, name, snapshots[revision].snapshot_id, basis_id, parent
        )
    notice_id, basis_id = NOTICE
    artifacts[notice_id] = issue_revocation(source, notice_id, basis_id)
    artifacts["basis-Q-export"] = export_review_basis(source, basis_id)
    return artifacts


def _formal(mirror):
    audit = audit_reviewed_partition(mirror, "甲")
    return None if audit is None else [audit.manifest.release_id, audit.tally.liters]


def _status(mirror, name):
    audit, status = audit_reviewed_release(mirror, name)
    return [status, [row.liters for row in audit.rows], audit.tally.liters]


def observe_v2_primary():
    """13 L 退到 4 L，同版独立复核恢复 12 L，断源重开仍可查失效的 13 L 原账。"""
    with TemporaryDirectory() as directory:
        mirror_path = Path(directory) / "mirror.sqlite3"
        with closing(sqlite3.connect(":memory:")) as source:
            snapshots = prepare_snapshots(source)
            artifacts = issue_review_artifacts(source, snapshots)
        with closing(sqlite3.connect(mirror_path)) as destination:
            mirror = MirrorReviewRepository(destination)
            steps = []
            for name in ("P", "Q", "S"):
                install_review_message(mirror, artifacts[name])
            steps.append(["证据到齐", _formal(mirror), _status(mirror, "S")])
            install_review_message(mirror, artifacts["撤Q"])
            steps.append([
                "撤销后",
                _formal(mirror),
                _status(mirror, "Q"),
                _status(mirror, "S"),
            ])
            install_review_message(mirror, artifacts["R"])
            steps.append(["R 恢复", _formal(mirror), _status(mirror, "R")])
        # 断开源仓后重开镜像，失效的 S 原账仍可按发行号核对。
        with closing(sqlite3.connect(mirror_path)) as reopened:
            mirror = MirrorReviewRepository(reopened)
            s_audit, s_status = audit_reviewed_release(mirror, "S")
            reopened_view = {
                "status": s_status,
                "rows": [row.liters for row in s_audit.rows],
                "liters": s_audit.tally.liters,
                "origins": [[row.provenance.partition, row.provenance.source, row.provenance.batch,
                             row.provenance.position, row.provenance.accepted_revision]
                            for row in s_audit.rows],
                "formal": _formal(mirror),
            }
    expected_steps = [
        ["证据到齐", ["S", 13], ["eligible", [13, 0], 13]],
        ["撤销后", ["P", 4], ["revoked", [12, 0], 12], ["revoked", [13, 0], 13]],
        ["R 恢复", ["R", 12], ["eligible", [12, 0], 12]],
    ]
    expected_reopened = {
        "status": "revoked",
        "rows": [13, 0],
        "liters": 13,
        "origins": [["甲", "甲表", "甲潮班", 1, 1], ["甲", "甲表", "甲潮班", 2, 1]],
        "formal": ["R", 12],
    }
    if steps != expected_steps or reopened_view != expected_reopened:
        raise RuntimeError(f"V2 主场景不一致：steps={steps!r} reopened={reopened_view!r}")
    return {"steps": steps, "reopened": reopened_view}


def observe_v2_revocation_first():
    """撤销单先到，账件迟到仍记失格；重投不改变正式账。"""
    with TemporaryDirectory() as directory:
        mirror_path = Path(directory) / "mirror.sqlite3"
        with closing(sqlite3.connect(":memory:")) as source:
            snapshots = prepare_snapshots(source)
            artifacts = issue_review_artifacts(source, snapshots)
        with closing(sqlite3.connect(mirror_path)) as destination:
            mirror = MirrorReviewRepository(destination)
            install_review_message(mirror, artifacts["P"])
            install_review_message(mirror, artifacts["撤Q"])
            before = _formal(mirror)
            install_review_message(mirror, artifacts["S"])
            install_review_message(mirror, artifacts["Q"])
            late = {
                "Q": audit_reviewed_release(mirror, "Q")[1],
                "S": audit_reviewed_release(mirror, "S")[1],
            }
            install_review_message(mirror, artifacts["R"])
            recovered = _formal(mirror)
            # 合法重投：同号同字节，无新增实收。
            for name in ("Q", "S", "撤Q"):
                install_review_message(mirror, artifacts[name])
            after_replay = _formal(mirror)
    result = {"before": before, "late": late, "recovered": recovered, "after_replay": after_replay}
    expected = {
        "before": ["P", 4],
        "late": {"Q": "revoked", "S": "revoked"},
        "recovered": ["R", 12],
        "after_replay": ["R", 12],
    }
    if result != expected:
        raise RuntimeError(f"V2 撤销先到场景不一致：{result!r}")
    return result


def observe_v2_split_delivery():
    """依据分件送达：缺证 pending，补齐后 eligible；同版按发行号字典序取最小。"""
    with closing(sqlite3.connect(":memory:")) as source:
        snapshots = prepare_snapshots(source)
        p_artifact = issue_reviewed(source, "P", snapshots[1].snapshot_id, "basis-P")
        issue_reviewed(source, "Q", snapshots[2].snapshot_id, "basis-Q")
        s_artifact = issue_reviewed(source, "S", snapshots[3].snapshot_id, "basis-S", "basis-Q")
        q_basis = export_review_basis(source, "basis-Q")
        r_artifact = issue_reviewed(source, "R", snapshots[2].snapshot_id, "basis-R")
        q_artifact = issue_reviewed(source, "Q-2", snapshots[2].snapshot_id, "basis-Q-2")
    with closing(sqlite3.connect(":memory:")) as destination:
        mirror = MirrorReviewRepository(destination)
        install_review_message(mirror, p_artifact)
        # 去掉 S 随件祖先，模拟依据分件且 Q 依据尚未送达。
        message = decode_review_message(s_artifact)
        partial = encode_review_v2(message.manifest, message.basis, (), message.records)
        install_review_message(mirror, partial)
        pending = audit_reviewed_release(mirror, "S")[1]
        try:
            audit_reviewed_release(mirror, "Q")
            q_unreceived = None
        except ValueError as error:
            q_unreceived = str(error)
        formal_pending = _formal(mirror)
        install_review_message(mirror, q_basis)
        eligible = audit_reviewed_release(mirror, "S")[1]
        formal_complete = _formal(mirror)
    # 同版选择规则在全新镜像上单独演示：R、Q 顺序送达，字典序取 Q。
    with closing(sqlite3.connect(":memory:")) as tie_destination:
        tie_mirror = MirrorReviewRepository(tie_destination)
        install_review_message(tie_mirror, r_artifact)
        install_review_message(tie_mirror, q_artifact)
        tie = _formal(tie_mirror)
    result = {
        "pending": pending,
        "q_unreceived": q_unreceived,
        "formal_pending": formal_pending,
        "eligible": eligible,
        "formal_complete": formal_complete,
        "tie": tie,
    }
    expected = {
        "pending": "pending",
        "q_unreceived": "unreceived_release",
        "formal_pending": ["P", 4],
        "eligible": "eligible",
        "formal_complete": ["S", 13],
        "tie": ["Q-2", 12],
    }
    if result != expected:
        raise RuntimeError(f"V2 分件送达场景不一致：{result!r}")
    return result


def observe_v2_failure_keeps_state():
    """提交失败与坏件不留变化；重试原通知后正式账回退。"""
    with TemporaryDirectory() as directory:
        mirror_path = Path(directory) / "mirror.sqlite3"
        with closing(sqlite3.connect(":memory:")) as source:
            snapshots = prepare_snapshots(source)
            artifacts = issue_review_artifacts(source, snapshots)
        with closing(sqlite3.connect(mirror_path)) as destination:
            mirror = MirrorReviewRepository(destination)
            for name in ("P", "Q", "S"):
                install_review_message(mirror, artifacts[name])
            if _formal(mirror) != ["S", 13]:
                raise RuntimeError("失败演练前置正式账不是 S 13 L")
        def fail_commit():
            raise sqlite3.OperationalError("shift commit interrupted")

        failed_connection = sqlite3.connect(mirror_path)
        failed = MirrorReviewRepository(failed_connection, before_commit=fail_commit)
        try:
            try:
                install_review_message(failed, artifacts["撤Q"])
            except sqlite3.OperationalError as error:
                stored_error = str(error)
            else:
                stored_error = None
        finally:
            failed_connection.close()
        with closing(sqlite3.connect(mirror_path)) as reopened:
            mirror = MirrorReviewRepository(reopened)
            after_failure = _formal(mirror)
            # 坏件同样被拒绝，正式账保持。
            notice = decode_review_message(artifacts["撤Q"])
            bad_notice = encode_revocation(notice.notice_id, notice.basis)
            tampered = bad_notice[:-1] + (b" " if bad_notice[-1:] != b" " else b"x")
            try:
                install_review_message(mirror, tampered)
            except ValueError:
                pass
            after_bad = _formal(mirror)
            install_review_message(mirror, artifacts["撤Q"])
            after_retry = _formal(mirror)
    result = {
        "stored_error": stored_error,
        "after_failure": after_failure,
        "after_bad": after_bad,
        "after_retry": after_retry,
    }
    expected = {
        "stored_error": "shift commit interrupted",
        "after_failure": ["S", 13],
        "after_bad": ["S", 13],
        "after_retry": ["P", 4],
    }
    if result != expected:
        raise RuntimeError(f"V2 失败演练不一致：{result!r}")
    return result


def main():
    report = {
        "v1": observe_v1(),
        "v2_primary": observe_v2_primary(),
        "v2_revocation_first": observe_v2_revocation_first(),
        "v2_split_delivery": observe_v2_split_delivery(),
        "v2_failure_keeps_state": observe_v2_failure_keeps_state(),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()

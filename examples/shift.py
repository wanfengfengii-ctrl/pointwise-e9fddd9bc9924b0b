from contextlib import closing
import json
from pathlib import Path
import sqlite3
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
from warehouse.mirror.admission import install_release
from warehouse.mirror.read import audit_partition, audit_release
from warehouse.mirror.repository import MirrorRepository
from warehouse.release.issue import issue_release


def _sample(name):
    return json.loads(Path(__file__).with_name(name).read_text(encoding='utf-8'))


def _ledger_summary(ledger):
    return {
        'count': ledger.tally.count,
        'liters': ledger.tally.liters,
        'categories': [[item.category, item.count, item.liters] for item in ledger.tally.categories],
        'rows': [row.liters for row in ledger.rows],
    }


def _audit_summary(ledger):
    summary = _ledger_summary(ledger)
    summary['revision'] = ledger.manifest.revision
    summary['release_id'] = ledger.manifest.release_id
    summary['origins'] = [[row.provenance.partition, row.provenance.source, row.provenance.batch,
                           row.provenance.position, row.provenance.accepted_revision] for row in ledger.rows]
    return summary


def play_shift():
    registry = _sample('registry.json')
    shift = _sample('shift.json')
    artifacts = {}
    frozen = {}
    with TemporaryDirectory() as directory:
        source_path = Path(directory) / 'source.sqlite3'
        mirror_path = Path(directory) / 'mirror.sqlite3'
        with closing(sqlite3.connect(source_path)) as source:
            initialize_registry(source, [PartitionDefinition(name) for name in registry['partitions']],
                                [SourceRegistration(*entry) for entry in registry['sources']],
                                [Classification(*entry) for entry in registry['classifications']])
            receipts = [post_batch(source, **batch) for batch in shift['batches']]
            if post_batch(source, **shift['batches'][0]) != receipts[0]:
                raise RuntimeError('甲区重投没有取回原收据')
            change = shift['amendment']
            catalog = CatalogRepository(source)
            facts = FactRepository(source)
            current = read_current_revision(catalog, facts, change['partition'])
            old = next(row for row in current.rows if row.identity.position == change['position'])
            if old.liters != change['expected_liters']:
                raise RuntimeError('甲区原位在勘误前不符合班次旧像')
            amend_row(source, change['partition'], old.identity, old, change['category'], change['liters'])
            for request in shift['issues']:
                snapshot = freeze_partition(source, request['snapshot_id'], request['partition'], request['revision'])
                release_id = request['release_id']
                _, artifacts[release_id] = issue_release(source, release_id, snapshot.snapshot_id)
                frozen[release_id] = _ledger_summary(snapshot)
        installed = []
        with closing(sqlite3.connect(mirror_path)) as destination:
            mirror = MirrorRepository(destination)
            for release_id in shift['delivery_order']:
                result = install_release(mirror, artifacts[release_id])
                installed.append([result.receipt.release_id, result.current_revision])
        with closing(sqlite3.connect(mirror_path)) as reopened:
            mirror = MirrorRepository(reopened)
            audits = {
                '甲': _audit_summary(audit_partition(mirror, '甲')),
                '乙': _audit_summary(audit_partition(mirror, '乙')),
                '甲旧': _audit_summary(audit_release(mirror, '甲件1')),
                '乙发行': _audit_summary(audit_release(mirror, '乙件1')),
            }

    expected = {
        '甲件1': {'count': 3, 'liters': 14, 'categories': [['cooling', 3, 14]], 'rows': [7, 7, 0]},
        '甲件2': {'count': 3, 'liters': 15, 'categories': [['cooling', 3, 15]], 'rows': [8, 7, 0]},
        '乙件1': {'count': 1, 'liters': 4, 'categories': [['effluent', 1, 4]], 'rows': [4]},
    }
    alpha_origins = [['甲', '甲表', '甲早班', position, 1] for position in (1, 2, 3)]
    beta_origins = [['乙', '乙表', '乙早班', 1, 1]]
    downstream_expected = {
        'installed': [['甲件2', 2], ['乙件1', 1], ['甲件1', 2]],
        'audits': {
            '甲': dict(expected['甲件2'], release_id='甲件2', revision=2, origins=alpha_origins),
            '乙': dict(expected['乙件1'], release_id='乙件1', revision=1, origins=beta_origins),
            '甲旧': dict(expected['甲件1'], release_id='甲件1', revision=1, origins=alpha_origins),
            '乙发行': dict(expected['乙件1'], release_id='乙件1', revision=1, origins=beta_origins),
        },
    }
    downstream = {'installed': installed, 'audits': audits}
    if frozen != expected or downstream != downstream_expected:
        raise RuntimeError(f'固定账或离线核账与手算不一致。固定账 {frozen!r}，离线结果 {downstream!r}')
    return {
        'case_id': 'v5',
        'input': {'registry': registry, 'shift': shift},
        'expected': expected,
        'actual': frozen,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream,
        'code_paths': ['warehouse/facts/revisions.py', 'warehouse/close/freeze.py', 'warehouse/close/tally.py',
                       'warehouse/release/issue.py', 'warehouse/mirror/admission.py', 'warehouse/mirror/read.py'],
        'oracle': '按三条甲行和一条乙行手算笔数、各类升数及原始批位，并按实到版序核对当前指向',
    }


def main():
    report = play_shift()
    print('POINTWISE_SCENARIO ' + json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()

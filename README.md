# 临岬港区排放档案中心

设备按分区交送排放计量批。资料员凭原批收据核查设备重投，数据员凭完整旧像办理单行勘误。质控员可为指定历史修订签出固定账，夜班将交班件装入独立 SQLite 库，断开源仓后核对明细、出处、分类笔数和升数。

使用 Python 3.11 标准库。在项目根目录运行完整回归和交班演练。

```sh
python3 -B check_project.py
python3 -B -m examples.shift
python3 -B -m examples.review_handoff
```

`check_project.py` 由运行平台提供。源码独立导出时也可用 `python3 -B -m unittest discover -v` 运行同一套测试。演练读取 `examples/registry.json` 和 `examples/shift.json`，在临时目录使用独立源库和镜像库。复核演练 `examples/review_handoff.py` 在临时库中演示 V1 行为及 V2 的撤销回退、同版恢复、分件送达和断源重开户核。

## 一次交班

1. 资料员登记甲区、乙区及各区设备和合法分类。
2. 甲设备依次交送 7、7、0 L，乙设备交送 4 L。两区各建立修订 1。
3. 重投甲区原批，按首执复核原接纳修订。两个 7 L 在不同原位。
4. 核对甲区第一原位的完整旧像并更正至 8 L，甲区进入修订 2。
5. 质控员在更正后冻结甲区修订 1、2 和乙区修订 1。
6. 为三份固定账签出独立发行件并关闭源库。
7. 先装甲区新版和乙区件，再装迟到的甲区旧件。
8. 重开镜像。甲区当前三笔 15 L，乙区当前一笔 4 L，甲区旧发行号仍可复核三笔 14 L 及原出处。

## Python API

源仓入口接受打开的 `sqlite3.Connection`。传入连接使用默认事务模式，调用写入口时无外部活动写事务。同一个源仓的初始化、入批、勘误、冻结和签出使用同一个数据库文件。调用方负责关闭连接。镜像使用单独的连接构造 `MirrorRepository`。

| 操作 | 入口及结果 |
| --- | --- |
| `init` | `warehouse.catalog.bootstrap.initialize_registry(db, partitions, sources, classifications)` 接收 `PartitionDefinition(name)`、`SourceRegistration(source, partition)` 和 `Classification(partition, category)` 的序列，返回清册版本。重跑按登记内容比较，输入排列无关。 |
| `post` | `warehouse.facts.batches.post_batch(db, partition, source, batch, readings)` 接收非空有序序列，每项为 `(category, liters)`，返回 `BatchReceipt`。按有序原报识别重投。 |
| `amend` | `warehouse.facts.amendments.amend_row(db, partition, identity, expected, category, liters)` 接收原位、完整 `MeasurementRow` 旧像及新类别和升数，返回 `AmendmentReceipt`。旧像可从事实读取取得。 |
| 读取事实 | `warehouse.facts.revisions.read_fact_revision(catalog, facts, partition, revision)` 和 `read_current_revision(catalog, facts, partition)` 返回 `RevisionView`。两个仓储对象分别由 `CatalogRepository(db)` 和 `FactRepository(db)` 创建。 |
| `freeze` | `warehouse.close.freeze.freeze_partition(db, snapshot_id, partition, revision)` 返回 `FrozenSnapshot`，包含固定账号、区版、原序明细及统计。 |
| `issue` | `warehouse.release.issue.issue_release(db, release_id, snapshot_id)` 返回 `(IssueReceipt, artifact_bytes)`，可按原发行号和固定账号重领。 |
| `install` | `warehouse.mirror.admission.install_release(mirror, artifact_bytes)` 返回 `InstallResult(receipt, current_revision)`。重投同件返回原实收及该区实际当前修订。 |
| `audit` | `warehouse.mirror.read.audit_partition(mirror, partition)` 和 `audit_release(mirror, release_id)` 返回 `AuditResult(manifest, rows, tally)`。 |

### V2 复核账

| 操作 | 入口及结果 |
| --- | --- |
| 复核签发 | `warehouse.release.issue.issue_reviewed(db, release_id, snapshot_id, basis_id, parent_basis_id=None)` 从已冻结固定账签出 V2 账件，返回原发 `bytes`；`parent_basis_id=None` 为独立复核。同号同参重领原字节，异绑拒绝。 |
| 撤销 | `warehouse.release.issue.issue_revocation(db, notice_id, basis_id)` 对已登记依据签出撤销单 `bytes`，同号同参重领原件。 |
| 依据导出 | `warehouse.release.issue.export_review_basis(db, basis_id)` 返回可独立安装的依据件 `bytes`。 |
| V2 接纳 | `warehouse.mirror.admission.install_review_message(mirror, artifact)` 安装 V2 账件、撤销单或独立依据件，成功或合法重投返回 `None`。镜像用独立连接构造 `warehouse.mirror.review_repository.MirrorReviewRepository`，支持与 V1 相同的 `before_commit` 钩子。 |
| V2 户核 | `warehouse.mirror.read.audit_reviewed_partition(mirror, partition)` 返回正式 V2 账的 `AuditResult`，无合格件返回 `None`；`audit_reviewed_release(mirror, release_id)` 返回 `(AuditResult, status)`，`status` 为 `eligible`、`pending` 或 `revoked`，未实收报 `unreceived_release`。 |

V2 消息为 UTF-8 JSON 字节，外层 `version=2`，`kind` 取 `reviewed_release`、`review_revocation` 或 `review_basis`。每个依据号绑定唯一的分区、修订、规范账面摘要及父依据；父依据须同区严格较早修订，同版可建独立依据。镜像按当前可确认的继承链裁决：链上任一依据已收撤销为 `revoked`，无撤销但祖先缺证为 `pending`，证据齐备为 `eligible`。正式账只从合格实收账中取最高修订，同修订取发行号字典序最小。V2 与 V1 的实收和查询互不充当。

## 计量、身份与错误

标识使用非空 UTF-8 字符串，首尾空白视为非法。原位由分区、来源、批号及从 1 起的批内位置组成，原出处另记该批的接纳修订。每区修订从 0 开始。原序按接纳修订和批内位置排列。升数使用 Python 整数，布尔值、浮点、空值和数字字符串均拒绝。每行对其类别贡献一笔及该行的升数。类别按标识排序，空账有空明细、空分类和零总计。

V1 件有版本、清单和账面。清单绑定发行号、固定账号、分区、修订和账面的 SHA-256 摘要。摘要用于检验内容完整性。发行和实收保存原发字节。不同发行号可交接同一区版的等同账面。同号实收重投按字节比较，分区同修订按规范账面比较。镜像按实到明细逐类复算并按区独立推进。

业务输入错误和绑定冲突以带原因的 `ValueError` 报告。`unknown_partition` 表示没有对应区账，`unreceived_release` 表示未实收该发行号。`unknown_revision`、`unknown_row` 和 `unknown_snapshot` 分别指源侧缺失的对象。`stale_old_image` 表示旧像不符。存储错误透传 SQLite 异常。新批和勘误分别在一个事务内追加事实，实收与当前指向也共用一个事务。

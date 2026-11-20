# 复核交班

质控用已冻结的区账签出带复核依据的 V2 账件。夜班保存原实收，并凭当前收到的证据选出正式账。撤销影响正式资格，原发行明细与出处仍可凭号核对。

## Python 调用

源仓继续使用 README.md 所列的 SQLite 连接约定。镜像使用独立连接及 `MirrorRepository`。下表是新增入口。

| 模块与调用 | 结果 |
| --- | --- |
| `warehouse.release.issue.issue_reviewed(db, release_id, snapshot_id, basis_id, parent_basis_id=None)` | 从已有固定账签出 V2 账件，返回原发 `bytes`。`None` 表示独立复核。 |
| `warehouse.release.issue.issue_revocation(db, notice_id, basis_id)` | 对源仓中一个已登记依据签出具名撤销单，返回原发 `bytes`。 |
| `warehouse.release.issue.export_review_basis(db, basis_id)` | 采用分件送达时提供此入口，返回该依据可独立安装的 `bytes`。 |
| `warehouse.mirror.admission.install_review_message(mirror, artifact)` | 安装 V2 账件、撤销单，或所选交付方式提供的独立依据件。成功或合法重投返回 `None`。 |
| `warehouse.mirror.read.audit_reviewed_partition(mirror, partition)` | 返回正式 V2 账的 `AuditResult`。没有合格件时返回 `None`。 |
| `warehouse.mirror.read.audit_reviewed_release(mirror, release_id)` | 返回 `(AuditResult, status)`，其中 `status` 为 `eligible`、`pending` 或 `revoked`。未实收该账件时报 `ValueError`。 |

`AuditResult` 沿用 V1 的 `manifest`、`rows`、`tally`。V1 发行、接纳、当前账和历史读取继续走现有入口。V2 查询只裁决 V2 实收账，V1 账件不能充当 V2 复核证据。

## 依据、来件和资格

- 每个依据号在同一源仓只绑定一个分区、修订、规范账面摘要和父依据。父依据必须在源仓已登记，且属于同区严格较早的修订。后继和撤销必须锁定所指依据的确切绑定，含该依据自身的父绑定。独立复核可为同版同账另建依据。发行号绑定固定账号及本次复核申请，通知号绑定本次撤销对象，均沿用 V1 的标识规则。同号同参返回初次提交的字节，提交失败或异绑整次拒绝。撤销后仍可复领已签发的原件。
- 消息为 UTF-8 JSON 字节，外层 `version` 为整数 `2`。`kind` 使用 `reviewed_release`、`review_revocation` 或 `review_basis`。账件的 `manifest` 与 `ledger` 沿用 V1 两段的字段及规范账面摘要。依据的具体编码由实现选定，须能检查账与本次依据的绑定、确切父引用及通知指向。任一实现可选择随账交齐祖先，或独立交付依据。随件附上的完整依据和独立实收的同一依据具有相同效力。依据到场与使用该依据的账件到场分别记认。
- 镜像允许先收撤销及后继。引用可以先到，须等确切依据实收才能凭它完成复核。按当前可确认的继承链判定资格。链上有已收到撤销的依据时为 `revoked`，即使尚缺更早依据。其余情况下，所需依据齐备为 `eligible`，缺证为 `pending`。正式账仅从合格的实收账中选择最高修订，同修订按发行号字典序取最小。按号查询返回原账与查询时的资格。
- 同号账件和通知按原字节识别重投。具名依据及已接纳的确切引用须前后一致。同区同修订的账面须一致。拒绝格式错误、账面摘要或统计不符、错区、非早版父关系及与当前已收证据或绑定矛盾的来件。业务拒绝以 `ValueError` 报告。账件实收、依据和撤销对读取的影响在一次接纳提交后同时可见。坏件和失败提交保留此前实收与正式账。

## 交班样例

`examples/review_handoff.py` 的 `prepare_snapshots(source)` 生成同一批的三个冻结修订。甲区原报为 `cooling` 4 L、`storm` 0 L。两次更正只触及首行，冻结账原序依次为 `[4, 0]`、`[12, 0]`、`[13, 0]`。各账均为两笔，出处为甲表的甲潮班原位 1、2，接纳修订均为 1。

| 发行号 | 区版 | 依据号 | 父依据 | 总 L |
| --- | --- | --- | --- | --- |
| P | 1 | `basis-P` | 独立 | 4 |
| Q | 2 | `basis-Q` | 独立 | 12 |
| S | 3 | `basis-S` | `basis-Q` | 13 |
| R | 2 | `basis-R` | 独立 | 12 |

质控先签 P、Q、S、R，再对 `basis-Q` 签通知 `撤Q`。关闭源仓后按下列收件顺序核验。

- P、Q、S 的必要证据到齐后，正式账为 S、13 L。安装 `撤Q` 后退回 P、4 L。再收 R，正式账恢复为 R、12 L，Q 和 S 仍可按号领取且显示 `revoked`。重开镜像后，按 S 领到原序 `[13, 0]`、原出处及失效资格。
- 另一镜像先收 P 和 `撤Q`，重开才收 S、Q 及相关依据。正式账维持 P，实收的 Q 和 S 可按号复核且失效。安装 R 后恢复 12 L，重投 Q、S 和 `撤Q` 后仍读 R。
- 先收 P 再收 S，Q 的账件此时未到。若 S 已携齐祖先证据，应正式读 S，按 Q 的发行号仍报未实收。若采用分件且 Q 的依据未到，S 显示 `pending` 且正式账仍为 P。没有撤销时补齐确切的 Q 依据即可正式读 S。另以 R、Q 的顺序收两个各自合格的同版件，正式账选 Q。
- 在已经正式读到 13 L 时模拟 `撤Q` 的提交失败。用下列钩子打开同一镜像库并尝试安装，预期透传存储错误。再以正常连接重开，仍正式读 13 L，重试原通知成功后读 4 L。异绑或坏账面也应保留此前结果。

```python
import sqlite3

from warehouse.mirror.repository import MirrorRepository


def fail_commit():
    raise sqlite3.OperationalError("shift commit interrupted")


# destination 是打开的镜像库连接。
mirror = MirrorRepository(destination, before_commit=fail_commit)
```

`python3 -B -m examples.review_handoff` 使用相同冻结样例演示 V1 的行为。Q 与 R 的规范账面摘要相同，V1 收到 R 时仍指向先到的 Q，收到 S 后读 13 L。新增演示应给出实际实收后的 V2 回退、恢复和重开户核结果。

## 运行核验

`.pointwise-runtime.json` 已给出原交班的运行入口。沿用其 `version`、`kind`、`setup`、`verify` 和 `demo` 字段约定，将复核交班验证与演示接入。核验保留完整原回归及失败退出码。演示展示源仓关闭后镜像实读的结果。README.md 中的原演练仍可独立运行。

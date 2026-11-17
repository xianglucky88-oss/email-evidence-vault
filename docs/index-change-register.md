# 索引链路保全变更登记册

**登记册版本**：REG-2026-11-17
**冻结点**：`v0.1.0`（规则指纹见下）
**维护人**：下一轮维护者（本册随交接一起移交）
**上位手册**：`docs/evidence-index-maintenance-manual.md`

> 本登记册只记录**索引维护**相关的规则变更、需求缺口、冻结事件与回滚。
> **不记录**邮件解析功能的开发需求；若某项要求必须新增解析能力，只能登记为 `REQ-*` 并明确标注"不在本维护任务范围"，不得直接进入 `CHG-*`。

---

## 0. 冻结点基线

| 项 | 值 |
| --- | --- |
| 基线目录 | `docs/samples/baseline/v0.1.0/` |
| 样例输入 | `docs/samples/cases/`（12 个） |
| 期望输出 | 12 个 `*.expected.json` |
| 清单 | `manifest.json`（`evidence-index-manifest/v1`） |
| 规则指纹 `rules_sha256` | `05c64c0041cf1e4ce790a8a6932d328e51fcd1d084d8c70ae813edf5fccede06` |
| 校验方式 | `python3 tools/index_audit.py check` → 期望 `0 drift, manifest=verified, rules=unchanged`（退出码 0） |

`rules_sha256` 是对 `src/email_evidence_vault/*.py` 按文件名排序后逐个 SHA-256 再整体 SHA-256 的结果。它**不是**业务哈希，只用来发现"输出没变但规则变了"的静默修改。

### 0.1 冻结点样例指纹

| 样例 | thread_fp(前 12) | attachment_fp(前 12) | hold_fp(前 12) | 线程数 | 附件数 | held |
| --- | --- | --- | --- | --- | --- | --- |
| s01-missing-message-id | `988dfbf1f1a7` | `e3b0c44298fc` | `adae2a0359db` | 3 | 0 | 1 |
| s02a-cross-mailbox-duplicate | `b93527701632` | `211e0bd0a353` | `6a69dda46d73` | 2 | 1 | 1 |
| s02b-cross-mailbox-duplicate-reversed | `0a523f2a3d8c` | `211e0bd0a353` | `6a69dda46d73` | 2 | 1 | 1 |
| s03-attachment-addressing | `8d39d677e079` | `d8302982526b` | `4f53cda18c2b` | 3 | 3 | 0 |
| s04a-hold-before | `fecaca9dcca1` | `fb99c5d9ed94` | `4f53cda18c2b` | 2 | 1 | 0 |
| s04b-hold-active | `fecaca9dcca1` | `fb99c5d9ed94` | `3952b17fcb98` | 2 | 1 | 1 |
| s04c-hold-after-release | `fecaca9dcca1` | `fb99c5d9ed94` | `4f53cda18c2b` | 2 | 1 | 0 |
| s05-ref-chain-forward | `16425c749e64` | `e3b0c44298fc` | `4f53cda18c2b` | 2 | 0 | 0 |
| s06-cycle | `8b9edac44c08` | `e3b0c44298fc` | `4f53cda18c2b` | 3 | 0 | 0 |
| s07a-no-date-fields | `f12ccde8f891` | `e3b0c44298fc` | `4f53cda18c2b` | 1 | 0 | 0 |
| s07b-timezone-missing | `f12ccde8f891` | `e3b0c44298fc` | `4f53cda18c2b` | 1 | 0 | 0 |
| s08-unicode-normalization | `d5c0ee219e66` | `4602870701c2` | `4f53cda18c2b` | 1 | 2 | 0 |

说明：

- `e3b0c44298fc…` 是空集合的指纹（`sha256("")`），不是某个附件。附件为 0 的样例共享它。
- `4f53cda18c2b…` 是空 hold 的指纹。hold 为空的样例共享它。
- s04a 与 s04c 的全部指纹逐字节相同：**hold 解除是可逆的**。
- s07a 与 s07b 的全部指纹逐字节相同：**时区字段不参与索引**。
- s02a 与 s02b 附件/hold 指纹相同、线程指纹不同：**重复 id 使结果依赖输入顺序**。

---

## 1. 需求缺口登记（REQ）

状态取值：`OPEN`（未实现，需排期）、`ACCEPTED`（已认可需求，未实现）、`OUT_OF_SCOPE`（明确不属于本维护任务）、`CLOSED`（已实现）。

| ID | 需求 | 当前实现 | 影响类别 | 状态 | 验收要点 | 范围 |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-001 | 引用缺失/孤立回复应可追溯：区分"消息本身缺失"与"父引用缺失"，并输出不完整标记 | 一律生成幻影根，`status` 仍为 `ok`；仅 `inspect`/`manifest` 事后可见 | `THREAD` | OPEN | 引入任何回退/补齐规则前，必须先用 s01、s04a、s05 说明线程指纹变化 | 索引规则 |
| REQ-002 | 跨邮箱去重键应为 `(mailbox, message_id)`，并保留来源 | 无邮箱维度；重复 `id` 后写覆盖先写，结果依赖输入顺序（s02a/s02b） | `THREAD` + `HOLD` | OPEN | 需要新增输入字段；属于数据契约扩展 | 数据契约（需单独评审） |
| REQ-003 | hold 应冻结对应附件实体并阻止删除/篡改 | hold 只输出与已采集 `id` 的交集；与附件零关联 | `HOLD` | OPEN | 冻结必须可验证，且不得改变线程/哈希；当前样例只能证明"点名" | 索引 + 流程（解析不在范围） |
| REQ-004 | 转发嵌套：记录内嵌消息层级与来源 | 不解析 `body`；转发仅按 `refs` 参与线程 | `THREAD` | **OUT_OF_SCOPE** | 若将来实现，必须单独立项，且新增样例后再改线程规则 | 邮件解析（不在本任务） |
| REQ-005 | 时区缺失策略（拒绝 / 假定 UTC / 标记不确定） | 完全不读日期字段；s07a=s07b | `THREAD`（仅当引入时间排序时） | OPEN | 未定策略前，禁止引入按时间排序或时间窗 | 索引规则 |
| REQ-006 | 哈希冲突与命名歧义应区分并告警 | 无显式检测；只有 `inspect` 侧的两个异常项可区分 | `HASH` | OPEN | 必须同时覆盖"同内容不同名"与"同名不同内容"两类样例（s02a、s03） | 索引规则 + 审查 |
| REQ-007 | digest → 文件名多值映射 | 同 digest 只保留最后写入的名字，且输出不暴露名字 | `HASH`（命名层） | OPEN | 不得改变 digest 本身；属于附加信息 | 输出契约 |
| REQ-008 | 输出信封带 `schema_version` | 无版本字段；`status` 恒为 `ok` | `ENVELOPE` | OPEN | 加版本字段只能产生 `ENVELOPE` 分类 | 输出契约 |

---

## 2. 变更登记（CHG）

### 2.1 字段定义

| 字段 | 含义 |
| --- | --- |
| `id` | `CHG-<年份>-<序号>` |
| `status` | `PROPOSED` / `APPROVED` / `APPLIED` / `ROLLED_BACK` / `WITHDRAWN` / `SIMULATED` |
| `class` | `THREAD` / `HASH` / `HOLD` / `ENVELOPE`（可多选） |
| `scope` | 改动文件与函数 |
| `rules_before` / `rules_after` | `index_audit.py run` 生成的 `rules_sha256` |
| `case_impact` | 受影响的样例与前后指纹 |
| `baseline_decision` | `KEEP` / `NEW`（新建 v0.1.x）/ `UPDATE_IN_PLACE` |
| `legal_signoff` | 法务签字（`THREAD`/`HASH`/`HOLD` 必填） |
| `evidence` | 可复现的对比产物路径 |

### 2.2 CHG-2026-000：冻结点建立

| 字段 | 值 |
| --- | --- |
| status | `APPLIED` |
| class | `ENVELOPE`（仅建立基线，无规则变化） |
| scope | 新增 `docs/samples/cases/`、`docs/samples/baseline/v0.1.0/`、`tools/index_audit.py` |
| rules_before | n/a |
| rules_after | `05c64c0041cf1e4ce790a8a6932d328e51fcd1d084d8c70ae813edf5fccede06` |
| case_impact | 12 个样例首次冻结 |
| baseline_decision | `NEW`（v0.1.0） |
| legal_signoff | 待签 |
| evidence | `python3 tools/index_audit.py check` → 0 drift |

### 2.3 模拟变更（未合并，仅用于校准审阅判断）

以下 5 条 `SIMULATED` 记录是在**临时副本**上人为改动规则后跑出的真实对比结果，用于证明分类器与指纹能区分不同性质的修改。它们**没有**合并进本仓库；复现方式见每条 `evidence`。审阅真实变更时，应产出同结构的记录。

#### CHG-2026-SIM-01：父引用由末位改为首位

| 字段 | 值 |
| --- | --- |
| status | `SIMULATED` |
| class | `THREAD` |
| scope | `engine.calculate`：`r["refs"][-1]` → `r["refs"][0]` |
| rules_before | `05c64c0041cf1e4c…` |
| rules_after | `52308fad4bad2789…` |
| case_impact | 仅 `s05-ref-chain-forward`：thread_fp `16425c749e64…` → `67a9deaac262…`；根 `root` 成员 `["leaf","mid","root"]` → `["leaf","mid","other","root"]`；根 `ghost` 消失；`phantom_roots` 异常消失；attachment_fp 与 hold_fp 不变 |
| 其余 11 样例 | `IDENTICAL` |
| baseline_decision | 若真实合并：`NEW`（v0.1.1），v0.1.0 只读保留 |
| legal_signoff | 必需 |
| evidence | `index_audit.py compare --before <v0.1.0 结果> --after <变体结果>`，JSON 中 `classification=["THREAD"]` |

**审阅结论**：会改变既有线程，不改变原始哈希。属于必须重冻并签字的变更。

#### CHG-2026-SIM-02：附件内容哈希前做 NFC 归一化

| 字段 | 值 |
| --- | --- |
| status | `SIMULATED` |
| class | `HASH` |
| scope | `engine.calculate`：`sha256(content)` → `sha256(unicodedata.normalize("NFC", content))` |
| rules_before | `05c64c0041cf1e4c…` |
| rules_after | `b91dda2110ac53ed…` |
| case_impact | 仅 `s08-unicode-normalization`：`attachment_count` 2 → 1；`attachment_fingerprint` **未变**（独立 oracle 仍按原始字节计算，输出指纹因此与引擎计数脱节）；`count_consistent` `true` → `false`；thread_fp 与 hold_fp 不变 |
| 其余 11 样例 | `IDENTICAL`（内容均为 ASCII，归一化无效果） |
| baseline_decision | 若真实合并：`NEW`，且必须先解决 `count_consistent=false` |
| legal_signoff | 必需 |
| evidence | 对比 JSON 中 `classification=["HASH"]`，`attachments.count_before=2`/`count_after=1` |

**审阅结论**：改变原始哈希语义，不改变线程。`count_consistent=false` 是本变更的强制阻断信号：引擎的"唯一 digest 数"与外部 oracle 不再一致，交接时必须显式说明。

#### CHG-2026-SIM-03：hold 输出置空

| 字段 | 值 |
| --- | --- |
| status | `SIMULATED` |
| class | `HOLD` |
| scope | `engine.calculate`：`held_messages` 恒为 `[]` |
| rules_before | `05c64c0041cf1e4c…` |
| rules_after | `3b5a2f2d09f88172…` |
| case_impact | `s01`：`["m1"]` → `[]`，hold_fp `adae2a0359db…` → `4f53cda18c2b…`；`s02a`/`s02b`：`["dup"]` → `[]`，hold_fp `6a69dda46d73…` → `4f53cda18c2b…`；`s04b`：`["m2"]` → `[]`，hold_fp `3952b17fcb98…` → `4f53cda18c2b…` |
| 其余样例 | `IDENTICAL` |
| 线程/附件 | 12 个样例全部不变 |
| baseline_decision | 若真实合并：`UPDATE_IN_PLACE` 不允许；应视为冻结状态丢失，按 P1 处理 |
| legal_signoff | 必需 |
| evidence | 对比 JSON 中 4 个样例 `classification=["HOLD"]` |

**审阅结论**：只改变冻结状态，不改变线程与原始哈希；但因为丢失的是 hold 事实，仍必须法务签字。

#### CHG-2026-SIM-04：输出信封新增 `schema_version`

| 字段 | 值 |
| --- | --- |
| status | `SIMULATED` |
| class | `ENVELOPE` |
| scope | `report.build_result`：新增 `"schema_version": "1"` |
| rules_before | `05c64c0041cf1e4c…` |
| rules_after | `170d215648e02710…` |
| case_impact | 12 个样例全部 `ENVELOPE`；`output_sha256` 全变；thread/attachment/hold 指纹全部不变 |
| baseline_decision | `UPDATE_IN_PLACE` 可接受 |
| legal_signoff | 不需要 |
| evidence | 对比 JSON 中 `classification=["ENVELOPE"]` 且三类语义指纹均相同 |

**审阅结论**：不改变线程与原始哈希；可轻量登记。对应 REQ-008。

#### CHG-2026-SIM-05：SHA-256 改为 SHA-1

| 字段 | 值 |
| --- | --- |
| status | `SIMULATED` |
| class | `HASH`（**输出不可见**） |
| scope | `engine.calculate`：`hashlib.sha256` → `hashlib.sha1` |
| rules_before | `05c64c0041cf1e4c…` |
| rules_after | `4c569b9574bf933b…` |
| case_impact | **12 个样例全部 `IDENTICAL`**：现有输出只暴露"唯一 digest 数"，不暴露 digest 值，换算法后去重成员关系不变 |
| baseline_decision | 若仅看样例：会误判为"无变化"。**这正是必须依赖 `rules_sha256` 的原因** |
| legal_signoff | 必需（原始哈希语义已改变） |
| evidence | `compare` 无任何样本分类变化，但 `rules.changed=true`；退出码 1 |

**审阅结论**：这是本登记册最重要的反例——**只看 `check`/样例输出无法发现哈希算法被替换**。因此每次变更必须同时比较 `rules_sha256`，并在登记册记录该值。若未来需要更强保证，应新增"样例中包含 digest 值本身"的用例（REQ-006 的验收项之一）。

---

## 3. 冻结事件登记（HLD）

记录 hold 的生效/解除对索引与冻结证据的影响。**当前实现下，hold 变更只影响 `held_messages` 与 `hold_fingerprint`。**

| ID | 事件 | 输入 | `held_messages` 变化 | thread_fp | attachment_fp | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| HLD-2026-001 | hold 建立前 | `s04a` | `[]` | `fecaca9dcca1…` | `fb99c5d9ed94…` | 基线快照 |
| HLD-2026-002 | hold 生效（对 `m2`） | `s04b` | `[]` → `["m2"]` | 不变 | 不变 | 只改冻结状态 |
| HLD-2026-003 | hold 解除 | `s04c` | `["m2"]` → `[]` | 不变 | 不变 | 输出与 HLD-2026-001 逐字节相同 |
| HLD-2026-004 | hold 指向未采集 ID | `s01`（`holds` 含 `missing-message-id`） | 该 hold 被静默丢弃，仅保留 `m1` | 不变 | 不变 | 无效 hold 必须在交接中显式说明 |
| HLD-2026-005 | hold 指向重复 id | `s02a`/`s02b`（`holds=["dup"]`） | 两个方向都得到 `["dup"]` | 两个方向不同 | 相同 | hold 无法区分两个来源；REQ-002 的动因之一 |
| HLD-2026-006 | hold 与 id 类型不一致 | `holds=[1]`，`id="1"`（或反之） | 不匹配，`[]` | 不变 | 不变 | 类型严格相等；跨系统导入时必须统一为字符串 |

**冻结边界（必须写进交接）**：

1. hold **不冻结附件**：s04b 中被 hold 的 `m2` 的附件仍参与去重计数（`attachment_count=1`），且没有"已冻结"标记。
2. hold **不阻止任何写操作**：本仓库无写入路径，hold 只是输出投影。
3. hold **不可区分同名消息**：重复 `id` 时两个来源共享同一 hold 记录。
4. 解除 hold **不会**恢复/删除任何证据；它只是让 `held_messages` 少一项。

---

## 4. 批次回滚登记（RB）

| ID | 对应 CHG | 回滚内容 | 基线处理 | 校验 | 状态 |
| --- | --- | --- | --- | --- | --- |
| RB-2026-000 | CHG-2026-000 | 建立冻结点（不可回滚，作为起点） | v0.1.0 永久保留 | — | `N/A` |

回滚规则（与手册 §9.2 一致）：

1. 一个批次 = 一次规则变更 + 其基线目录 + 其 CHG 条目，三者同进同退。
2. `git revert` 变更提交；基线目录若为新建则移入 `docs/samples/baseline/WITHDRAWN/`，**不要删除**，也**不要**改写旧基线。
3. 本表新增 `RB-*` 行，CHG 状态改 `ROLLED_BACK`，记录回滚时间、执行人、复核人。
4. 回滚后必须运行 `check` 且 `rules=unchanged`、`0 drift`。
5. **禁止**在回滚时"顺手"用当前输出覆盖冻结点。

---

## 5. 审查记录（REV）

| ID | 对象 | 判定问题 | 结论 | 依据 |
| --- | --- | --- | --- | --- |
| REV-2026-001 | CHG-2026-SIM-05 | 仅凭样例输出，SHA-256→SHA-1 不可见 | 阻断：必须比较 `rules_sha256` | §2.3 SIM-05 |
| REV-2026-002 | CHG-2026-SIM-02 | 归一化后 `count_consistent=false` | 阻断：引擎计数与 oracle 脱节 | §2.3 SIM-02 |
| REV-2026-003 | CHG-2026-SIM-01 | 末位引用改首位会重组 s05 线程 | 需重冻 + 法务签字 | §2.3 SIM-01 |
| REV-2026-004 | HLD-2026-004 | hold 指向未采集 ID 被静默丢弃 | 交接必须显式声明该 hold 无效 | §3 |
| REV-2026-005 | 现状 | `status` 恒为 `"ok"`，异常不失败 | 记录为 REQ-001/REQ-008 动因 | 手册 §7.2 |

---

## 6. 升级与签字栏

| 级别 | 触发 | 记录位置 | 时限 |
| --- | --- | --- | --- |
| P1 | 干净树 `check` drift；非预期 `HASH`；`rules_sha256` 变化但无 CHG | 本册新增 REV + CHG | 立即 |
| P2 | 非预期 `THREAD`；`count_consistent=false` | 本册新增 CHG | 1 个工作日 |
| P3 | 幻影根/顺序敏感/命名冲突无说明 | HLD 或 REV 补注 | 3 个工作日 |
| P4 | 仅 `ENVELOPE` | CHG 即可 | 下个迭代 |

签字栏（每次 `THREAD`/`HASH`/`HOLD` 变更必填）：

```
CHG-ID:            __________
变更分类:          __________
rules_sha256 前:   __________
rules_sha256 后:   __________
线程是否改变:      __________
原始哈希是否改变:  __________
法务签字/日期:     __________
数据工程签字/日期: __________
回滚批次号:        __________（未回滚填 N/A）
```

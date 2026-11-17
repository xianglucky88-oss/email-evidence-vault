# 证据索引维护手册

**状态**：现行（v0.1.0，2026-11-17）
**适用对象**：法务、数据工程、下一轮维护者
**范围**：仅维护现有证据索引的**规则、样例、冻结点与交接记录**。本文档不扩展邮件解析功能；任何"新增解析/采集能力"都必须单独立项，不在本手册的变更范围内。

---

## 0. 一页结论

| 问题 | 结论 |
| --- | --- |
| 当前索引由什么决定？ | 仅 `messages[].id`、`messages[].refs[-1]`、`attachments[].content` 的 SHA-256、以及 `holds[]` 与 `id` 的交集。其余字段一律不参与。 |
| 原始哈希是什么？ | `sha256(content.encode("utf-8")).hexdigest()`，**不**做 Unicode 归一化、**不**读文件名、**不**解码 MIME。 |
| 哪些改动会改变既有线程？ | 改变"父引用取哪一个"的规则（如 `refs[-1]` → `refs[0]`）、改变根节点判定、改变引用缺失时的处理方式。 |
| 哪些改动会改变原始哈希？ | 任何改变 `content` 字节序列来源或编码方式的做法（归一化、换行转换、MIME 解码、截断、加盐、换哈希算法）。 |
| 哪些改动只影响冻结状态？ | 只改 `holds[]` 的匹配/生效/解除语义，或只改输出信封（`project`/`status` 等）。 |
| 怎么判断某次修改属于哪一类？ | 运行 `tools/index_audit.py compare`，看每个样例被标记为 `THREAD` / `HASH` / `HOLD` / `ENVELOPE` / `IDENTICAL`。 |
| hold 会冻结什么？ | **当前实现只冻结"哪些消息被点名"这一事实**。它不冻结附件、不阻止任何写操作、也不改变线程或哈希。详见 §5。 |
| 需求与实现的差距在哪？ | 见 §8 与 `index-change-register.md` 中 `REQ-*` 条目。 |

---

## 1. 文档与代码的三层标注

本手册对每条主张标注来源，审阅者不应把"需求约定"误当成"已实现"：

| 标注 | 含义 | 可信度 |
| --- | --- | --- |
| **【实现】** | 在当前提交上可运行、可用样例复现的行为 | 以 `docs/samples/baseline/v0.1.0/` 与 `tests/` 为准 |
| **【需求】** | 法务/数据工程约定应然行为，但当前代码未实现 | 只能作为验收目标，不能作为现状依据 |
| **【待开发】** | 已识别、尚未排期的能力缺口 | 记录在变更登记册，不阻塞当前交接 |

**现状优先原则**：当【实现】与【需求】冲突时，索引的实际输出以【实现】为准；本手册的"影响分析"一律先描述实现行为，再说明需求差距。

---

## 2. 当前实现的索引规则（【实现】）

唯一计算入口是 `src/email_evidence_vault/engine.py:calculate`。全部规则如下，没有隐含规则：

1. **父引用**：对每条消息取 `refs` 的**最后一个元素**作为父节点；`refs` 缺失或为空列表时，该消息自成一个根（`parent[id] = id`）。
   - 若 `refs` 是字符串（而非列表），当前实现会按下标取**最后一个字符**作为父节点。这是缺陷，不是约定；见 §7.2。
   - 列表中排在最后的引用若指向未采集的 ID，**前面的有效引用不会被回退使用**。见 s05。
2. **线程根**：沿父指针上溯直到自环或重复访问。**不按 `subject` 合并**，不按 `from` 合并，不按日期合并。
3. **线程成员**：同一根下的所有 `id`，排序后输出。重复 `id` 会重复出现（见 s02a/s02b）。
4. **附件去重**：对 `attachments[].content` 的 UTF-8 字节做 SHA-256；**同一 digest 只计一次**，`attachment_count` 是唯一 digest 的数量。
   - 文件名不参与寻址；同一 digest 的不同文件名视为同一附件（s02a、s03）。
   - 除 `name` 外不读取任何附件元数据（无 MIME 解码、无扩展名推断）；`content` 必须是字符串，否则抛异常。
   - 当前输出只暴露计数，不暴露 digest 或文件名；`inspect`/`manifest` 才提供 digest 与命名冲突明细。
5. **legal hold**：`held_messages = sorted(set(holds) & {id})`。
   - hold 只在输出中报告交集，**不影响**线程、附件计数或任何持久化状态。
   - 指向未采集 ID 的 hold 被静默丢弃（s01、s04a）。
6. **输出信封**（`report.py`）：`{"project", "status": "ok", ...data}`。当前没有版本字段；`status` 恒为 `"ok"`，即使存在数据异常。
7. **确定性**：相同输入产生相同输出。但**输入行顺序可能改变输出**（s02a vs s02b），这是重复 `id` 导致的；`order_sensitive` 异常项会标出。

### 2.1 参与与不参与索引的字段

| 字段 | 是否参与 | 作用点 | 备注 |
| --- | --- | --- | --- |
| `messages[].id` | 是 | 线程键、hold 交集 | 唯一性未校验；重复 id 会同时进入线程和 hold |
| `messages[].refs` | 是（仅末位） | 父指针 | 列表取最后一个；字符串取最后一个字符（缺陷） |
| `messages[].attachments[].content` | 是 | SHA-256 去重 | 必须为字符串 |
| `messages[].attachments[].name` | 仅记录，不参与寻址 | — | digest 冲突时当前不会保留第二个名字 |
| `messages[].subject` | 否 | — | 【需求】可能用于缺失 Message-ID 的兜底；【实现】不读 |
| `messages[].from` | 否 | — | 不用于跨邮箱去重 |
| `messages[].body` | 否 | — | 不解析嵌套转发 |
| `messages[].sent_at` / 日期 | 否 | — | 当前样例里的时区字段被完全忽略 |
| `holds[]` | 是 | hold 交集 | 与 `id` 精确类型比较；`"m1"` 与 1 不相等 |
| 其他任意字段 | 否 | — | `inspect` 会列出 `ignored_message_fields` |

---

## 3. 术语与数据契约

- **证据记录（message row）**：`messages` 数组中的对象，至少含 `id`。当前不做 schema 校验。
- **父引用**：决定线程归属的单一 ID，即 `refs[-1]`（或自环）。
- **原始哈希 / 内容寻址**：`sha256(utf8(content))`。它是附件的唯一身份，**不是**文件哈希，也不是 MIME 实体哈希。
- **冻结（hold）**：本项目中仅为"被点名的消息 ID 出现在 `held_messages`"。【需求】的"禁止修改/删除"未实现。
- **冻结点（baseline）**：`docs/samples/baseline/v0.1.0/` 下的期望输出与 `manifest.json`。任何索引规则变更必须显式决定"是否重冻"。
- **异常项（anomaly）**：`inspect`/`manifest` 计算出的结构性风险信号，不阻断运行。

---

## 4. 八类问题的处理口径

每一类都给出：当前实现行为、对索引/冻结的影响、审阅者要检查什么。对应最小样例在 `docs/samples/cases/`，期望输出在 `docs/samples/baseline/v0.1.0/`。

### 4.1 Message-ID / 引用缺失

**【实现】** 消息的 `id` 为 `null` 或空字符串时仍会被索引（分别成为键 `null` 与 `""`）；**完全没有 `id` 键**会抛 `KeyError`。`refs` 指向未采集 ID 时，该 ID 成为**幻影根**（phantom root），回复被单独成线程。
**影响**：线程数量增加；冻结不受影响；不会报错。
**样例**：`s01-missing-message-id`（`m3` 的父 `missing-message-id` 不存在）。基线输出把 `m3` 放进 `missing-message-id` 线程，同时 `holds` 中同名的项被静默丢弃。
**审阅检查**：`threads` 是否出现不在 `id` 集合中的键；`holds_not_matched` 是否非空；`status` 是否仍为 `"ok"`。

### 4.2 跨邮箱重复

**【实现】** 没有"邮箱/账户"维度。相同 `id` 出现在不同邮箱时，`parent` 字典后写覆盖先写，**结果依赖输入顺序**。
**影响**：两个方向的输入可产生不同线程树（s02a vs s02b），但附件计数与 hold 输出相同；`order_sensitive: true`。
**样例**：`s02a-cross-mailbox-duplicate`、`s02b-cross-mailbox-duplicate-reversed`。
**审阅检查**：`duplicate_ids` 是否非空；`order_sensitive` 是否为 `true`；变更前后线程指纹是否仅在重复 id 所在的根上变化。
**【需求】** 应以 `(mailbox, message_id)` 作为去重键并保留来源，当前未实现（REQ-002）。

### 4.3 附件内容寻址

**【实现】** 以 `content` 的 UTF-8 SHA-256 为唯一键；同名不同内容计为两个附件；同内容不同名计为一个（保留最后写入的名字）。
**影响**：`attachment_count` 是唯一 digest 数，不是附件条目数。文件名冲突时，输出只反映最后一次写入的名字，当前输出甚至不暴露名字，只有计数。
**样例**：`s03-attachment-addressing`、`s08-unicode-normalization`。
**审阅检查**：`count_consistent`（引擎计数 vs 独立哈希 oracle）必须为 `true`；若变更后为 `false`，说明计数规则与哈希规则脱节，属于 `HASH` 类事故。

### 4.4 哈希冲突

**【实现】** 只用了 SHA-256 的十六进制全串，冲突在工程上不可观测；代码**没有**检测同 digest 不同 `content` 的能力（因为 digest 就是 content 的函数）。文件内两条相同 `content` 合并为一个。
**影响**：真实 SHA-256 碰撞不会发生；但"内容相同、名字不同"经常被误报为碰撞。需要区分：
- **同名不同内容** → 两个 digest，`attachment_name_digest_reuse` 非空，不是哈希冲突；
- **同 digest 不同名字** → 一个 digest，`attachment_digest_name_conflicts` 非空，是命名歧义，不是哈希冲突。
**审阅检查**：不得把上述两种异常写成"哈希冲突"；若未来引入非 SHA-256 或截断摘要，必须登记 REQ-006 并重跑 `compare`。

### 4.5 转发嵌套

**【实现】** 不解析嵌套邮件。转发只是 `refs` 链上的一条普通消息；`body` 中的内嵌邮件头不会生成新记录，也不影响线程。
**影响**：如果转发没有携带 `refs`，它会成为新根，导致线程断裂；`subject` 的 `Fwd:` 前缀被忽略。
**样例**：`s05-ref-chain-forward`（`leaf` 的 `refs` 为 `["root","mid"]`，取末位 `mid`；`other` 的 `refs` 末位是幻影 `ghost`，于是脱离已有链）。
**审阅检查**：把 `refs[-1]` 改为 `refs[0]` 会把 `other` 并入 `root` 线程——这正是 `THREAD` 类变更，必须走 §9 流程。
**【需求】** 需要记录内嵌消息层级与来源，当前不实现（REQ-004）。

### 4.6 时区缺失

**【实现】** 完全不读日期字段。`s07a`（无日期）与 `s07b`（一条带 `+08:00`、一条无时区）输出**逐字节相同**，线程指纹一致（`f12ccde8f8…`）。
**影响**：时区缺失目前对索引与冻结**零影响**；不要声称索引对时间做了归一化。
**审阅检查**：任何引入日期排序或时间窗的改动都会新增对缺失时区的依赖，属于【待开发】REQ-005，必须先定义缺失时区策略（拒绝、按 UTC 假设、或标记不确定），再变更索引规则。
**样例**：`s07a-no-date-fields`、`s07b-timezone-missing`。

### 4.7 孤立回复

**【实现】** 孤立回复（父 ID 未采集）不会报错，而是以幻影根单独成线程；若同时被 hold 命中，`held_messages` 会包含它（前提是 hold 写的是消息自身 `id`，而非父 ID）。父 ID 的 hold 会被丢弃。
**影响**：线程数偏高；冻结状态可能"看起来完整"但其实指向一个不存在的父。
**样例**：`s01`（`holds` 含 `missing-message-id`，结果 `held_messages` 只有 `m1`）、`s04a/b/c`（`m3` 是孤立回复，但不在 hold 内）。
**审阅检查**：`phantom_roots` 与 `holds_not_matched` 同时非空时，必须在交接记录中写明"该 hold 无效"。

### 4.8 hold 生效 / 解除

**【实现】** hold 是一个纯输入集合，输出只是它与已采集 `id` 的交集。生效与解除**只改变 `held_messages`**，不改变线程指纹、不改变附件哈希、不改变任何冻结的物理证据。
**影响**：
- 生效（`s04a` → `s04b`）：`hold_fingerprint` 变化，`thread_fingerprint`、`attachment_fingerprint` 不变。
- 解除（`s04b` → `s04c`）：完全可逆，`s04c` 与 `s04a` 的输出逐字节相同（`8cc44f4bf9…`）。
- hold 与附件无关联：即使消息被 hold，它的附件仍然按内容去重计数，不进入任何"冻结清单"。
**样例**：`s04a-hold-before`、`s04b-hold-active`、`s04c-hold-after-release`。
**【需求】** hold 应冻结对应附件并阻止删除（REQ-003），当前只有"被点名"这一事实。
**审阅检查**：任何 hold 变更只应产生 `HOLD` 分类；若 `compare` 同时报出 `THREAD` 或 `HASH`，说明变更越界。

---

## 5. 冻结证据的影响矩阵

| 变更 | 线程指纹 | 附件哈希/计数 | hold 指纹 | 输出信封 | 分类 |
| --- | --- | --- | --- | --- | --- |
| 改 `refs[-1]` → `refs[0]` | 变（s05） | 不变 | 不变 | 变 | `THREAD` |
| 改根节点判定（如按 subject 合并） | 变 | 不变 | 不变 | 变 | `THREAD` |
| 改 `content` 编码/归一化 | 不变 | 变（s08） | 不变 | 变 | `HASH` |
| 改哈希算法/截断/加盐 | **样例可能不变** | **样例可能不变** | 不变 | 可能不变 | `HASH`（须全量重冻） |
| 改 hold 生效/解除语义 | 不变 | 不变 | 变（s01/s02/s04b） | 变 | `HOLD` |
| 只加输出字段（如 `schema_version`） | 不变 | 不变 | 不变 | 变 | `ENVELOPE` |
| 只改 `status` 文案 | 不变 | 不变 | 不变 | 变 | `ENVELOPE` |

原则：**线程与哈希是两把独立的锁**，**规则指纹是第三把锁**。任何一次修改，只要 `compare` 报出 `THREAD` 或 `HASH`，或 `rules_sha256` 变化，就必须按 §9 的"重冻 + 登记 + 审查"执行；只报 `HOLD` 时也需法务签字（冻结状态变化）；纯 `ENVELOPE` 可走轻量流程。

---

## 6. 字段血缘

```
输入 JSON
  messages[]                     # parser.load_records：仅保留 dict 行
    ├─ id            ──────────┬─> threads 的键与成员
    │                          ├─> held_messages（与 holds 求交）
    │                          └─> parent 图的节点
    ├─ refs          ──[-1]───> parent[id] ──> root() ──> threads 的根
    │   （字符串时为末字符，缺陷）
    ├─ attachments[]
    │    ├─ name      ──（不参与寻址；同名冲突时最后写入者胜）
    │    └─ content   ──utf8──> sha256 ──> attachments 去重键 ──> attachment_count
    └─ 其他字段（subject/from/body/sent_at/...）──> 不参与，仅出现在 ignored_message_fields
  holds[]          ──────────> set ∩ ids ──> held_messages
report.build_result ──> {project, status:"ok", ...}
```

血缘要点：

- `id` 是**唯一**同时影响线程与 hold 的字段；其唯一性未校验。
- `refs` 只以**末位元素**进入血缘；中间引用在实现中不产生任何效果（但它们会出现在 `missing_ref_targets` 检查里）。
- `content` 是**唯一**产生哈希的字段；`name` 不进入哈希。
- `holds` 与附件无血缘关系。

---

## 7. 审查清单

### 7.1 每次索引规则修改前（必做）

- [ ] 变更意图写成一句话，并标注它属于 `THREAD` / `HASH` / `HOLD` / `ENVELOPE` 中的哪一类（可多选）。
- [ ] 在干净工作树上运行 `python3 tools/index_audit.py check`，确认 `0 drift` 且 `manifest=verified`。
- [ ] 用旧代码跑出 before 结果目录：`python3 tools/index_audit.py run --out /tmp/before`（从冻结点提交检出）。
- [ ] 在新代码上跑出 after 结果目录：`python3 tools/index_audit.py run --out /tmp/after`。
- [ ] 运行 `python3 tools/index_audit.py compare --before /tmp/before --after /tmp/after`，确认分类与意图一致。
- [ ] 若出现非预期的 `THREAD` 或 `HASH`，**停止**，走 §10 异常升级。
- [ ] 在 `index-change-register.md` 新建 `CHG-*` 条目，填写前后指纹。

### 7.2 当前实现中的已知脆弱点（交接必读）

这些是【实现】缺陷，不是需求。修改它们等同于索引规则变更，必须走 §9：

1. `refs` 为字符串时按字符取父（`refs[-1]` 取末字符）。已在 §2 标注。
2. 重复 `id` 时 `parent` 后写覆盖先写，结果依赖输入顺序（s02a/s02b）。
3. 线程成员使用 Python 默认排序；`id` 混合 `int` 与 `str` 会抛 `TypeError`。
4. 附件 `content` 非字符串时抛 `AttributeError`；缺少 `name`/`content` 时抛 `KeyError`；`holds` 为 `None` 时抛 `TypeError`，`holds` 为字符串时被当作字符集合（`holds="m1"` 会分别匹配 `id="m"` 或 `id="1"`，并**不会**匹配 `id="m1"`），`holds` 含 dict/list 时抛 `TypeError`。
5. hold 与 `id` 是**严格类型相等**：`holds=[1]` 不会命中 `id="1"`，反之亦然；`None` 可以互相匹配。
6. `status` 恒为 `"ok"`，异常只体现在字段里，不会失败退出。
7. CLI 对不存在文件抛异常并以非零码退出；没有结构化错误信封。
8. `parser.load_records` 静默丢弃非 dict 行，不计数、不告警。

> **`rules_sha256` 是必查项。** 样例输出只暴露线程成员与附件计数，不暴露 digest 值。把 `sha256` 换成 `sha1` 时，所有样例输出仍逐字节相同，`check` 的 drift 为 0——只有 `rules_sha256` 会变化。因此 `check` 在规则指纹变化时也会返回非零；只改输出字段（`ENVELOPE`）同样会触发该提示，需在登记册说明。

### 7.3 审阅者判断"是否改变既有线程或原始哈希"的最短路径

1. 看 `check` 末尾的 `rules=unchanged|CHANGED`；`CHANGED` 说明规则文件被动过，继续第 2 步。
2. 看 `compare` 输出里是否有 `THREAD` 或 `HASH` 字样。
3. 若只有 `HOLD`/`ENVELOPE`/`IDENTICAL` 且 `rules.changed=false` → 既有线程与原始哈希未变。
4. 若 `rules.changed=true` 但所有样例均为 `IDENTICAL` → **不能**判定未变，必须逐行审查 diff（`compare` 会因此返回非零）。
5. 若有 `THREAD`/`HASH` → 打开 classify 报告的 JSON，检查 `threads.changed_roots`、`attachments.digests_added/removed`、`count_consistent_*`、`fingerprints.before/after`。
6. 与 `manifest.json` 中对应样例的 `thread_fingerprint`、`attachment_fingerprint` 对照；完全一致才可写"线程/哈希未变"。

---

## 8. 当前实现 vs 需求约定 vs 待开发

| 能力 | 现状 | 需求约定 | 结论 |
| --- | --- | --- | --- |
| 线程重建 | 基于 `refs[-1]` 单父指针 | 应以标准 `References`/`In-Reply-To` 语义回退 | 【实现】有，【需求】未满足（REQ-001） |
| Message-ID 缺失 | 幻影根，不告警 | 应标记"引用不完整"并可追溯 | 【待开发】REQ-001 |
| 跨邮箱去重 | 无邮箱维度，顺序敏感 | 以 `(mailbox, message_id)` 去重并保留来源 | 【待开发】REQ-002 |
| 附件内容寻址 | SHA-256(utf8(content)) | 同左，且需记录 digest→名字多值 | 【实现】有，命名多值【待开发】REQ-007 |
| 哈希冲突处理 | 无显式检测 | 需区分内容碰撞与命名歧义并告警 | 【待开发】REQ-006 |
| 转发嵌套 | 不解析 | 需建模内嵌消息层级 | 【待开发】REQ-004（明确不在本次范围） |
| 时区缺失 | 完全忽略 | 需定义缺失时区策略 | 【待开发】REQ-005 |
| 孤立回复 | 幻影根，不告警 | 应输出"孤立"标记并进入审查清单 | 【待开发】REQ-001 |
| hold 生效/解除 | 仅输出交集 | 应冻结附件并阻止删除 | 【待开发】REQ-003 |
| 输出信封版本 | 无 `schema_version` | 建议加版本字段 | 【待开发】REQ-008 |

**边界声明**：上表右列的【待开发】项都是**新能力**，不是本维护任务的交付物。本任务只保证：这些缺口被明确记录、可被审查、且不会在交接中被误当成已实现。

---

## 9. 变更流程（含批次回滚）

### 9.1 标准变更

1. **分类**：按 §5 判定 `THREAD`/`HASH`/`HOLD`/`ENVELOPE`。
2. **冻结前快照**：`index_audit.py run --out /tmp/before`（旧代码）。
3. **实现变更**：只改 `engine.py` / `parser.py` / `report.py` 中与意图相符的最小面。
4. **对照**：`index_audit.py compare`；分类必须与步骤 1 一致。
5. **决定重冻**：
   - `THREAD` 或 `HASH`：**必须**新建基线目录 `docs/samples/baseline/v0.1.1/`，复制新期望输出与 `manifest.json`，旧目录保持只读。
   - `HOLD` / `ENVELOPE`：可原地更新 v0.1.0，但需在登记册说明"未改变线程/哈希"。
   - **只改规则但所有样例 `IDENTICAL`（如换哈希算法）**：仍然视为 `HASH` 类语义变更，必须新建基线并在登记册写明"样例不可见，依据 `rules_sha256` 判定"；不得因 `compare` 全绿而跳过。
6. **登记**：填写 `index-change-register.md` 的 `CHG-*`，附 before/after 指纹。
7. **测试**：`PYTHONPATH=src python3 -m unittest discover -s tests -v` 必须全绿；`check` 必须 `0 drift`。

### 9.2 批次回滚

- **回滚单位是"批次"**：一次变更 + 它的基线目录 + 它的登记条目，三者同进同退。
- 回滚步骤：
  1. `git revert` 该次变更提交（不改写历史）。
  2. 若基线目录是新建的（v0.1.1），删除该目录或将其标记 `WITHDRAWN`，**不要**修改 v0.1.0。
  3. 在登记册把对应 `CHG-*` 状态改为 `ROLLED_BACK`，记录回滚时间与复核人。
  4. 运行 `index_audit.py check`，确认回到 `0 drift` 且指向原基线。
- **禁止**：回滚时"顺手"把基线改成回滚后的输出——那会抹掉事故证据。

### 9.3 重冻判定表

| 分类 | 是否新建基线 | 旧基线 | 需法务签字 |
| --- | --- | --- | --- |
| `THREAD` | 是 | 只读保留 | 是 |
| `HASH` | 是 | 只读保留 | 是 |
| `HOLD` | 可选 | 可原地更新 | 是（涉及冻结状态） |
| `ENVELOPE` | 否 | 可原地更新 | 否 |
| `IDENTICAL` | 否 | 不变 | 否 |
| `IDENTICAL` 但 `rules_sha256` 变化 | 是（按语义归类） | 只读保留 | 是 |

---

## 10. 异常升级路径

| 级别 | 触发条件 | 第一响应 | 时限 | 升级对象 |
| --- | --- | --- | --- | --- |
| P1 | `check` 在干净树上出现 drift；或 `compare` 报出非预期 `HASH`；或 `rules_sha256` 变化但登记册无对应 `CHG-*` | 停止合并，保留 `/tmp/before` 与 `/tmp/after` | 立即 | 数据工程负责人 + 法务 |
| P2 | `compare` 报出非预期 `THREAD`；或 `count_consistent=false` | 冻结该批次，提交登记册草稿 | 1 个工作日 | 数据工程负责人 |
| P3 | `phantom_roots`/`order_sensitive`/命名冲突在交接样例中出现且无说明 | 补写说明到登记册 | 3 个工作日 | 下一轮维护者 |
| P4 | 仅 `ENVELOPE` 变化，且无数据语义变化 | 登记即可 | 下个迭代 | 维护者自决 |

升级时必须附带：变更提交号、`compare --json` 完整输出、涉及样例的 `input_sha256`、以及"线程/哈希是否改变"的一句话结论。

---

## 11. 交接清单

- [ ] 能复述 §2 的 6 条实现规则，且知道 `subject`/`from`/日期都不参与。
- [ ] 能独立运行 `check` / `run` / `compare` / `inspect` 四个子命令。
- [ ] 能解释 `THREAD` 与 `HASH` 为什么必须分开判定。
- [ ] 知道 hold 当前只影响 `held_messages`，不影响附件。
- [ ] 知道 v0.1.0 基线的 12 个样例各自证明什么（§4 与样例目录一一对应）。
- [ ] 已阅读 `index-change-register.md` 中的全部 `REQ-*` 与 `CHG-*`。
- [ ] 明确"本次任务不扩展邮件解析"，新增能力需另立 REQ 与变更批次。

---

## 12. 复现命令

```bash
# 全部测试
PYTHONPATH=src python3 -m unittest discover -s tests -v

# 基线一致性（必须 0 drift）
python3 tools/index_audit.py check

# 当前异常画像
python3 tools/index_audit.py inspect

# 变更前后对照（两侧都必须带 run-meta.json）
python3 tools/index_audit.py run --out /tmp/before   # 旧代码
python3 tools/index_audit.py run --out /tmp/after    # 新代码
python3 tools/index_audit.py compare --before /tmp/before --after /tmp/after
```

`run` 会写出每个样例的结果和 `run-meta.json`（记录输入指纹与 `rules_sha256`）。`compare` 要求两侧样例集合一致、输入指纹一致；规则指纹不同即返回非零，即使所有样例输出相同。

`run` 默认拒绝覆盖已存在的输出（包括 `run-meta.json`）；需要重跑时加 `--force`，但不得对冻结点目录加 `--force`。

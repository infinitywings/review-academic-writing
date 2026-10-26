# 材料与记录清单（materials-checklist）

版本：2026-10-21 草案 v1
上位文件：`protocol.md` 第 8 节（冻结清单）、调研报告第 16–19 页（数据资源、复现风险、建议冻结的实验清单）

每一项前有 `[ ]`，阶段 0 结束时全部勾完才能开始任何模型运行。

---

## 1. 留出稿件

- [ ] 稿件来源只能是以下两类之一：未发表且作者书面授权的稿件；或由专家按真实稿件结构新写的原创等价稿件（概念依赖关系真实，内容不复制已公开文本）。
- [ ] 不用试点教学材料。2026-10-20 之前用过的 Gopen & Swan、NuWrite 样本每篇都被至少一个读者"认出"，不能再作验证材料（`package-check-2026-10-20.md` 第 5 节第 6 条）。
- [ ] 每篇记录公开程度，作为污染风险的独立限制写进报告：

| 字段 | 取值 |
| --- | --- |
| `exposure` | `unpublished`（从未公开）/ `preprint`（预印本，写日期）/ `published`（已发表，写日期）/ `original_equivalent`（原创等价稿） |
| `exposure_date` | 首次公开日期，未公开填 null |
| `model_cutoff_relation` | 公开日期相对所用模型训练截止的先后；不确定填 `unknown` |
| `recognition_probe_result` | 第 3 节识别探针的汇总 |

- [ ] 授权记录：作者姓名（匿名 ID）、授权范围（阅读实验、修改、引用片段）、日期、是否允许发布片段。
- [ ] 许可：稿件文本、图表、数据各自的许可分别记录；不从一处外推全部。
- [ ] 抽取核对：按技能 SKILL.md 第 1 步转为 UTF-8 Markdown，检查分栏顺序、公式、下标上标、表格。试跑里 `pdftotext` 把 带连字符的名称（如 "X-Y"） 吞成 "PGDSL"、行内下标全丢；用 pdfplumber 按栏裁剪后仍丢所有行内下标。记录每篇的抽取限制，评估点附近有不可靠区域的稿件退出（`protocol.md` 第 9 节）。
- [ ] 每篇生成 `index.json`，记录 `snapshot_id`；版本（自然 / 干预 / 泄漏 V1 V2 V+ V0）各自索引。
- [ ] 划分：开发集（可用于调指令）、留出集（只用于最终报告），以稿件为单位，不跨集合出现同一稿件的不同版本。

---

## 2. 封存专家意见

- [ ] 专家逐项意见的格式：每条一行，字段按 `scoring-template.json` 的 `source = expert_item` 行填写（位置锚点、障碍类型、适用读者层、适用任务、严重度、理由）。全局"好 / 坏"标签不用。
- [ ] 专家在模型运行前完成意见；意见文件放评估者专用目录，不在读者输入目录、不在文件名、不在图片边缘（`references/masked-reading.md` 第 2 节）。
- [ ] 意见文件的 SHA-256 在模型运行前写入冻结清单；揭示时核对。
- [ ] 专家不看任何模型输出，直到意见封存。
- [ ] 干预稿的"已知答案"（移动了哪段、加了哪句）与专家意见一起封存。
- [ ] 正、负控制与泄漏版本的定义 Def1 / Def2 由专家签字确认科学含义不同，签字文件封存。
- [ ] 专家参考答案（`human-study.md` 第 2 节四项任务）与读者背景题一起封存。
- [ ] 揭示后不再运行任何新模型调用；揭示后发现的指令缺陷记为"本轮未覆盖"。

---

## 3. 识别自报（source_recognition）

- [ ] 遮罩与分支响应：`masked_reader.py` 的 `record` 与 `branch-record` 要求响应含 `source_recognition: {recognized: bool, description: string}`，缺失即拒绝。原样保留，不改。
- [ ] 前缀响应：`review_packets.py check-observation` 只接受 `snapshot_id`、`checkpoint_id`、`prefix_sha256`、`packet_sha256`、`understanding`、`issues` 六个字段，多一个字段即拒绝。所以前缀条件（A、B、C、D-完整）的识别自报不能写进观察文件。做法：对每个前缀包另发一次无状态"识别探针"调用，输入是同一个包和一句固定提问（"你是否认得这段原文或与它相关的写作教学材料？只答 recognized 与 description，不要给出任何回忆的内容"），输出存为 `<label>.recognition.json`，与 `provenance.json` 并列。探针不改变主响应。
- [ ] 识别自报是自报。`recognized: false` 不证明模型没见过；`recognized: true` 的稿件（或术语）退出主分析，在附录单列。
- [ ] 负控制 V0 的结果与识别自报一起解释：V0 上"知道"了未给出的定义，等同于识别阳性。

---

## 4. 最低数据记录

沿用调研报告"建议冻结的实验清单"，并对应到技能现有文件。每个运行目录一份 `manifest.json` 汇总以下内容。

### 4.1 模型

| 字段 | 来源 |
| --- | --- |
| 模型 ID 与日期 | `provenance.json` 的 `model_usage` |
| 推理参数（temperature、最大输出） | `provenance.json` 的 `command`；未显式传参时记录运行时默认值的来源 |
| 实际系统提示 | `command` 里的 `--system-prompt` 两句，逐字 |
| 任务指令模板 | 四份 prompt 文件及其 SHA-256（A、B、C、D）；建表指令；提醒文本 |

### 4.2 代码与数据

| 字段 | 来源 |
| --- | --- |
| 技能版本 | `review-academic-writing/VERSION`、`SHA256SUMS`、`CHANGELOG.md` |
| 运行格式版本 | `masked_reader.py` 的 run-format（当前 4）；`review_steps.py` 的 journal schema（当前 2） |
| 控制脚本 | 验证运行自用的调用脚本（试跑的 `reader.py`、`extract.py` 原型）的文件哈希 |
| 稿件 | 每个版本的 `index.json`、`snapshot_id`、原始 PDF 哈希、抽取脚本哈希 |
| 许可副本 | 稿件、图表、外部数据各一份 |
| 获取日期 | 每份材料 |
| 划分清单 | 开发集 / 留出集稿件列表 |

### 4.3 输入访问

| 字段 | 来源 |
| --- | --- |
| 可见文本 ID | 前缀包的 `blocks` 列表与 `stop_offset`；遮罩包的当前窗口块 ID |
| 图表访问记录 | 遮罩运行的 lookup 事件（触发块、目标资产、返回事件）；前缀条件记 `strict-prefix`，无访问 |
| 检索范围 | `tool_policy`；`permission_denials` 为空作为"未尝试工具调用"的证据 |
| 是否读到未来信息 | 泄漏测试结果（`protocol.md` 第 4 节）；包哈希断言结果 |
| 图表政策标签 | `strict-prefix` 或 `reference-jump`，每次运行一个 |

### 4.4 输出与错误

| 字段 | 来源 |
| --- | --- |
| 原始响应 | `*.response.raw`（原字节）与 `*.response.json`（解析后），两者都留 |
| 解析失败 | `provenance.json` 的 `parse_error` |
| 重试 | 每次重试另存（如 E03 → E03b），原响应不删；重试原因（格式、预算、超时） |
| 截断、限流 | `exit_code`、`stderr` |
| 人工排除 | 排除的响应 ID 与理由（例如抽取下标丢失导致的问题，`reconcile` 标 `adequate`） |
| 程序校验 | `check-observation` 输出、遮罩回执、`06-ready.json`、`check-final` 输出 |

### 4.5 成本（分阶段）

| 阶段 | 记录 |
| --- | --- |
| 抽取与索引 | 人工时间 |
| 编辑侧 `reading` / `checks` / `reconcile` / `decisions` / `repairs` | 人工时间（试跑：约 15 / 20 / 25 / 25 分钟） |
| 读者调用（按条件 A、B、C、D-完整、D-压缩） | 请求数、`cache_creation_input_tokens`、`input_tokens`、`output_tokens`、`seconds`、`cost_usd`、失败重跑次数 |
| 建表调用、识别探针 | 同上，单列 |
| 评分与裁定 | 人工时间、裁定人数、分歧处理时间 |
| 保真审计 | 人工时间 |
| 真人阅读 | 每位读者用时 |

### 4.6 评价

| 字段 | 来源 |
| --- | --- |
| 评分表版本 | `scoring-template.json` 的 `_schema.version` |
| 评分指南版本 | `scoring-guide.md` 的版本行 |
| 标注者背景 | 裁定人与专家的匿名 ID、领域、是否参与稿件选择 |
| 分歧处理 | `scoring-guide.md` 第 3 节；一致率 |
| 盲法 | 谁在什么时候知道了什么（版本、条件、专家意见） |
| 预注册 | 主要结果、次要结果、分析单位、停止规则、转向规则的文件与哈希 |

---

## 5. 目录形状（建议）

```
validation-run-<date>/
  manifest.json              # 第 4 节全部字段 + 全部文件 SHA-256
  prereg.md                  # 预注册
  manuscripts/<id>/<version>/index.json, source.pdf, extraction-notes.md
  sealed/                    # 专家意见、参考答案、背景题、分配表；运行前只存哈希
  prompts/                   # A B C D 模板、建表指令、提醒文本、识别探针提问
  run/<manuscript>/<version>/journal/   # review_steps.py 日志
  run/<manuscript>/<version>/A|B|C|Dfull/<cp>.packet.json, .observation.json, .response.raw, .provenance.json, .recognition.json, .check.json
  run/<manuscript>/<version>/Bstate|Dstate/<cp>.state.json    # 建表输出
  run/<manuscript>/<version>/Dc/reader/   # masked_reader.py 运行目录（events、branches、receipts）
  scoring/scoring-<date>.json            # 填好的评分表
  human/                     # 匿名读者记录
```

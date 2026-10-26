# 效果验证方案（protocol）

版本：2026-10-21 草案 v1
对象：`skills/review-academic-writing/` 技能（阶段日志 `scripts/review_steps.py`、前缀包 `scripts/review_packets.py`、遮罩阅读 `scripts/masked_reader.py`）
设计来源：《AI 学术写作：读者认知与顺序阅读》文献方法调研报告（2026-10-21，第 20–26 页，以及 P4、P5 两节）
配套文件：`human-study.md`（真人实验）、`materials-checklist.md`（材料与记录）、`scoring-template.json` 与 `scoring-guide.md`（评分）

本方案只规定怎么验证，不预测结果。本文件没有运行任何模型。

---

## 0. 要验证什么，不验证什么

调研报告把要验证的因果链拆成三段：信息控制是否改变诊断；诊断是否对应真人障碍；编辑是否带来理解收益。本方案按这三段分别设计，不合并成一个总分。

三条边界贯穿全文：

1. 程序检查不是审稿质量。`review_steps.py ready`、`check-final`、`review_packets.py check-observation`、`masked_reader.py record` 的回执只证明文件、哈希、顺序、覆盖和引文位置正确。它们是进入分析的资格条件，不是结果指标。
2. 模型结果不是人类理解。四个条件下读者模型的问题、复述和状态表都是"模型在记录的输入条件下的行为"。只有 `human-study.md` 里的真人数据才能支持"理解"二字。
3. 强基线必须真的强。SimpleToM 附录 F 表明，针对知识状态的提醒加推理提示能把该基准的行为判断从 15.3% 提到 94.7%（P4）。因此全文条件必须拿到与前缀条件同样的状态表、同样的提醒、同样的推理指令。弱提示造出来的前缀优势不算数。

---

## 1. 技能里的实际对象

本方案提到的阶段和命令都取技能现有名字，不另起名。

| 技能对象 | 实际名字 | 在验证中的用途 |
| --- | --- | --- |
| 阶段日志五个阶段 | `reading` → `checks` → `reconcile` → `decisions` → `repairs`（`review_steps.py` 的 `STAGES`） | 编辑侧流程；验证运行沿用，`checks` 里的 `checkpoint_plan` 在派发读者前冻结 |
| 日志命令 | `review_steps.py start / record / ready / check-final` | `start` 绑定索引、读者档案、范围；`record --stage` 入链；`ready` 出就绪回执；`check-final` 核对终稿与记录对应 |
| 前缀包命令 | `review_packets.py index / packet / check-observation / check-review` | `index` 建块表；`packet` 构造前缀包（`--stop-after-text` / `--stop-offset` / `--through-block`）；`check-observation` 校验观察格式与引文 |
| 遮罩命令 | `masked_reader.py start / packet / record / branch / branch-record` | 逐窗展示、带 `gist` 与 `notes` 的有限笔记、单问分支 |
| 执行模式 | `fresh-prefix`、`bounded-request`、`masked-sequential`、`full-context` | 每个条件只能用其中一种，并原样写进记录 |
| 读者调用方式 | `claude -p --restricted --strict-mcp-config --tools "" --setting-sources "" --no-session-persistence --permission-prompts none`，自定义两句系统提示（见 `runs/2026-10-20/readers/*.provenance.json`） | 这是 `bounded-request` 的实现；上下文 862 token，而不是带全部 MCP 工具定义的 236k |
| 读者档案 | `assets/reader-profile.json`（前缀）、`assets/masked-reader-profile.json`（遮罩） | 四个条件用同一份背景描述；不含未见稿件内容 |
| 观察分类 | `absent_at_critical_use`、`resolved_later`、`needed_again`、`lost_in_notes`、`interpretation_changed`、`inferred_connection`、`asset_lookup`、`adequate` | `reconcile` 阶段的八类，评分表沿用 |
| 读者问题类别 | `unavailable_prerequisite`、`ambiguous_reference`、`missing_relation`、`retrieval_burden`、`scope_ambiguity`、`other` | 读者输出的类别；终稿类别另有 13 种，见 `references/output-contract.md` |

---

## 2. 四个条件

### 2.1 共同设定（四条件完全一致）

- 同一模型 ID 与日期、同样的推理参数、同样的最大输出。
- 同一份读者档案（`assets/reader-profile.json` 的结构，背景按稿件学科填写，不含项目专有内容）。
- 同一组评估点（checkpoint）。评估点由编辑在 `checks` 阶段的 `checkpoint_plan` 里选定并 `record`，选点可以参考全文，但必须写明 `selected_with_full_text: true`。
- 同一份任务指令。以 `runs/2026-10-20/readers/prompt-prefix.txt` 为底稿，加入一条推理指令："先列出当前判断依赖的前提，逐条写明该前提是否已在评估点之前的可见文本中给出；再给出问题。"这条指令四个条件都有，避免把"推理提示"的收益算到前缀头上。
- 同一个响应 schema（`snapshot_id`、`checkpoint_id`、`prefix_sha256`、`packet_sha256`、`understanding`、`issues`），由 `check-observation` 做格式校验。A、B 的包是全文包，校验照样能跑，但只是格式校验。
- 评估点标记句。四个条件的指令都含同一句："评估点：读到『<评估点末句原文>』时。"C、D 的包本来就在这句结束，这句是冗余的；保留它是为了四条件提示字数与形式一致。
- 每个评估点每个条件独立新上下文；不复用见过更晚评估点的上下文。

### 2.2 条件 A：强全文基线（全文，无状态表，同等任务提示）

- 可见范围：全文。包由 `review_packets.py packet --through-block <末块ID>` 生成。注意 `checkpoint_id` 是助手由 `snapshot_id`、`stop_offset`、`prefix_sha256` 算出来的，`check-observation` 会核对，控制器不能改写它。所以 A 的包带的是"全文包"自己的 `checkpoint_id`；它对应哪个评估点，由指令里的评估点标记句和控制器的 `manifest.json` 映射（文件名 `A/cp-01.*` 对应 C 的 `cp-01`）来记录。
- 显式状态：无。
- 执行模式：`bounded-request`。
- 它回答的问题：常规强审阅在"评估点时读者能否理解"这个任务上的能力。

A 不是"随便问一句全文审稿"。它用的是和 C 一模一样的任务、读者档案、推理指令和输出 schema，只是多看到了后文。

### 2.3 条件 B：全文加显式状态表与针对性提醒

- 可见范围：全文（同 A）。
- 显式状态：一张状态表加一段针对性提醒，格式与 D 完全相同。
- 执行模式：`bounded-request`，但每个评估点两次调用（一次建表，一次阅读）。

**状态表怎么建，才不会把读者在评估点本不该有的全文定义喂给它。**

状态表由一个独立的、无状态的"建表调用"产生，建表调用的输入只有条件 C 的前缀包（`review_packets.py packet --stop-after-text`，到评估点为止）和一段固定的建表指令。它看不到后文，所以表里不可能出现后文才给出的定义。建表输出固定为：

```
concepts: [{term, first_mention_anchor, status: stated | inferred | background | undetermined, current_explanation, evidence_anchor}]
open_questions: [{question, anchor, needed_now: true|false}]
```

`status` 四个值沿用 `masked_reader.py` 分支答案的 `support` 枚举（`stated / inferred / background / undetermined`），不另造。

同一张表同时交给 B 和 D；B 与 D 之间的唯一差别是可见范围。表由建表调用生成而不是由人工从全文整理，这一点要写进报告："状态表是同模型从前缀生成的，不是专家状态表。"如果预算允许，可以对一个子集另做专家只读前缀后手写的状态表，作为更强的 B/D 变体，但要单独标签。

**针对性提醒的固定文本（B 与 D 相同）：**

> 下表列出截至评估点可见文本中出现的概念及其状态。`undetermined` 表示评估点之前没有可用解释。评估读者在评估点时的理解，只能依据评估点之前的文本；评估点之后的文本不能用来判断评估点时的理解。对每个问题，先核对它依赖的前提是否在表中为 `stated` 或 `inferred`。

B 看得到后文，提醒要求它不要用；它是否真的不用，由第 4 节的泄漏测试来量，不靠它自报。

### 2.4 条件 C：完整已读前缀，无状态（技能现状）

- 可见范围：完整已读前缀，`review_packets.py packet --stop-after-text '<评估点末句>'`（重复引文用 `--occurrence N`，整块边界用 `--through-block`）。
- 显式状态：无。
- 执行模式：`bounded-request`（或 `fresh-prefix`，须在记录里写明实际用的是哪种）。
- 这就是 `review_packets.py` 今天产出的东西，也是 2026-10-20 试跑里 P1 到 P3 的条件。

### 2.5 条件 D：前缀加状态

D 必须先定义"完整前缀变体"，再定义"压缩记忆变体"。两者不能合并报告。

**D-完整前缀（主条件）**

- 可见范围：与 C 相同的前缀包。
- 显式状态：与 B 相同的状态表与提醒（同一张表，同一次建表调用的输出）。
- 执行模式：`bounded-request`，每个评估点两次调用（建表、阅读）。
- 它与 C 的差别只有状态表；与 B 的差别只有可见范围。2×2 由此成立。

**D-压缩记忆（变体，标签必须写"压缩记忆"）**

- 实现：`masked_reader.py start / packet / record`，逐窗新上下文，只带 `carry_memory`（`gist` 加 `notes`，每条笔记含 `id`、`kind`、`text`、`sources`、`since`），预算由 `memory_limit` 与 `memory_unit` 定义。
- 执行模式：`masked-sequential`。
- 它同时改变了两件事：可见范围（只有当前窗口加笔记，而非完整前缀）和记忆形式（结构化笔记而非原文）。所以它不是 2×2 的一个格子，是 C/D 之后的消融。调研报告的顺序是"先用完整已读前缀确认未来信息隔离有用，再检验压缩记忆"，本方案照此：D-压缩只在 C 对 A、D-完整对 B 的比较完成后运行，并单独成表。
- 用 `masked_reader.py branch` 做的单问分支算事后材料，不算自然阅读的一部分。
- 2026-10-20 试跑里 E01 到 E04c 与 BR01 属于这一变体。

### 2.6 一张表看四个条件

| 条件 | 可见范围 | 显式状态 | 执行模式 | 每评估点调用数 | 构包命令 |
| --- | --- | --- | --- | --- | --- |
| A | 全文 | 无 | `bounded-request` | 1 | `packet --through-block <末块>` |
| B | 全文 | 前缀建表 + 提醒 | `bounded-request` | 2 | 同 A 的包 + 建表调用（输入为 C 的包） |
| C | 完整前缀 | 无 | `bounded-request` / `fresh-prefix` | 1 | `packet --stop-after-text` |
| D-完整 | 完整前缀 | 同 B 的表 + 提醒 | `bounded-request` | 2 | 同 C 的包 + 同一建表输出 |
| D-压缩 | 当前窗口 + 有限笔记 | 结构化笔记 | `masked-sequential` | 窗口数 + 回看数 | `masked_reader.py start / packet / record` |

---

## 3. 资源匹配

匹配的目标是让四条件的差别只剩"可见范围"和"显式状态"两个因素。能做到相等的写"相等"，做不到的写"记录并报告"，不假装相等。

| 项目 | 规则 | 怎么核对 |
| --- | --- | --- |
| 模型 | 同一 `model_usage` 字符串，同一日期；研究中途模型换版即停（见第 9 节） | 每次调用的 `provenance.json` |
| 推理参数 | 同 temperature、同最大输出、同系统提示两句 | `provenance.json` 的 `command` 字段逐字比对 |
| 调用次数 | B、D-完整每评估点 2 次，A、C 1 次；不为"配平"而给 A、C 加空调用 | 按评估点计数表 |
| 输出预算 | 最大输出 token 相同；问题条数不设上限 | 分析时另报"每千可见 token 的问题数"作为次要指标 |
| 提示强度 | 任务指令、推理指令、评估点标记句四条件逐字相同；B、D 只多状态表与提醒块 | 冻结四份 prompt 模板，记录其哈希 |
| 输入长度 | A、B 输入必然长于 C、D；这是条件本身的差别，不做截断 | 报告每次调用 `cache_creation_input_tokens` / `input_tokens` |
| 成本 | 按条件、按阶段记录请求数、输入输出 token、秒、美元、失败重跑 | 沿用试跑的 `provenance.json` 字段 |
| 重试 | 格式或预算失败的重试保留原响应，重试另存并标注原因；重试次数进成本表，不进主指标 | `masked_reader.py` 的回执；前缀条件由控制器另存 |

试跑数据给了一个量级：前缀读者输出随前缀变长而变长（3.4k 到 7.1k token，问题数 4 到 9）。所以"问题多"本身不是指标，要以专家项和真人确认障碍为分母算 precision 和 recall。

---

## 4. 泄漏测试：前文相同，后文不同

目的：证明"前缀条件没有用到后文"不是靠声明，而是靠可观察的行为。对 C 和 D 它是控制器的管道测试；对 A 和 B 它量化全文条件实际用了多少后文。

### 4.1 材料构造

对一篇授权稿件选一个项目专有术语 T（由作者定义、不是领域通用词），做四个版本：

| 版本 | 评估点之前 | 评估点之后 | 用途 |
| --- | --- | --- | --- |
| V1 | 与 V2 逐字相同；T 只被使用，未被定义 | 给出定义 Def1 | 分叉版本 1 |
| V2 | 与 V1 逐字相同 | 给出定义 Def2（与 Def1 科学含义不同，由专家写并签字） | 分叉版本 2 |
| V+（正控制） | 定义 Def1 已在评估点之前给出 | 与 V1 相同 | 读者应能说出定义（`stated`） |
| V0（负控制） | 与 V1 相同 | 全文都不给定义 | 读者应报 `undetermined`，不能"知道" |

评估点放在 T 第一次关键使用之后、定义之前。四个版本各自 `index`，各自取 `snapshot_id`。

### 4.2 运行与断言

- 管道断言（运行模型前）：V1 与 V2 在该评估点的前缀包 `prefix_sha256` 必须相等，`visible_text` 与 `blocks` 逐字节相等。不相等说明构造或切分有问题，停下修正。V0 与 V1 的前缀包也必须相等。两个包不会完全同字节：`snapshot_id` 按全文算，`checkpoint_id` 又含 `snapshot_id`，所以这两个哈希字符串在 V1 与 V2 之间不同。它们不携带内容，但要在记录里写明"包只在这两个哈希字段上不同"。
- C 与 D-完整：对 V1、V2 各跑 n 次（n 在预注册里定，试点先定 5）。由于可见文本相同，差别只能来自采样。比较两组响应里对 T 的解释分布，预期无系统差异。有系统差异就是控制器之外的泄漏（例如文件名、路径、`checkpoint_id` 命名里带了版本信息），要排查。
- D-压缩：在分叉前的每个窗口，V1 与 V2 的 `carry_memory` 应逐事件一致（允许采样差异，不允许出现任一定义的内容）。
- A 与 B：对 V1、V2 各跑 n 次。把响应里对 T 的解释分为四类：符合 Def1、符合 Def2、`undetermined`、其他。V1 与 V2 之间的分布差异就是"全文条件用了后文"的量。B 的提醒若有效，B 的差异应小于 A；这个差异要报告，不要只报 B 的自述。
- V+：四条件都应能说出 Def1，并标 `stated`，引文落在定义句。做不到的条件说明任务指令本身有问题，先修指令。
- V0：四条件都应报 `undetermined`。某条件"知道"了定义，要么是预训练先验，要么是编造；该术语标为污染，换术语重做。

### 4.3 评分

每次响应由两名裁定人按 `scoring-guide.md` 第 6 节给 T 的解释分类，裁定人不知道版本和条件。

---

## 5. 内容排序干预

目的：在自然稿件之外，制造已知答案的受控缺陷，但不让受控成绩冒充自然成绩。

三种最小操作，每篇稿件每次只做一种：

| 操作 | 做法 | 已知答案 |
| --- | --- | --- |
| 解释提前 | 把一段已有解释原样移到其首次关键使用之前 | 原稿在该使用处的候选问题应消失 |
| 解释推后 | 把一段解释原样移到其首次关键使用之后 | 该使用处应出现 `unavailable_prerequisite` 类问题 |
| 增加桥接句 | 在两段之间加一句不引入新事实、不改变 claim 的过渡 | 相邻段落的 `missing_relation` 类问题应消失 |

专家检查：每个干预稿由一名领域专家对照 `scoring-template.json` 的 `fidelity` 字段签字，确认数值、条件、因果措辞、claim 强度、引用含义都没变。签字前不运行任何模型。变了的干预稿作废。

报告规则：自然原稿与干预稿分开成表，分开算 precision、recall；不得把两者合并后报一个总数。干预稿的"检出率"只支持"在受控任务上"的结论（见第 10 节转向规则二）。

---

## 6. 图表政策标签

两种政策，各自一个标签，同一次运行只能有一种：

| 标签 | 含义 | 在技能里的实现 | 记录 |
| --- | --- | --- | --- |
| `strict-prefix` | 只允许当前及此前已显示的图表；遇到对后文图表的引用不跳转 | `review_packets.py packet` 的前缀包：图表以 caption 块形式只在其在前缀内时可见 | 包里没有图表访问记录，因为没有访问 |
| `reference-jump` | 遇到明确引用可跳到指定图表，但每次跳转记录 | `masked_reader.py` 的 `assets`（`referenced_by` / `encountered_by`）加 lookup 事件 | 每次 lookup 的触发块、目标、返回事件、成本 |

- 前缀条件（A、B、C、D-完整）默认 `strict-prefix`。
- D-压缩默认 `reference-jump`，因为 `masked_reader.py` 的 lookup 机制就是它。若要在 D-压缩里做 `strict-prefix`，case 文件里不放 `assets`，并在记录里写明。
- 两种政策的结果分开报告。调研报告的原话：不能在同一"前缀阅读"标签下混合这两种政策。
- 图表中的图例、坐标轴、表头随图表整体可见；跨页表记录实际可见范围。读者对图表的疑问要区分"稿件缺信息"和"抽取漏了信息"（试跑里下标丢失造成的问题就属于后者，`reconcile` 时标 `adequate` 并排除）。

---

## 7. 每个条件用哪些命令

```
# 共同：建索引、读者档案、范围，开日志，记录 reading / checks（含 checkpoint_plan）
python3 <skill-dir>/scripts/review_packets.py index --input manuscript.md --out run/index.json
python3 <skill-dir>/scripts/review_steps.py start --index run/index.json --profile run/reader-profile.json --scope run/scope.json --out run/journal
python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage reading --notes run/reading.json
python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage checks --notes run/checks.json

# C：前缀包
python3 <skill-dir>/scripts/review_packets.py packet --index run/index.json --profile run/reader-profile.json --stop-after-text '<评估点末句>' --out run/C/cp-01.packet.json

# A：全文包（checkpoint_id 是全文包自己的；与 C 的 cp-01 的对应关系靠文件名和 manifest.json 记录）
python3 <skill-dir>/scripts/review_packets.py packet --index run/index.json --profile run/reader-profile.json --through-block <末块ID> --out run/A/cp-01.packet.json

# B、D-完整：建表调用的输入是 C 的包；建表输出 state-01.json 同时拼进 B 与 D 的指令块
#（建表与阅读都是 claude -p 的 bounded-request 调用，命令行见 provenance.json）

# 四条件观察格式校验（只校验格式、哈希、引文）
python3 <skill-dir>/scripts/review_packets.py check-observation --packet run/C/cp-01.packet.json --observation run/C/cp-01.observation.json

# D-压缩
python3 <skill-dir>/scripts/masked_reader.py start --case run/case.json --profile run/masked-profile.json --out run/Dc/reader
python3 <skill-dir>/scripts/masked_reader.py packet --run run/Dc/reader --out run/Dc/packet-001.json
python3 <skill-dir>/scripts/masked_reader.py record --run run/Dc/reader --packet run/Dc/packet-001.json --response run/Dc/response-001.json --agent-id <实际ID>
python3 <skill-dir>/scripts/masked_reader.py branch --run run/Dc/reader --from-sequence 7 --question run/q1.json --out run/Dc/branch-001.json
python3 <skill-dir>/scripts/masked_reader.py branch-record --run run/Dc/reader --packet run/Dc/branch-001.json --response run/Dc/branch-response-001.json --agent-id <实际ID>

# 编辑侧收尾（验证运行的编辑只挂接 C 与 D-压缩，A、B 的观察不作为前缀 checkpoint 挂进日志）
python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage reconcile --notes run/reconcile.json
python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage decisions --notes run/decisions.json
python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage repairs --notes run/repairs.json
python3 <skill-dir>/scripts/review_steps.py ready --journal run/journal --index run/index.json
python3 <skill-dir>/scripts/review_steps.py check-final --journal run/journal --index run/index.json --review run/review.json
```

说明：

- A、B 的包是全文包，`reconcile` 的 `checkpoints` 不接收它们作为 `fresh-prefix` / `bounded-request` 条目（那两种 mode 的语义是前缀）。A、B 的观察存在验证运行自己的目录里，由评分表引用，不进技能日志。
- 技能日志在验证里的作用是保证编辑的独立检查（`checks`）先于读者证据（`reconcile`）冻结。这是程序能保证的；编辑是否私下先看了观察文件，程序不能保证，报告照实写。
- 读者调用的 `--tools ""` 禁用了工具，`permission_denials` 为空说明读者没有尝试调用；这不是操作系统沙箱，也不能排除预训练记忆。

---

## 8. 揭示专家答案前必须冻结的东西

专家意见（专家逐项列出的理解障碍）在整个模型运行期间封存，封存方式见 `materials-checklist.md` 第 2 节。揭示之前，以下内容必须已经写入记录并有哈希：

1. 稿件集：每篇的 `snapshot_id`、版本（自然 / 干预 / 泄漏 V1 V2 V+ V0）、授权记录、公开程度。
2. 读者档案与四份 prompt 模板（A、B、C、D）及建表指令、提醒文本。
3. 评估点计划：`checks` 阶段已 `record`，含每个评估点的 `id`、`anchor`、`mode`、`reason`、`selected_with_full_text`。
4. 模型 ID、日期、参数、系统提示。
5. 四条件（含 D-压缩）全部原始响应、重试、`provenance.json`、`check-observation` 结果、遮罩回执。
6. 建表调用的全部输出（状态表）。
7. 评分表的版本（`scoring-template.json` 的 `_schema.version`）与评分指南版本。
8. 预注册文件：主要结果、次要结果、分析单位、停止规则、转向规则（本文件第 9 到第 11 节的最终版）。
9. 一份清单文件，列出以上所有文件的 SHA-256。

专家意见揭示后，模型侧不再运行任何新调用。揭示后发现的指令缺陷只能记为"本轮未覆盖"，在新一轮用新材料测试，不回头补跑（这与 `references/masked-reading.md` 第 7 节对教学材料的规则一致）。

---

## 9. 停止规则（操作层面）

| 触发 | 动作 |
| --- | --- |
| 泄漏测试管道断言失败（V1/V2 前缀包哈希不等，或 C/D 响应出现分叉后定义） | 停止全部模型运行；排查控制器、文件命名、路径；修复前的结果作废 |
| 正控制 V+ 失败（任一条件在 V+ 上说不出已给出的定义的比例高于预注册阈值） | 停止；先修任务指令，再从试点重来 |
| 负控制 V0 失败（任一条件"知道"未给出的定义） | 该术语标为污染，换术语；若多术语连续污染，怀疑稿件已被模型见过，稿件退出 |
| 抽取不可靠（阶段 0 核对发现公式、下标、分栏顺序错误影响评估点附近文本） | 该稿件在任何模型运行前退出；不事后用"抽取问题"解释结果 |
| 两名裁定人在试点上的一致率低于预注册阈值 | 停止主评分；修订 `scoring-guide.md`，用新的试点材料重测一致率 |
| 模型中途换版（`model_usage` 字符串变化） | 冻结已跑部分；新版本作为新一轮，不与旧版本混合 |
| 单阶段成本超过预算上限 | 减少每篇稿件的评估点数，不减少稿件数或读者数，以保持嵌套结构 |
| 读者档案、稿件版本或范围在运行中被改动 | 旧日志保留，`review_steps.py start` 开新运行；依赖记录全部重做 |

---

## 10. 转向规则（主张层面，来自调研报告第 26 页）

这些规则在预注册里写死，结果出来后按规则改主张，不改规则。

| 若出现 | 则主张改为 |
| --- | --- |
| 一：前缀（C、D-完整）在预注册的主要结果上没有优于强全文基线（A、B） | 贡献转向状态表示与诊断协议本身（B 对 A 的差），不再主张"遮盖有用" |
| 二：收益只出现在干预稿（第 5 节），自然稿件上没有 | 结论限定为受控任务；不主张对真实稿件的诊断收益 |
| 三：真人读者认为改稿更顺（流畅感、主观评分）但理解结果层指标没有改善 | 不主张认知收益；只能主张读者偏好 |
| 四：保真审计发现的事实损害（数值、条件、因果措辞、引用含义改变）抵消了理解收益 | 系统降级为人工审核的建议工具；不主张可自动改稿 |

补充两条调研报告"不预设赢家"里的情形：

- 若前缀条件只提高 recall 而显著损害 precision：定位为"疑问候选生成器"，输出必须经 `decisions` 阶段过滤后才算诊断。
- 若 A 与 C 同样有效：报告"状态与评价协议比遮盖更重要"，这本身是研究结果。

---

## 11. 分析计划要点

- 主要结果（预注册，示例，正式版在 `human-study.md` 第 7 节）：以真人确认障碍为真值，各条件在固定最低 precision 下的障碍检出率；以及理解正确率不下降时的阅读成本改善。
- 次要结果：按类别的 precision / recall；每千可见 token 的问题数；`resolved_later` 占比；泄漏测试的分布差异；成本。
- 分析单位：评估点嵌套于稿件，读者记录嵌套于读者；用稿件与读者为分组因素的层次模型，或先按稿件再按读者的分层 bootstrap。不把每条问题当独立样本。
- 留出集：至少一篇稿件和一组读者不参与任何指令调整，只用于最终报告。
- 自然稿与干预稿、`strict-prefix` 与 `reference-jump`、D-完整与 D-压缩，各自分表。

---

## 12. 本研究能支持的主张

1. 在记录的模型、参数、提示和输入边界下，前缀可见范围（C、D）相对于全文（A、B）是否改变了模型在评估点报出的问题集合，以及改变的方向和大小。
2. 显式状态表与提醒（B、D）相对于无状态（A、C）是否改变了同样的问题集合。
3. 两个因素是否有交互。
4. 各条件的问题与专家逐项意见、与真人确认障碍的 precision 和 recall，分自然稿与干预稿。
5. 全文条件在泄漏测试中实际使用后文的程度；前缀条件的控制器在构造上没有暴露后文（以包哈希与控制响应为证）。
6. 由诊断驱动的最小修改，在真人盲测中是否改善理解结果层指标，同时是否通过独立保真审计。
7. 各条件、各阶段的调用次数、token、时间与成本。
8. `review_steps.py`、`review_packets.py`、`masked_reader.py` 的程序检查在验证运行中是否全部通过（这只说明记录合规）。

## 13. 本研究不能支持的主张

1. 模型模拟了人类的阅读认知过程。模型在前缀条件下的问题是"在该输入边界下的模型行为"，不是人类读者在该位置的体验。
2. 任何记忆容量的数值。`memory_limit` 的 180 词或任何字符数是实验参数，不是人类工作记忆的估计；"半页到一页"是工程起点，不是认知常数。
3. 模型没有使用预训练中对该稿件或相关工作的记忆。`source_recognition` 为 false 是自报；负控制通过只说明该术语未被"知道"。
4. 问题更多等于诊断更好；问题更少等于稿件更好。
5. 干预稿上的检出率能推广到自然稿件。
6. 另一个模型给改稿打了更高分等于真人理解改善。
7. 流畅感或读者偏好等于理解收益。
8. 程序检查通过等于审稿质量合格；`ready` 与 `check-final` 的回执不是效果证据。
9. 在本轮所用模型以外的模型上成立。
10. 在未经真人实验验证的读者分层上成立。

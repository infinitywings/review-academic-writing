# 评分指南（scoring-guide）

版本：2026-10-21 草案 v1
配套：`scoring-template.json`（字段定义与三个示例行）
上位文件：`protocol.md`、`human-study.md`

评分的对象是两类行：专家意见行（`source = expert_item`）和模型问题行（`source = model_finding`）。每行一个判断，不给稿件打总分。

---

## 1. 评分前的条件

- 专家意见已封存并揭示（`materials-checklist.md` 第 2 节）；模型运行已全部结束，冻结清单哈希核对一致。
- 裁定人不知道每行的 `condition` 与 `version`。评分用的工作表把这两列隐藏，只在合并时恢复。
- 裁定人可以看稿件全文、读者响应原文、专家意见原文；不能看另一位裁定人的判断，直到各自完成。
- 每条专家意见和每条模型问题都要有一行；漏行就是漏数据，不能在分析时补。

---

## 2. 逐列怎么填

**anchor。** 原样抄响应里的 `anchor`；专家意见行抄专家写的锚点。锚点必须是该 `snapshot_id` 下的精确原文，否则该行 `match = excluded` 并在 `notes` 写明。

**match。** 四个主要值的定义：

| 值 | 用在哪类行 | 定义 |
| --- | --- | --- |
| `hit` | 两类行都用 | 模型问题与某条专家意见指向同一障碍：锚点所在块相同或相邻，并且问的是同一个依赖关系、同一个指代或同一处论证缺口。两行共用一个 `match_group` |
| `miss` | 只用于专家意见行 | 没有任何模型问题命中这条专家意见 |
| `false_positive` | 只用于模型问题行 | 模型问题没有命中任何专家意见，并且裁定人判定该位置对指定读者层不构成障碍（`fragment_confusing = false`） |
| `not_assessed` | 两类行都用 | 该条目不在该条件的评估点覆盖范围内（例如专家意见落在没有评估点的段落），不进分母 |

"同一障碍"的判定不看措辞。模型说"没定义状态可验证"，专家说"比较 A 与 B 时需要先区分可观测与可验证"，这是同一障碍。模型说"不知道 W1 和 W2 的区别"，而原因是抽取丢了下标，这不是障碍，`match = excluded`，`notes` 写"抽取问题"。

一条模型问题最多命中一条专家意见；一条专家意见可以被多个条件各命中一次，但在同一条件内，第二条命中同一专家意见的模型问题记为 `hit` 并在 `notes` 写"重复命中"，计算 precision 时重复命中只算一次。

**category_expert。** 专家障碍类型沿用调研报告第 21 页的诊断类别表：依赖不足、解释过晚、指代或对象不明、暂定理解被推翻、证据与主张缺口；加上"合理预告（非障碍）"用于专家明确标为不是问题的位置。模型问题行由裁定人按同一表填裁定后的类型。

**reconcile_label。** 如果验证运行的编辑在 `reconcile` 阶段已经给这条观察分了类，原样抄；没有就填 null。这一列只是记录，不参与 `match` 判定。

**applies_to_reader_stratum 与 applies_to_task。** 专家在写意见时就要填；模型问题行由裁定人填。一个问题对本领域专家可能不是障碍，对邻近方向评审是。`match` 的判定以专家意见行声明的读者层为准：模型问题命中了一条"只对邻近方向成立"的意见，就是 `hit`，不因为专家读者不会卡住而改判。

**severity。** 两列分别抄专家与模型的原值，不调和。差异在分析时报告。

**reader_confirmed。** 真人实验里是否有读者在同一位置标记卡住并经裁定为障碍。没做真人实验的行填 `not_tested`。主要结果用 `reader_confirmed = true` 的行作真值；专家意见作为次要真值。两者的差异本身要报告。

---

## 3. 两名裁定人怎么处理分歧

1. 各自独立填 `adjudicator_1` / `adjudicator_2` 的四个判断（`match`、`fragment_confusing`、`model_question_reasonable`、`notes`）。
2. 合并后比较。四个判断全部相同记 `agreed_initially = true`。
3. 不同的行，两人一起打开稿件原文和响应原文讨论，只允许用原文和本指南作理由；不允许用"我觉得模型通常会这样"之类的理由。讨论后一致记 `resolution = discussed_then_agreed`，`resolution_notes` 写分歧点和最终理由。
4. 讨论后仍不一致，交第三名裁定人独立判断，三人多数记 `resolution = third_adjudicator`。
5. 三人都不一致，记 `resolution = unresolved`，该行从主分析排除，在敏感性分析里分别按两种判断各算一次。
6. 报告初始一致率（按四个判断分别报告）和最终 `unresolved` 的比例。试点阶段一致率低于预注册阈值时，先修本指南再重测（`protocol.md` 第 9 节）。
7. 讨论不得改动任何原始响应或专家意见文件；只改评分表。

---

## 4. "这段原文确实让人困惑"与"模型的问题是合理的"是两件事

这是本指南最重要的区分。两列独立填，四种组合各有含义：

| `fragment_confusing` | `model_question_reasonable` | 含义 | 计数 |
| --- | --- | --- | --- |
| true | true | 原文有障碍，模型问到了点上 | `hit`（若有专家行）或 `hit` 加 `notes` "模型新发现"（若专家漏了，见第 5 节） |
| true | false | 原文有障碍，但模型问的不是这个（例如在正确位置问了一个无关的问题） | 对专家行是 `miss`；对模型行是 `false_positive`，`notes` 写"位置对、内容错" |
| false | true | 原文没有障碍，但就可见文本而言模型的问题是合理的（典型：诚实的贡献预告、直觉解释在先而正式定义在后、常识背景足以推出） | `false_positive`。这类误报是前缀条件的固有风险，分析时单独列出，用于判断前缀条件是否只适合做"疑问候选生成器"（`protocol.md` 第 10 节） |
| false | false | 原文没有障碍，模型的问题也站不住（编造了不存在的歧义、要求不该此处出现的细节） | `false_positive`，`notes` 写"不合理" |

`fragment_confusing` 判的是文本相对于指定读者层的属性，证据来自专家意见和真人卡住标记。`model_question_reasonable` 判的是问题相对于可见文本的属性：裁定人只看该条件下模型实际看到的文本（前缀条件就只看前缀；全文条件看全文）来判断这个问题是否有根据。全文条件下模型问了一个后文已经回答的问题，`model_question_reasonable` 仍可以是 true，因为问题针对的是评估点时的理解。

不要让"问题合理"抬高 precision。precision 的分子只有 `hit`。

---

## 5. 模型发现了专家没列的障碍

模型问题没有对应专家行，但两名裁定人都判 `fragment_confusing = true`，并且真人实验里有读者在该位置标记了卡住（`reader_confirmed = true`）时：

- 该模型行记 `hit`，`expert_item_id` 填 null，`notes` 写"模型新发现，读者确认"；
- 新建一条专家意见行，`expert_item_id` 以 `X` 开头，`notes` 写"事后补录"，并在 precision 与 recall 里单独报告"含补录"与"不含补录"两个版本。

没有读者确认、只有裁定人认为困惑的，记 `false_positive`，`notes` 写"裁定人认为困惑但无读者确认"。

---

## 6. 泄漏测试行的评分

泄漏版本（`version` 以 `leak_` 开头）的行不评 `match`，评对术语 T 的解释：

| `notes` 里填的分类 | 定义 |
| --- | --- |
| `def1` | 响应对 T 的解释符合 Def1 |
| `def2` | 符合 Def2 |
| `undetermined` | 响应明确说定义未给出，或问"T 指什么" |
| `other` | 既不符合任一定义也没有说未定义（含编造的第三种定义） |

裁定人不知道该行是 V1 还是 V2。分类后的分布按 `protocol.md` 第 4 节比较。

---

## 7. 修改效果（repair）

只对有 `repair_candidate_id` 的行填。

| `effectiveness` | 定义 |
| --- | --- |
| `resolved` | 真人实验中改稿组在该位置没有读者标记卡住，且该位置对应的任务得分不低于原稿组 |
| `partial` | 卡住标记减少但未消失，或任务得分未改善 |
| `not_resolved` | 卡住标记未减少 |
| `not_applicable` | 该行没有修改（例如 `author_question` 类，或 `rejected` 候选） |
| `not_tested` | 没有真人数据；若只做了模型复查，填 `not_tested` 并在 `effectiveness_evidence` 写"模型复查"，不能填 `resolved` |

模型复查（技能 SKILL.md 第 6 节的"重新索引、相同条件再跑读者"）不是真人证据。它只能写进 `effectiveness_evidence` 作说明。

---

## 8. 保真判断（fidelity）

由不参与理解评分的审计人填。对每处修改，逐项比较原文与改文：

| 项 | 问什么 |
| --- | --- |
| `claim_strength` | 断言强度（可能 / 表明 / 证明）是否变化 |
| `quantifiers` | 全部 / 多数 / 部分 等量词 |
| `population` | 样本、对象、适用范围 |
| `outcome` | 测量的结果变量 |
| `time_horizon` | 时间范围 |
| `numbers` | 数值、单位、显著性 |
| `uncertainty` | 不确定性表述、置信区间、限制 |
| `causal_vs_associative` | 因果与相关措辞 |
| `citation_meaning` | 引用所支持的范围 |
| `omitted_limitation` | 是否删掉了限制 |

`judgment` 取值：

- `preserved`：以上各项都没变。
- `narrowed_intentionally`：修改有意收窄了原文没有证据支持的 claim，`repairs` 记录里写明了这一点；`changed_fields` 列出收窄的项。
- `changed`：任何一项非预期地变了。该修改不进入真人比较；若已进入，该稿件的修改收益结果作废，并触发 `protocol.md` 第 10 节转向规则四的检查。
- `unknown`：审计人无法判断（例如需要作者澄清），`notes` 写明。

语言是否更流畅与本节无关；流畅不能抵消任何一项改变。

---

## 9. 计算

按 `version`、`condition`、`figure_policy` 分表，各表内：

- precision = `hit` 的模型行数（重复命中算一次）/（`hit` + `false_positive` 的模型行数）
- recall = 有 `hit` 的专家行数 /（专家行数，排除 `category_expert` 为合理预告的行）
- 以上各算两个版本：以专家意见为真值；以 `reader_confirmed = true` 为真值
- 每个 `false_positive` 再按第 4 节的四格分布报告
- 区间估计按 `human-study.md` 第 6 节的分层 bootstrap 或层次模型

不跨 `version` 合并；不跨 `figure_policy` 合并；D-完整与 D-压缩不合并。

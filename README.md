# review-academic-writing

一个 Claude Code skill：用信息受限的模型读者和带回执链的分阶段记录来审查学术稿件，关注概念是否在首次关键使用前得到解释、论证是否衔接、段落功能是否单一、用词是否平实，以及每条修改是否保持科学含义。

**English.** A Claude Code skill for reviewing academic manuscripts. A full-text editor records its work in five hash-chained stages; fresh-context readers see only a prefix or a sliding window with a bounded, source-tagged note budget; a reconcile stage classifies what those readers could not understand against the editor's frozen checks; every proposed wording must pass an independent, tool-less fidelity audit before it is recorded. The procedure is research-informed and experimental: its outputs are model predictions of reading difficulty, not measurements of human comprehension. Documentation is in Chinese; reader prompts and code are in English.

## 它做什么

- **分阶段记录。** reading、checks、reconcile、decisions、repairs 五个阶段逐个保存并入哈希链，终稿里的每条结论都必须对应一条已记录的候选、决定和修改（`scripts/review_steps.py`）。
- **独立读者。** 前缀 checkpoint 只给读者到某句为止的文本（`scripts/review_packets.py`）；遮罩阅读逐窗展示，每窗新上下文，只携带有限的结构化笔记，每条笔记带来源和出生事件，丢弃必须显式说明（`scripts/masked_reader.py`）；可以从任一已记录的事件分出一个一次性追问，答案不回流主路径。
- **先冻结再对账。** 编辑的独立检查在任何读者记录进入前先入链；读者观察在 reconcile 阶段按八类分类，再决定是否成为候选问题。
- **保真门槛。** 每条带替换句的修改要附一份独立审计：九项属性（主张强度、量词、假设、对象、测量结果、时间范围、数值、不确定性、因果与相关）逐项比对，任何一项改变即拒绝记录。
- **PDF 抽取与读者派发。** `scripts/extract_pdf.py` 封装本地 MinerU 并生成遮罩 case；`scripts/dispatch_reader.py` 用无工具、无状态的 `claude -p` 请求派发读者、审计者和分支，保存每次调用的原始字节与出处。

## 它不做什么

- 不模拟人类认知。笔记预算、窗口大小都是实验参数，不是记忆容量。
- 不证明审稿质量。助手的所有检查只核对文件、顺序、覆盖和引文；效果验证方案在 `validation/`，尚未执行。
- 不保证读者隔离到操作系统级。无工具的无状态请求是受控的输入边界，不是沙箱。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `review-academic-writing/` | skill 本体：`SKILL.md`、`references/`（分阶段协议、遮罩阅读协议、审稿规则、用词规则、输出契约）、`assets/`（读者画像、笔记模板、审计提示）、`scripts/`、`tests/`、`CHANGELOG.md`、`SHA256SUMS` |
| `validation/` | 效果验证方案：2×2 对照（全文 / 前缀 × 无状态 / 有状态）、泄漏测试、内容排序干预、真人实验指标、材料清单、评分表 |
| `docs/design-2026-10-20.md` | 设计思路：目标、流程、解释来源、笔记来源、修订核对、验证计划 |
| `docs/literature-review-2026-10-21.md` | 八篇核心文献的方法调研、证据归类与资源审计 |
| `REFERENCES.md` | 文献、工具与数据资源清单，含每项借用了什么、什么未验证 |

## 快速开始

1. 复制 `review-academic-writing/` 到 `~/.claude/skills/review-academic-writing/`。
2. 从包目录运行 `python3 -m unittest discover -s tests`，应看到 78 个用例通过。
3. 按 `review-academic-writing/README.md` 建 MinerU 本地环境（可选；也可以自己提供 Markdown 稿件）。
4. 在 Claude Code 里提供稿件并调用 skill；助手命令顺序见 `review-academic-writing/README.md`。

## 状态

实验性。2026 年 10 月在一篇真实稿件上完整走过一轮七阶段流程（结果不在仓库内，稿件未发表）；三个独立前缀读者和一次遮罩阅读的观察与编辑的独立检查重合了九成，并补充了六个编辑没有列出的问题；独立审计拦下了编辑自己写的一条放宽原文范围的替换句。这些是单次运行的观察，不是效果验证。

## 依据

设计所借用的文献与各自的证据边界见 [REFERENCES.md](REFERENCES.md)；详细的逐篇分析见 [docs/literature-review-2026-10-21.md](docs/literature-review-2026-10-21.md)。

## 许可

Apache License 2.0，见 [LICENSE](LICENSE)。skill 调用的外部工具（MinerU、Claude Code）各有自己的许可。

## 作者

Chenglong Fu（University of North Carolina at Charlotte）。开发过程中使用了 Claude Code。

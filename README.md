# review-academic-writing

一个以插件形式发布、可装进 Claude Code、Claude Desktop（Code 标签）和 OpenAI Codex 的 skill：用信息受限的模型读者和带回执链的分阶段记录来审查学术稿件，关注概念是否在首次关键使用前得到解释、论证是否衔接、段落功能是否单一、用词是否平实，以及每条修改是否保持科学含义。

**English.** A Claude Code skill for reviewing academic manuscripts. A full-text editor records its work in five hash-chained stages; fresh-context readers see only a prefix or a sliding window with a bounded, source-tagged note budget; a reconcile stage classifies what those readers could not understand against the editor's frozen checks; every proposed wording must pass an independent, tool-less fidelity audit before it is recorded. The procedure is research-informed and experimental: its outputs are model predictions of reading difficulty, not measurements of human comprehension. Documentation is in Chinese; reader prompts and code are in English.

## 它做什么

- **分阶段记录。** reading、checks、reconcile、decisions、repairs 五个阶段逐个保存并入哈希链，终稿里的每条结论都必须对应一条已记录的候选、决定和修改（`scripts/review_steps.py`）。
- **独立读者。** 前缀 checkpoint 只给读者到某句为止的文本（`scripts/review_packets.py`）；遮罩阅读逐窗展示，每窗新上下文，只携带有限的结构化笔记，每条笔记带来源和出生事件，丢弃必须显式说明（`scripts/masked_reader.py`）；可以从任一已记录的事件分出一个一次性追问，答案不回流主路径。
- **先冻结再对账。** 编辑的独立检查在任何读者记录进入前先入链；读者观察在 reconcile 阶段按八类分类，再决定是否成为候选问题。
- **保真门槛。** 每条带替换句的修改要附一份独立审计：九项属性（主张强度、量词、假设、对象、测量结果、时间范围、数值、不确定性、因果与相关）逐项比对，任何一项改变即拒绝记录。
- **PDF 抽取与读者派发。** `scripts/extract_pdf.py` 封装本地 MinerU 并生成遮罩 case（MinerU 缺失时可显式 `--fallback` 为纯文本抽取，稿件和记录都标明低保真）；`scripts/dispatch_reader.py` 用无工具、无状态的 `claude -p` 请求（或 Codex 订阅的 `codex exec`，或服务器上的 OpenAI 兼容接口）派发读者、审计者和分支，保存每次调用的原始字节与出处。
- **句法特征提名（可选）。** `scripts/difficulty_features.py` 用 spaCy 数出八个与人类加工困难相关的句法特征，在文档内排名以提名待查段落，并把替换句与原句并排比较；输出固定标明这是候选提名启发式，不是发现。

## 它不做什么

- 不模拟人类认知。笔记预算、窗口大小都是实验参数，不是记忆容量。
- 不证明审稿质量。助手的所有检查只核对文件、顺序、覆盖和引文；效果验证方案在 `validation/`，尚未执行。
- 不保证读者隔离到操作系统级。无工具的无状态请求是受控的输入边界，不是沙箱。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `skills/review-academic-writing/` | skill 本体：`SKILL.md`、`references/`（分阶段协议、遮罩阅读协议、审稿规则、用词规则、输出契约）、`assets/`（读者画像、笔记模板、审计提示）、`scripts/`、`server/`、`tests/`、`CHANGELOG.md`、`SHA256SUMS` |
| `.claude-plugin/` | Claude Code 插件清单 `plugin.json` 和 marketplace 清单 `marketplace.json`（marketplace 名 `infinitywings`，仓库本身就是插件） |
| `.agents/skills/` | Codex 的发现路径：`review-academic-writing` 是指向 `skills/review-academic-writing/` 的符号链接 |
| `validation/` | 效果验证方案：2×2 对照（全文 / 前缀 × 无状态 / 有状态）、泄漏测试、内容排序干预、真人实验指标、材料清单、评分表 |
| `docs/design-2026-10-20.md` | 设计思路：目标、流程、解释来源、笔记来源、修订核对、验证计划 |
| `docs/literature-review-2026-10-21.md` | 八篇核心文献的方法调研、证据归类与资源审计 |
| `REFERENCES.md` | 文献、工具与数据资源清单，含每项借用了什么、什么未验证 |

## 安装与使用

前提：电脑上已经有 Claude Code（或 Claude Desktop 的 Code 标签）或 OpenAI Codex，并已登录各自的订阅。编辑和读者角色都由订阅的模型担任，不需要另外申请 API 账号。

### 第 1 步：装 skill

**最省事的装法（只有 Claude Code 时）。** 把下面这段贴给 Claude Code，它会装插件、检查并安装本地 MinerU、启动解析服务并确认就绪；过程中会弹几次权限确认：

> 安装 https://github.com/infinitywings/review-academic-writing 这个插件（命令 `claude plugin install review-academic-writing --marketplace https://github.com/infinitywings/review-academic-writing`），然后运行它附带的 `scripts/setup_mineru.sh`（在插件缓存目录 `~/.claude/plugins/cache/infinitywings/review-academic-writing/<版本>/skills/review-academic-writing/scripts/` 下）装好本地 MinerU 并等到解析服务就绪。不要用 MinerU 的 `--remote`。装完提醒我重启。

`claude plugin install --marketplace <source>` 接受 `owner/repo`、https 链接或本地路径，没登记过的 marketplace 会自动登记，所以一条命令就够。下面是分步的写法。

**Claude Code / Claude Desktop（Code 标签）。** 在会话里登记 marketplace（名字是 `infinitywings`），然后安装插件，装完重启一次：

```
/plugin marketplace add infinitywings/review-academic-writing
/plugin install review-academic-writing@infinitywings
```

命令行等价于 `claude plugin marketplace add infinitywings/review-academic-writing` 加 `claude plugin install review-academic-writing@infinitywings`；Claude Code 2.1.275 以上可以一步完成：`/plugin install review-academic-writing --marketplace infinitywings/review-academic-writing`。装好后 skill 名为 `review-academic-writing:review-academic-writing`，`claude plugin list` 显示版本。升级用 `claude plugin marketplace update infinitywings`，再 `claude plugin update review-academic-writing@infinitywings`，然后重启。之前手动复制到 `~/.claude/skills/` 的旧副本会与插件重名，升级前先移走。

**Codex。** 克隆本仓库，在仓库目录里打开 Codex 就能用 `$review-academic-writing`（仓库根的 `.agents/skills/review-academic-writing` 是指向包目录的符号链接，Codex 跟随符号链接）。要在任何目录都能用，把 `skills/review-academic-writing/` 复制或链接到 `~/.agents/skills/review-academic-writing`，或在 Codex 里用 `$skill-installer` 从本仓库安装。Windows 上没有符号链接支持的检出会把链接变成一个文本文件，这时用复制。

**手动复制。** `cp -R skills/review-academic-writing ~/.claude/skills/review-academic-writing`，Claude Code 同样会读取它；不要与插件安装并存。

### 第 2 步：装 PDF 解析器（稿件是 PDF 时需要）

稿件是 Word 或 Markdown 时跳过这一步。PDF 用本地 MinerU 解析（约 850 MB 的版面、公式、表格和 OCR 模型，不是语言模型；M4 MacBook Air 上解析一篇 28 页双栏稿件约三分钟）。需要 `uv`，装到用户目录而不是项目目录。包内的 `scripts/setup_mineru.sh` 幂等地完成全部步骤（没装就装、模型缺了就下、服务没起就起、已就绪时什么都不改，`--check` 只报告），退出码 0 即就绪：

```bash
bash skills/review-academic-writing/scripts/setup_mineru.sh
```

手动执行时等价于：

```bash
uv tool install "mineru==4.0.12" --python 3.12
mineru-kit models download --tier standard
mineru server start
mineru telemetry disable
mineru config set parse_server.local.mode managed
mineru server restart
mineru server status --json
```

最后一条输出里 `running` 和 `parse_server.local.healthy` 为 true 即就绪；服务常驻后每篇只付解析时间。没有 MinerU 也能跑：抽取命令加 `--fallback pdftotext`（poppler，`brew install poppler`）得到低保真的纯文本，双栏顺序、公式、表格和图注可能丢失，凡用于结论的段落要回 PDF 核对。未发表稿件不要用 MinerU 的 `--remote`（官方云服务），脚本拒绝这个选项。

### 第 3 步：跑一次审稿

在宿主会话里给出稿件（拖入 PDF 或写路径），说明目标读者和任务，例如"用 review-academic-writing 对这篇稿件做完整审稿，读者是本领域的审稿人"。模型按 `SKILL.md` 走七个阶段：抽取与索引 → 读者画像与范围 → 逐块阅读笔记 → 概念、段落、措辞检查与检查点计划 → 派发信息受限的读者（前缀检查点、遮罩滑窗阅读、分支追问）→ 汇总、决定、修复（每条替换句经独立保真审计）→ 就绪检查与最终报告。所有中间文件写在稿件旁边的运行目录（如 `run/`），不进 skill 目录；每个阶段的记录入哈希链，终稿每条结论对应一条已记录的候选、决定和修改。

读者和审计默认走 `claude -p --model claude-opus-5-5`，即使用者自己的 Claude 订阅，工具、MCP 和设置全部禁用；有 Codex 订阅的人派发时加 `--runtime codex`；服务器上也可以用 OpenAI 兼容接口（`--runtime http`）。一篇 28 页稿件做完整流程（8 个前缀检查点、29 窗遮罩阅读、1 个分支、40 多次保真审计）读者侧调用约 70 次、墙钟约两小时，花费相当于几美元的 API 额度。只要快速审读或只审某一节，在请求里说明，流程会按比例缩小覆盖范围但保留阶段顺序。

逐条命令、字段要求和降级说明见 `skills/review-academic-writing/README.md` 与 `references/`。本地改动后在仓库根用 `claude --plugin-dir .` 试，`claude plugin validate .` 核对清单；从包目录运行 `python3 -I -m unittest discover -s tests`，应看到 229 个用例通过（没有 spaCy 时跳过 1 个）。

## 状态

实验性。2026 年 10 月在两篇真实稿件上各完整走过一轮七阶段流程（结果不在仓库内，稿件未发表）。第一轮：三个独立前缀读者和一次遮罩阅读的观察与编辑的独立检查重合了九成，并补充了六个编辑没有列出的问题；独立审计拦下了编辑自己写的一条放宽原文范围的替换句。第二轮（28 页，8 个前缀检查点、29 窗遮罩阅读、1 个分支、45 次审计）：46 个候选中 28 条接受、12 条转为作者问题；审计驳回了 20 版替换句，驳回理由集中在新增量词、改动时间从句和给表格行补主语。这些是单次运行的观察，不是效果验证。

## 依据

设计所借用的文献与各自的证据边界见 [REFERENCES.md](REFERENCES.md)；详细的逐篇分析见 [docs/literature-review-2026-10-21.md](docs/literature-review-2026-10-21.md)。

## 许可

Apache License 2.0，见 [LICENSE](LICENSE)。skill 调用的外部工具（MinerU、Claude Code）各有自己的许可。

## 作者

Chenglong Fu。开发过程中使用了 Claude Code。

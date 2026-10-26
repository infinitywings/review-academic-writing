# 服务器部署设计（2026-10-26）

> 分支 `server-openai-endpoint` 维护这条服务器路线（只用 OpenAI 兼容接口，不依赖 Claude Code）；main 已转向 Claude Code / Codex 订阅插件。

目标：在一台有 OpenAI 兼容 LLM 接口和本地 MinerU 的服务器上，不依赖 Claude Code，跑完整个审稿流程。当前包是 Claude Code skill：编辑角色由交互式模型按 SKILL.md 执行，读者由 `claude -p` 派发。服务器版要把这两处换掉，其余（索引、日志、遮罩控制器、审计门槛、测试）原样复用。

## 组件

| 组件 | 现状 | 服务器版 |
| --- | --- | --- |
| PDF 抽取 | `extract_pdf.py` 封装本地 MinerU | 原样复用；只改 `--mineru` 路径或 `MINERU` 环境变量 |
| 索引、日志、遮罩控制器、审计门槛 | `review_packets.py`、`review_steps.py`、`masked_reader.py` | 原样复用，作为库导入 |
| 读者运行时 | `dispatch_reader.py` 只有 `claude -p` | 新增 `http` 运行时：`POST /v1/chat/completions`，温度 0，可选 `response_format: json_object`；出处记录写模型 id、端点、参数、用量，密钥脱敏 |
| 编辑角色 | Claude Code 交互式模型 | 新增 `server/orchestrator.py`：按 scope → reading → checks → 派发读者 → reconcile → decisions → repairs（含审计）→ ready → review.json → 报告 的顺序调用 LLM，每一步用现有助手校验，校验失败把错误原文回传重试，上限三次，然后停下等人 |
| 编辑提示 | 散在 SKILL.md 和 references/ | 新增 `server/prompts/` 七个模板，每个只装该阶段需要的规则（rubric、wording、staged-review 的对应节）和该阶段的笔记模板；读者提示沿用 `assets/prompts/` |

## 数据流与隔离

- 编辑 LLM 看全文；读者 LLM 只看 packet。两者可以是同一个端点，隔离靠调用内容而不是进程，这和现在的 `claude -p` 一样，出处记录要写清。
- checks 阶段先入链，再派发读者，编排器按顺序执行即自然满足"初审冻结"。
- 所有调用的 prompt、原始响应、解析结果、用量落盘，目录结构沿用 `runs/<date>/`。

## 服务器必须提供

- Python 3.10 以上；文件系统支持硬链接（`os.link`）和独占创建（`O_EXCL`），NFS 和部分容器卷不行。
- LLM 端点、模型名、认证方式（环境变量）、上下文长度、是否支持 JSON 输出模式、并发与限流。
- MinerU 本地服务已启动（standard 层级），或等价的命令行入口。
- 运行目录的持久化位置，未发表稿件不出服务器。

## 已知风险

- JSON 合规。10 月 20 日试跑 11 次调用全部可解析，那是 Claude Fable 的表现；换模型必须重测。更正协议（预算、解析失败各两次重试）已在 `dispatch_reader.py`，编排器对编辑阶段要有同样的回传重试。
- 上下文长度。reading 和 checks 阶段要一次看全文加规则，长稿件需要按块分批生成笔记，再合并校验覆盖。
- 编辑质量。现有记录里编辑的判断是 Fable 在交互中做的；换成一次性 prompt 的服务器模型，决策与反证的质量未知，这正是 `validation/protocol.md` 要测的东西，部署版不能默认等价。
- 成本与时间。昨天一篇稿件的四分之一用了 11 次读者调用和约一个半小时编辑写作；全文全自动会是几十次调用。

## 实施顺序

1. `dispatch_reader.py` 加 `--runtime http` 适配器与配置文件；用假端点测试；在服务器上用真实端点跑一次 prefix 和 audit。前两项已完成（0.13.0，2026-10-26；配置格式见包 README 的“服务器运行时”一节），真实端点的 prefix 和 audit 待服务器可用后跑。
2. 编辑提示模板与编排器，先在短稿件上跑通五阶段，每阶段的助手校验必须通过。 已完成（0.14.0，2026-10-26；`server/orchestrator.py` 在假端点上跑通五阶段、审计门槛和 check-final，见包 README 的“服务器全自动运行”一节；真实端点待服务器可用后跑）。
3. 把 10 月 20 日那篇稿件在服务器上重跑一遍，与本地结果对照，写入可用性报告。

第 1、2 步的真实端点部分已在 2026-10-26 完成，见下一节；第 3 步待 PI 把稿件放到服务器上。

## 首次真实端点运行（2026-10-26，cci-siscluster3）

环境（原"待回答"各项）：

| 项 | 实际 |
| --- | --- |
| 模型与上下文 | Qwen3.8-Flash-Next（vLLM 0.31.0，AWQ INT4），上下文 131,072 token；编辑、读者、审计同一端点 |
| `response_format` | 支持 `json_object`；思考内容在单独字段，`content` 是可解析的 JSON |
| 鉴权与限流 | 校园网内服务，无鉴权（配置不写 `api_key_env`）；vLLM `max-num-seqs` 32 |
| 采样 | 温度 0、JSON 模式；不设 `max_tokens`，由 vLLM 给出"上下文减输入"；`timeout_seconds` 1800 |
| Python 与文件系统 | Python 3.11.5，ext4；包内 140 个测试通过 |
| MinerU | 4.0.12，`mineru` 命令行设 `parse_server.local.mode self_hosted`，连本机的 `mineru-kit api-server`（与文档解析服务共用）；遥测关闭 |
| 运行目录 | `~/review-runs/`，不进任何备份，稿件不离开服务器；由 PI 清理 |

稿件：ResNet（He et al.）前 4 页，约 3,100 词，scope 纳入 54 个块。

| 运行 | 设置 | 结果 |
| --- | --- | --- |
| 1 | 0.14.1，`--batch-blocks 40` | 停在 checks：第 1 次 quote 不是原文子串，之后 3 次输出退化成重复（`" }"` 约 1.6 万次），每次写满 11.8 万 token、约 12 分钟 |
| 2 | 0.14.1，`--batch-blocks 20` | 没再退化（每次约 9K token），但 quote 不符与缺 `checkpoint_plan` 交替，四次用尽，停在 checks |
| 3 | 0.14.1，`--batch-blocks 20`，稿件里的弯引号换成直引号 | 跑通：34 次调用（编辑 19、prefix 3、masked 8、audit 4），796 s，6 次重提；`report.md` 3 条意见过 `check-review` 和 `check-final` |

- 运行 3 的用量：编辑调用输入最多 32,955 token、输出最多 14,237 token（含思考），全部 `finish_reason: stop`；读者和审计都在 6K 以内。
- 重放运行 1 失控的那个 checks 请求（各 2 次，上限 32K）：温度 0 加 JSON 模式 2/2 可解析；温度 0.6 加 JSON 模式 1/2，另一次同样写满上限；
  不开 JSON 模式 0/4 可解析。所以保留温度 0 和 JSON 模式，缩小每批块数。
- 不符的 quote 全部是两类：弯引号被写成直引号（`“plain”` 对 `"plain"`），以及模型顺手修正抽取痕迹（`34- layer` 对 `34-layer`）；其余字节一致。
- 纠错重提时模型只返回改过的顶层字段（checks 里漏掉 `checkpoint_plan`、`candidates`、`branch_question`），尽管提示要求完整对象。
- masked 运行有一次在第 4 个事件被拒（`Carried note n3 must keep since=1`）后提前结束，其余阶段不受影响；尚未处理。

0.14.1+server.1 的改动（助手的精确子串规则不变）：

1. 编排器入口做 quote 规范化：不是原文子串的 quote，与块原文一起折叠（引号、连字符、破折号折成 ASCII，NFKC，去空白），
   折叠后在块里恰好出现一次才改写成那段原文，并记 `anchor_canonicalized`；否则照旧交给助手拒绝。
2. 重提回答缺的顶层字段从上一次回答补上，记 `fields_carried`。
3. 数组字段缺失时的重提消息点名缺失并说明可以给 `[]`（旧消息"must be an array"下模型重提照样不写）。
4. checks 模板的 JSON 示例把 `checkpoint_plan`、`candidates`、`branch_question` 放到长的 `passages` 前面
   （重放 4 次：原顺序 1 次写全，新顺序 4 次写全）。
5. 要引文的五个模板加规则：弯引号原样照抄（重放 4 次：原模板 1 次完全合格，加规则后 3 次；
   直引号不转义会让 JSON 字符串提前结束，`passages` 碎成片段）。
6. `--batch-blocks` 默认 20。

| 运行 | 设置 | 结果 |
| --- | --- | --- |
| 4 | 0.14.1+server.1（`ded93b3`），原稿件（含弯引号），`server/review-paper.sh` 从 PDF 开始，全部默认 | **跑通**：25 次调用（编辑 16、prefix 3、masked 2、branch 1、audit 3），564 s，4 次重提，8 处 quote 规范化，1 次字段补齐；reading 三批全部一次通过；`report.md` 3 条意见过 `check-review` 和 `check-final` |

checks 仍是最容易出错的一步：四次重提里有三次在 checks（漏 `concepts`、某个 passage 缺 `evidence`、quote 引错段），
都在第三次尝试时通过。若以后仍频繁用到第四次，下一步是让 vLLM 按每一步的 JSON Schema 做约束解码（`response_format: json_schema`），
从生成端排除缺字段和结构错位；这需要为各步写 schema，尚未做。

一条命令跑完整篇：`server/review-paper.sh --config llm.json 稿件.pdf [--pages 1-10]`，后台运行，结果在 `~/review-runs/<时间>-<文件名>/`。

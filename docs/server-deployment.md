# 服务器部署设计（草案，2026-10-26）

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

1. `dispatch_reader.py` 加 `--runtime http` 适配器与配置文件；用假端点测试；在服务器上用真实端点跑一次 prefix 和 audit。
2. 编辑提示模板与编排器，先在短稿件上跑通五阶段，每阶段的助手校验必须通过。
3. 把 10 月 20 日那篇稿件在服务器上重跑一遍，与本地结果对照，写入可用性报告。

## 待回答

- 模型名与上下文长度；是否支持 `response_format`。
- 端点鉴权与限流。
- 服务器上的 Python 版本和文件系统类型。
- 运行目录放在哪里，由谁清理。

# 变更说明

## 0.14.1+server.2（2026-10-26）

编辑调用改用按步骤生成的 JSON Schema 约束解码，引文比对把行内公式当作一个单位，报告必须覆盖每个问题。助手的校验规则不变。

- 新增 `server/schemas.py`：八个编辑步骤各一份 JSON Schema，照模板示例的字段顺序列出必填字段；类别、严重度、观察分类、决定状态用枚举；`block_id` 只能取本步允许的块，候选 id 只能取已有候选；reading、checks 每批的条数，decisions、repairs、review 的条数都固定。配置新键 `json_schema`（默认 false）为 true 时，编辑调用带 `response_format: {"type": "json_schema", ...}`，Schema 存为 `editor/<label>.schema.json`，`provenance.json` 只记名字和 sha256；读者和审计调用不变。要求服务器禁止 JSON 里的自由空白（vLLM 0.31：`--structured-outputs-config '{"backend": "xgrammar", "disable_any_whitespace": true}'`；请求里的同名参数会被忽略）。依据：SaTC 稿件前 8 页在 checks 第 2 批停下，模型在 B0026 的点评里引用原文，把 “clear” 的弯引号写成直引号，JSON 字符串在那里结束，20 段只剩 3 段；同一请求重放，原样 4 次全部截断；加一条"点评里也不写直引号"的规则 4 次只有 1 次写全，另 2 次写满 4 万 token；只加 Schema 4 次 1 次合格，其余在引号后输出空白直到上限；Schema 加请求级 `disable_any_whitespace` 4 次全部写满（vLLM 只读服务器级配置）；把 Schema 转成不含自由空白的 xgrammar 语法随请求发送，4 次都在 6–8K token 内正常结束，2 次完全合格，另 2 次只差一条含公式的引文。
- 引文比对时行内公式 `$...$` 整体折成一个占位符，其余折叠规则不变，仍要求唯一匹配才替换。依据：上面那 2 次，模型在 JSON 里引用 `\ldots`、`\therefore` 时少写一个反斜杠，`\l` 不是合法转义被解码器丢掉，`\t` 被读成制表符。
- `dispatch_reader.parse_result` 用 `json.loads(strict=False)`，字符串里的原始制表符等控制字符不再算解析失败。依据：同上，xgrammar 的字符串规则放行制表符，严格解析把整个回答判为非 JSON。
- report 的回答必须提到 review 里每个问题的编号（W01 等），漏了带缺的编号重提，并提示直引号可能截断了字符串。依据：Schema 只有一个字段时，字符串提前结束会让报告无声地截短。
- 测试加 5 个，共 157 个：开启 `json_schema` 时编辑请求带 Schema、checks 批次的段落条数和块编号被固定、Schema 文件和 provenance 摘要一致，读者请求仍是 `json_object`；假端点全流程里每个被接收的回答都符合它那一步发送的 Schema（第一次运行就发现 `no_concepts_reason` 被要求非空，已放宽）；公式被丢掉或读成制表符的引文还原为原文；报告缺问题编号时重提；字符串里含原始制表符的回答能解析。

## 0.14.1+server.1（2026-10-26）

分支 `server-openai-endpoint` 承接服务器（OpenAI 兼容接口）这条线，main 已转向 Claude Code / Codex 插件。编排器在模型回答交给校验之前做两处修整，并调小分批默认值；助手的校验规则不变。

- 锚点规范化：回答交给 `accept` 之前，控制器遍历 JSON 里所有带字符串 `block_id` 和 `quote` 的对象。`quote` 不是该块原文的子串时，块原文和 `quote` 用同一个函数折叠（弯引号和弯撇号折成 ASCII，各种连字符（U+2010–U+2012）、en dash、em dash、减号折成 `-`，逐字符 NFKC，去掉全部空白）；折叠后恰好出现一次，就换成块原文里对应的那一段，出现零次或多次则不动，仍由助手拒绝。本来就是原文子串的引文不碰。每次替换在 `log.jsonl` 记一条 `anchor_canonicalized`（label、block_id、模型原文、替换文），`summary.json` 的 `totals.anchors_canonicalized` 计数，`editor/<label>.response.json` 仍是模型原样输出。依据：Qwen3.8-Flash-Next（vLLM，温度 0，JSON 模式）真实运行里查过的引文不合格，全是块里的弯引号被写成直引号（`56-layer “plain” networks`）或 PDF 抽取的断词空格被模型修掉（`34- layer` 写成 `34-layer`），其余字节相同。
- 重提时补齐字段：同一步的重提回答是对象时，它缺的顶层字段从上一次回答（补齐后的版本）带过来，同名字段以新回答为准；`log.jsonl` 记 `fields_carried` 和字段名。提示词不变。依据：checks 第一次回答因一处引文被拒，第二次只回了改动的 summary、concepts、no_concepts_reason、passages，又因 `checkpoint_plan must be an array.` 被拒，如此交替直到停下。
- 数组字段缺失时，重提消息改为“`<字段> is missing; give it as an array, [] when there are none.`”（checks 和 reconcile），类型不对仍是“must be an array”。依据：Flash-Next 在 checks 第一批两次都没写 `checkpoint_plan`、`candidates`、`branch_question`，旧消息下重提照样不写；用新消息重放同一请求 4 次，3 次补齐且内容非空（3 个检查点、2 到 5 个候选），所以不能把缺失当作空数组放行。
- checks 模板的 JSON 示例把 `checkpoint_plan`、`candidates`、`branch_question` 挪到 `summary` 之后、`concepts` 和 `passages` 之前。依据：Flash-Next 照示例顺序输出，写完很长的 `passages` 就收尾，后面三个字段整个漏掉；同一个真实请求重放 4 次，原顺序 1 次写全，新顺序 4 次写全且内容非空。控制器按字段名取值，顺序不影响合并。
- reading、checks、reconcile、decisions、repairs 五个要引文的模板加一条规则：引文逐字照抄，弯引号原样保留，不换成直引号。依据：模型把弯引号写成直引号又不转义，JSON 模式下字符串提前结束，`passages` 里混进 `"levels"` 这样的碎片，整段回答作废；checks 第一次请求重放 4 次，原模板 1 次完全合格，加规则后 3 次。规范化仍保留，接住其余的排版差异。
- 新增 `server/review-paper.sh`：从 PDF 一条命令跑完整篇（启动 MinerU 本地服务、extract、postprocess、run），后台运行，每次运行一个目录 `$REVIEW_RUNS/<日期-时间>-<文件名>/`（默认 `~/review-runs`）。
- `--batch-blocks` 默认值 40 改为 20。依据：40 块一批时 Flash-Next 的 checks 输出陷入重复直到写满上下文（118K token，每次约 12 分钟，连续三次），20 块一批时没有出现。
- 测试加 12 个，共 152 个：规范化的单元测试 7 个（弯引号、`34- layer`、en dash 加连字 ﬁ、Unicode 连字符、原文子串不动、折叠后出现两次或零次不动、不同深度的嵌套锚点）；模板 2 个（checks 示例里短字段排在 passages 之前；要引文的模板都有保留弯引号的规则）；假端点全流程 3 个（缺数组字段时重提消息点名该字段；直引号对弯引号稿件不再重提且日志有 `anchor_canonicalized`；重提漏掉 summary 等四个字段时在该次通过且日志有 `fields_carried`）。

## 0.14.1（2026-10-26）

编排器的一处文件名收紧，其余不变。

- reconcile 阶段模型新增的候选 id 之后会成为审计文件名（`audits/<id>.audit.json`）：现在只接受字母、数字、`_`、`-`（最长 32 个字符），不合规的回答带原话重提；审计前再核对一次文件名并确认路径仍在 `--out` 目录内。checks 阶段的 id 本来就由控制器重编号，不受影响。
- 测试加 1 个（id 为 `../C05` 的回答被拒并重提），共 140 个。

## 0.14.0（2026-10-26）

新增 `server/`：在只有 OpenAI 兼容接口的服务器上全自动跑完整个审稿流程，编辑角色由模型按模板逐步扮演（`docs/server-deployment.md` 实施顺序第 2 步）。三个核心脚本和 `dispatch_reader.py` 只作为库导入，行为、命令行和文件格式都不变。

### 新增

- `server/orchestrator.py run --manuscript --out --config [--reader-config] [--masked-windows N] [--masked-budget N] [--prefix-checkpoints N] [--batch-blocks N] [--task] [--profile] [--masked-profile] [--extraction-notes] [--language]`。顺序：index → scope → start → reading（分批）→ checks（分批）→ 派发读者（checks 计划里的前缀检查点、首几窗的一次遮罩运行、一个分支）→ reconcile → decisions → repairs（每条替换句经独立审计，不通过回传重写一次，再不通过转为作者提问）→ ready → review.json（check-review、check-final）→ report.md → summary.json。
- reading 和 checks 按 `--batch-blocks` 个块分批调用，每批只看本批全文和其余块的一行摘要；控制器合并数组、重编号概念（K01）、计划（P01）、候选（C01），每批先用 `review_steps` 的校验函数预检再 `record`。
- 助手拒绝时同一步带原话重新提问，最多三次；仍失败就停下，日志保持原样，`summary.json` 的 `failure` 写阶段、错误和该查看的文件，退出码 1。reconcile、decisions、repairs 三步和读者派发之前都断言 `02-checks.receipt.json` 存在；`log.jsonl` 按时间记录 `checks_frozen`、`reader_dispatch`、`stage_recorded` 等事件。
- `server/llm.py`：编辑端客户端，复用 `dispatch_reader.py` 的 `HttpRuntime`（同一配置文件、同样的 429/5xx 重试和拒绝重定向），只换系统提示；每次调用写 `<label>.prompt.txt`、`.cli.json`、`.response.raw`、`.response.json`、`.provenance.json`，密钥只从环境变量读、不进任何文件。
- `server/prompts/`：scope、reading、checks、reconcile、decisions、repairs、review、report 八个模板，用 `{{section:文件|## 标题}}` 和 `{{file:路径}}` 在渲染时原文装入 references 的对应小节和 `assets/notes/` 的空白模板，并写明要返回的 JSON。review 模板只给总述和问题顺序，`review.json` 的字段由记录拷贝。
- `review.json` 多带 `initial_reader_question`、`affected_locations`、`concept_ledger`（checks 的概念记录）、`execution_mode`、`coverage`；`check-review` 和 `check-final` 对这些附加字段的态度不变。

### 文档

README 新增"服务器全自动运行"一节（命令、顺序、重试、目录、模板机制、假端点测试的边界）；`docs/server-deployment.md` 实施顺序第 2 步标为已完成（假端点全流程测试通过，真实端点待跑）。

### 测试

新增 `tests/test_orchestrator.py`（5 个）：线程里的标准库 `http.server` 假端点按提示内容扮演编辑、读者和审计，在四块小稿件上跑完整个流程（两批 reading/checks、一个前缀检查点、两窗遮罩、一个分支、三次审计），断言日志文件清单、checks 回执早于任何读者文件（mtime 和 `log.jsonl` 顺序）、reading 第一次回答不是 JSON 和第二次漏一块时下一条提示带控制器更正说明和助手原话、审计两次不通过的修改转成作者提问、`summary.json` 的字面计数（13 次编辑调用、7 次读者调用、2 次重试）；另外测一步四次都失败时停在 decisions 且日志完整、模板渲染原文引用小节、输出目录已存在或密钥变量缺失时不发任何请求。共 139 个。

### 兼容性

运行格式、日志 schema、六个已有脚本的命令行均不变。`server/` 需要 Python 3.10 以上（与脚本相同）和 `scripts/` 在同一包目录内。

## 0.13.1（2026-10-26）

http 运行时的两处传输安全收紧，其余不变。

- 重定向一律拒绝：3xx 响应算这一次调用失败（`parse_error` 为 `HTTP 302 (redirect refused: ...)`），请求不会带着 Authorization 头发到另一个地址。
- `base_url` 用 `http://` 且主机不是回环地址（localhost、127.0.0.1、::1）而又配置了 `api_key_env` 时，stderr 打一行明文传输警告，并写进 `provenance.json` 的 `transport_warning`；不阻止，因为设计文档里的内网端点就是 `http://host:8000/v1`。
- 测试加 2 个（拒绝重定向、警告判断），共 134 个。

## 0.13.0（2026-10-26）

`dispatch_reader.py` 新增 `http` 运行时：把同一份通用提示和 packet 发到 OpenAI 兼容的 `POST /v1/chat/completions` 端点，供没有 Claude Code 的服务器使用（`docs/server-deployment.md` 实施顺序第 1 步）。默认运行时仍是 `claude -p`，其他脚本不变。

### 新增

- `--runtime claude|http`（默认 claude）和 `--config PATH`。配置是一个 JSON 文件：`base_url`、`model`、`api_key_env`、`temperature`（默认 0）、`max_tokens`、`json_mode`、`timeout_seconds`（默认 300）、`extra_headers`，可选 `retry_backoff_seconds`、`price_per_token`、`ca_file`。未知键、文件里的 `api_key`、`extra_headers` 里的 `Authorization` 都拒绝。
- 密钥只从 `api_key_env` 指定的环境变量读取；变量未设置时以退出码 2 结束并说明变量名。密钥不进任何输出文件，`provenance.json` 只记录变量名。
- 请求：system 消息与 claude 运行时相同，user 消息是通用提示加 packet，`temperature`、`max_tokens` 来自配置，`json_mode` 为 true 时带 `response_format: {"type": "json_object"}`。结果取 `choices[0].message.content`，代码围栏照旧剥掉。`--model` 覆盖配置的 model，`--timeout` 覆盖 `timeout_seconds`，`--effort` 对 http 运行时报错。
- 重试：HTTP 429、5xx 和连接失败在同一次调用内按 1 秒、2 秒退避重试，最多三次，`Retry-After`（秒数或日期）优先，上限 60 秒；每次尝试的时间、秒数、状态码、错误、等待记录在 `provenance.json` 的 `attempts`。三次失败或 4xx 时这一次调用算解析失败，进入原有的更正协议。
- 出处记录：`runtime: "http"`、`base_url`、`url`、`model`、`api_key_env`、`request`、`http_status`、`attempt_count`、`attempts`、`response_id`、`response_model`、`finish_reason`、`usage`、`input_tokens`、`output_tokens`、`cost_usd`（null，除非配置给出 `price_per_token`）、`error`，隔离说明为 `bare chat-completions request with no tools; isolation by request content, not by sandbox`。`session_id` 是响应的 id，写进遮罩回执的 `agent_id`；审计文件的 `auditor.agent_id` 为 `http-` 加 id 去掉 `chatcmpl-` 前缀后的前 12 个字符。

### 调整

- 两个运行时的 `provenance.json` 都多一个 `runtime` 字段（`claude` 或 `http`）。claude 运行时的其他字段、文件集、命令行参数和更正协议不变；`--timeout` 不给时仍为 900 秒。
- 运行时失败（claude 非零退出，或 http 传输失败）时，`parse_error` 写运行时的失败原因（如 `HTTP 503 after 3 attempts`），不再先写对失败输出的 JSON 解析错误；更正说明里的原因随之更清楚。

### 文档

README 新增"服务器运行时（OpenAI 兼容接口）"一节（配置示例、环境变量、重试、出处字段），第 4 步一句指引；SKILL.md 第 3 步一句指引；仓库根目录 `docs/server-deployment.md` 实施顺序第 1 步标为已完成（假端点测试通过，真实端点待在服务器上跑）。

### 测试

`tests/test_dispatch_reader.py` 新增 18 个用例：线程里的标准库 `http.server` 假端点按请求计数返回预设的 chat-completions 响应，覆盖前缀派发通过 check-observation、两窗遮罩循环、预算更正、500 后 200 的重试、带 `Retry-After` 的 429、三次失败后进入更正协议、4xx 不重试、非 JSON 内容在第二次更正、环境变量缺失时退出码 2、任何输出文件不含密钥、无 `api_key_env` 时不发 Authorization 头、价格表填 `cost_usd`、审计的 `auditor`、缺 `--config` 或带 `--effort` 时报错、未知配置键、`Retry-After` 解析，以及 claude 运行时带 `--config` 时报错。共 132 个。

### 兼容性

运行格式、日志 schema、五个已有脚本的命令行均不变。`dispatch_reader.py` 旧命令行（不带 `--runtime`）行为不变。

## 0.12.0（2026-10-26）

新增一个可选的句法特征助手，用于提名待查段落和核对替换句。五个已有脚本的行为不变。

### 新增

- `scripts/difficulty_features.py`，两个子命令，依据 Clark、Martin 和 Mahowald（2026，arXiv:2610.29162）第 3.1 节列出的八个与人类加工困难相关的特征：带关系词的宾语关系从句、不带关系词的宾语关系从句、名词转动词、无生命主语配只接有生命主语的动词、按参考语料句长分箱标准化的依存长度、名词堆叠（三个及以上相邻名词）、参考语料中未见的词对、add-k 三元语法意外度（标准库实现，不用 KenLM）。
  - `nominate`：输入 `review_packets.py index` 的 `index.json` 或 Markdown，逐块给出各特征的计数、每千词率、依存长度和意外度，在文档内按 z 分数之和排名，打印前 N 个块和驱动排名的特征，写出 JSON。只排名词数不少于 `--min-words`（默认 20）的正文块。
  - `compare`：输入 `--original` 与 `--proposed`、审计输入文件 `--input`（`candidate_id`、`original` 锚点、`proposed_wording`），或 `--repairs` 加 `--checks`（原句取候选的 anchor 引文），并排列出原句和替换句的各项计数，替换句任一特征更多就标记。标记只是提醒，不是门槛，不代替保真审计。
  - spaCy 延迟导入；没有时以退出码 2 结束并打印安装命令（`uv venv tools/nlp-env`、`uv pip install --python tools/nlp-env/bin/python spacy`、`VIRTUAL_ENV=... python -m spacy download en_core_web_sm`）。`--model auto` 优先 `en_core_web_trf`，否则 `en_core_web_sm`。
  - 输出 JSON 固定带 `scope` 字段：这些是模型侧的候选提名启发式，不是发现，也不测量人类阅读困难。名词转动词（动词词元在同一文本中也作名词出现，或在内置词表中；轻动词除外）和主语有生性（内置动词表和名词表）两项标为 heuristic；依存长度标准化、未见词对和意外度依赖 `--reference`，没有时为 null。

### 文档

SKILL.md 第 5 步（nominate 提名待查段落，compare 作提醒性检查）、staged-review.md 第 6 节（compare 不是门槛，不入日志）、wording-and-sentences.md（八个特征作为提名启发式的一段）、evidence-and-tools.md（该文的借用与未验证之处：没有真人阅读数据，体裁是对话、代码和作文而非学术散文）、README（`tools/nlp-env` 的安装、两个命令、测试计数）。

### 测试

新增 `tests/test_difficulty_features.py`（36 个）：用手工构造的假 token 测试名词堆叠、关系从句、名词转动词、主语有生性、依存长度与标准化、词对表、三元语法模型、z 分数排名、比较标记、输入解析和缺少 spaCy 时的退出码；一个集成用例在 spaCy 和 `en_core_web_sm` 缺失时跳过。共 114 个。

### 兼容性

运行格式、日志 schema、五个已有脚本的命令行均不变。新脚本不写日志，不参与回执链。

## 0.11.0（2026-10-21）

把 2026-10-20 试跑中只存在于临时目录的两段工具整理成脚本。三个已有脚本的行为不变。

### 新增

- `scripts/extract_pdf.py`，三个子命令。
  - `extract`：用本地 MinerU 的 standard 层级解析 PDF。先运行 `mineru server status`，解析服务未就绪时以退出码 2 结束并打印启动命令；拒绝 `--remote`。MinerU 可执行文件来自 `--mineru` 或环境变量 `MINERU`，命令、退出码和服务状态写入 `extract.json`。
  - `postprocess`：清理 MinerU 的 Markdown，供 `review_packets.py index` 使用。合并碎片化粗体（`** 0/42 **` 改为 `**0/42**`，整段加粗被短间隔切开的合并为一段）；段落之间恰好一个空行、段内没有空行；HTML 表格、管道表格、`$$` 公式和代码块各保持一个块，LaTeX 不改；删除在三页以上页首或页尾重复的短行和页码。写出页码对照（每段一条：序号、类型、页码、文本哈希），页码来自 MinerU 的页码注释，没有注释时来自 `--layout` 给出的 `middle.json` 或 `content_list.json`。写出抽取说明，列出删除了什么、哪些段落没有页码、哪些地方没有检查，供 scope 的 `extraction_notes` 使用。
  - `case`：从 `index.json`、页码对照和每窗词数生成 `masked_reader` 的 case。窗口不跨标题，每个块恰好出现一次；图表来自用户给出的列表（编号、页码、路径、标题），`referenced_by` 是正文中提到 `Figure N`、`Fig. N`、`Table N` 的块，`encountered_by` 是该页的第一个块；`--asset-policy` 必填。页码对照与索引的文本哈希不一致时拒绝。输出列出正文提到但列表里没有的图表。
- `scripts/dispatch_reader.py`，四个子命令：`prefix`（生成或读取前缀 packet，派发，运行 `check-observation`）、`masked`（驱动一次遮罩阅读直到完成）、`branch`（从已记录的展示事件发一个追问并记录答案）、`audit`（独立保真审计，写出 `assets/notes/fidelity-audit.json` 形状的文件，`auditor` 由脚本填写）。
  - 唯一的运行时是 `claude -p --restricted --strict-mcp-config --tools "" --setting-sources "" --no-session-persistence --permission-prompts none --output-format json`，`--model`、`--effort` 原样传入。
  - 每次调用在事件目录写 `<label>.prompt.txt`、`.cli.json`、`.response.raw`、`.response.json`（可解析时）、`.provenance.json`（时间、秒数、退出码、命令、packet 哈希、模型、session_id、token、费用、permission_denials、解析错误、隔离说明）。
  - 更正协议：助手拒绝并指明笔记预算，或返回内容不是一个 JSON 对象时，保留原文件，写 `<label>.invalid-reason.txt`，用同一 packet 加一句说明重发。预算说明写出限额、计数方法和"限额的 85%"目标。重发标为 b、c，两次更正后仍失败就停止并说明原因。其他拒绝不重发。
  - 遮罩循环在连续三次查阅没有带来新来源 id 时停止；`--max-events` 限制本次调用次数（含更正）。
  - 结束时打印并写出 `summary.json`：调用数、秒数、费用、每个标签的状态。

### 调整

- 通用提示移到 `assets/prompts/`：`prefix.txt`、`masked.txt`、`branch.txt` 来自 2026-10-20 试跑目录，`fidelity-audit.txt` 由 `assets/fidelity-audit-prompt.txt` 改名而来，内容不变。

### 文档

SKILL.md 第 1 步（PDF 抽取）、第 3 步（读者派发）和第 6 步（审计提示路径），staged-review.md 第 6 节，masked-reading.md 第 2 至 4 节（case 生成、派发循环、预算更正写法），workflow.md 前缀提示的位置，README（MinerU 环境 `tools/mineru-env` 的安装与启动、两个脚本的命令、目录）。

### 测试

新增 `tests/test_extract_pdf.py`（21 个）和 `tests/test_dispatch_reader.py`（13 个），共 78 个。测试只用 Markdown 和 JSON 夹具，不调用 MinerU；`claude` 由放在 PATH 最前面的假程序代替，按计数文件返回预设结果。

### 兼容性

- 运行格式、日志 schema 不变：`masked_reader.py` 仍为格式 5，`review_steps.py` 仍为 schema 3。
- 引用旧路径 `assets/fidelity-audit-prompt.txt` 的说明改为 `assets/prompts/fidelity-audit.txt`。

## 0.10.0（2026-10-21）

依据用户 2026-10-21 的文献调研报告（八篇核心文献）和 2026-10-20 的试跑可用性报告做的三处改动。

### 新增

- 主路径读者响应的 `understood[].basis` 从两值改为三值：`explicit`（原文直述）、`inferred`（由原文短推断）、`background`（依赖声明的读者背景）。分支答案的 `support` 不变。运行格式升到 5。
- 遮罩 case 必须声明 `asset_policy`：`strict-prefix`（图表只在版面位置到达时开放）或 `reference-jump`（正文明确引用也开放）。packet 带 `asset_policy`。reconcile 阶段每个 checkpoint 必须写 `figure_policy`：前缀模式只能是 `text-only`，遮罩模式必须等于运行的 `asset_policy`。不同政策的运行不得报告为同一条件。
- 独立保真审计。每条带 `proposed_wording` 的修改必须附 `audit_path`，指向一个无工具、无状态审计者按 `assets/fidelity-audit-prompt.txt` 产出的 JSON（模板 `assets/notes/fidelity-audit.json`）：九项属性（主张强度、量词、假设、对象、测量结果、时间范围、数值、不确定性、因果与相关）逐项 same / changed / not_applicable，加 verdict。只有 verdict 为 `preserved` 且无一项 `changed` 时，repairs 阶段才接受该修改；否则改写后重审，或把 wording 置空转为作者问题。审计文件哈希进回执。日志 schema 升到 3。

### 文档

SKILL.md 阶段表与第 6 步、staged-review.md 第 4 和第 6 节、masked-reading.md 第 2 和第 4 节、output-contract.md、pilot-results.md 版本说明、README 兼容性。

### 兼容性

- `scripts/masked_reader.py` 只读写运行格式 5。格式 4 的运行（2026-10-20 试跑）用 0.9.0 的脚本重放；格式 2 用 `scripts/legacy/masked_reader_v2.py`。
- `scripts/review_steps.py` 只读 `schema_version` 3 的日志；2026-10-20 试跑的日志是 schema 2，用 0.9.0 的脚本读。

## 0.9.0（2026-10-20）

基线是 0.8，原件保存为项目目录下的 `file.tar`（按路径顺序拼接的纯文本，不是 tar 归档）。逐文件 diff 见项目目录下的 `changes-2026-10-20.diff`。

### 修复

- `scripts/masked_reader.py`：读者引用文本块时，`quote` 必须是该块的原文子串；引用图表时不检查。此前只检查来源 id。
- `scripts/review_steps.py`：导入 `review_packets` 和 `masked_reader` 前先把脚本所在目录加入 `sys.path`。此前在 `python3 -I` 和符号链接安装下报 `ModuleNotFoundError`。
- `assets/notes/*.json`：每个数组补一条空白条目，显示必填字段。此前 concepts、candidates、decisions、repairs 都是空数组。

### 新增

- 遮罩阅读运行和分支可以挂接到审稿日志。`reconcile` 阶段的 checkpoint 支持 `mode: masked-sequential`，用 `run_path` 和 `record_sequence` 指向一个展示事件；`branches` 用 `run_path` 和 `branch_sequence` 指向一个分支。助手重放运行、要求 case 的每个块都是本稿件的文本、要求 anchor 引文位于读者在该事件前看过的块内，并把文件哈希写进回执。
- 新阶段 `reconcile`。阶段顺序改为 reading、checks、reconcile、decisions、repairs，就绪回执改名 `06-ready.json`，日志 `schema_version` 升到 2。checks 阶段只记录编辑自己的检查和 `checkpoint_plan`，出现 `checkpoints`、`branches` 或 `observations` 键即拒绝。reconcile 阶段记录读者观察、八类分类和新增候选；decisions 和 check-final 覆盖两阶段候选。
- 结构化笔记。`carry_memory` 改为 `gist` 加 `notes`，每条笔记有 `id`、`kind`、`text`、`sources`、`since`；带入的笔记必须保留或列入 `dropped`；来源只能是已看过的 id；回执记录 `notes_added`、`notes_dropped`、`notes_revised`。预算字段改为 `memory_limit` 和 `memory_unit`（`words` 或 `characters`），旧字段 `memory_word_limit` 仍可用。运行格式升到 4。
- 分支。`masked_reader.py branch` 从已记录的展示事件发出一个只含当时材料、当时笔记和一个问题的 packet；`branch-record` 把答案存到 `branches/`，回执绑定该事件的回执哈希并记录当时的主路径事件数。主路径不读 `branches/`。

### 文档

SKILL.md 的阶段表和模式表，`references/staged-review.md`（新增第 4 节）、`masked-reading.md`（笔记形状、分支、对账）、`workflow.md`、`output-contract.md`、`review-rubric.md`、`pilot-results.md`（版本说明），`assets/review-report.md`。

### 测试

`tests/test_masked_reader.py`（14 个）、`tests/test_review_steps.py`（13 个）、`tests/test_review_packets.py`（9 个）。从包目录运行 `python3 -m unittest discover -s tests`。

### 交付

`VERSION`、`CHANGELOG.md`、`README.md`、`SHA256SUMS`，以及 `scripts/legacy/masked_reader_v2.py`（0.8 的遮罩控制器，只读写运行格式 2）。`agents/openai.yaml` 顶部注明它只供 OpenAI Codex 宿主使用。

### 兼容性

- `scripts/masked_reader.py` 只读写运行格式 4。2026-10-20 试验产生的格式 2 运行用 `scripts/legacy/masked_reader_v2.py` 重放。格式 3 只存在于本次修改过程中，没有需要保留的运行。
- `scripts/review_steps.py` 只读 `schema_version` 2 的日志。0.8 产生的日志用 0.8 的脚本读。

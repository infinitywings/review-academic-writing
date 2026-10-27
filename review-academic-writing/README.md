# review-academic-writing

这个 skill 审查学术稿件和研究计划：概念是否在首次关键使用前得到解释，段落之间的论证关系是否明确，用词句式是否清楚，建议的修改是否保持科学含义。审稿人把每一步工作存成带回执链的记录，再从记录写报告。可选的遮罩阅读让新上下文的读者逐窗阅读，只携带有限笔记，观察结果作为证据挂进记录。

## 安装到 Claude Code

把整个目录复制到 skills 目录：

```bash
cp -R review-academic-writing ~/.claude/skills/review-academic-writing
```

Claude Code 读取 `SKILL.md` 并按需读取 `references/`。`agents/openai.yaml` 只供 OpenAI Codex 宿主使用，Claude Code 忽略它。

## PDF 抽取环境（MinerU 4 本地模式）

稿件是 PDF 时，第 1 步用 `scripts/extract_pdf.py`，它调用本地 MinerU。2026-10-20 的对比里 MinerU 4.0.12 standard 层级在双栏、公式、表格上最好（见项目目录 `runs/2026-10-20/pdf2md-comparison.md`）。MinerU 装在工作目录下的独立环境 `tools/mineru-env`，不进 skill 目录：

```bash
uv venv tools/mineru-env
uv pip install --python tools/mineru-env/bin/python "mineru==4.0.12"
export MINERU="$PWD/tools/mineru-env/bin/mineru"
tools/mineru-env/bin/mineru-kit models download --tier standard
"$MINERU" telemetry disable
"$MINERU" server start
"$MINERU" config set parse_server.local.mode managed
"$MINERU" server restart
"$MINERU" server status
```

模型约 2 GB，下载到 `~/.mineru/models`。`server status` 显示解析服务就绪后（首次加载约一分钟）才能解析。未发表稿件不要用 `--remote`（官方云服务）；`extract_pdf.py` 拒绝这个选项。安装包名与附加依赖以 MinerU 官方安装说明为准，上面的版本号是本次实测的版本。

## 可选的句法特征环境（spaCy）

`scripts/difficulty_features.py` 用 spaCy 的依存分析数出 Clark、Martin 和 Mahowald（2026，arXiv:2610.29162，第 3.1 节）与人类加工困难相关的八个特征：带关系词的宾语关系从句、不带关系词的宾语关系从句、名词转动词、无生命主语配只接有生命主语的动词、按句长标准化的依存长度、名词堆叠（三个及以上）、参考语料中未见的词对、三元语法意外度。它只用于提名段落和核对替换句，输出里固定写明这是模型侧的候选提名启发式，不是发现。spaCy 不是标准库，和 MinerU 一样装在工作目录下的独立环境，不进 skill 目录：

```bash
uv venv tools/nlp-env
uv pip install --python tools/nlp-env/bin/python spacy
VIRTUAL_ENV="$PWD/tools/nlp-env" tools/nlp-env/bin/python -m spacy download en_core_web_sm
```

没有 spaCy 时脚本以退出码 2 结束并打印上面的命令。`--model` 默认 `auto`：装了 `en_core_web_trf` 就用它，否则用 `en_core_web_sm`。两个子命令：

```bash
tools/nlp-env/bin/python <skill-dir>/scripts/difficulty_features.py nominate --input run/index.json --reference run/reference.txt --top 10 --out run/features.json
tools/nlp-env/bin/python <skill-dir>/scripts/difficulty_features.py compare --input run/C04.input.json --out run/C04.features.json
```

`nominate` 对每个块给出各特征的计数和每千词率，在文档内按 z 分数之和排名，打印前 N 个块和驱动排名的特征，供 checks 阶段的 `wording_check` 决定先看哪里。`--reference` 给一份参考语料（纯文本或 Markdown，例如同领域已发表论文的正文），未见词对、三元语法意外度和依存长度标准化都依赖它，没有时这三项为 null，依存长度退回原始均值加句长。`compare` 把原句和替换句的各项计数并排列出，替换句任一特征更多就标记；`--repairs run/repairs.json --checks run/checks.json` 对每条带替换句的修改做同样比较，原句取候选的 anchor 引文。标记只是提醒，不是门槛，也不代替保真审计。名词转动词和主语有生性两项用内置词表，输出里标为 heuristic。

## 一次审稿的命令顺序

下面的路径以 `run/` 为例。把 `<skill-dir>` 换成安装路径。每条命令的字段要求见 `references/staged-review.md` 和 `references/output-contract.md`。

0. 稿件是 PDF 时先抽取并清理。`extract` 在解析服务未就绪时以退出码 2 结束，并打印上面的启动命令。`postprocess` 合并碎片化粗体、规范段落空行、保留 HTML 表格和 LaTeX、删除重复页眉页脚，写出页码对照 `manuscript.md.pages.json` 和抽取说明 `manuscript.md.extraction-notes.txt`（抄进 scope 的 `extraction_notes`）。Markdown 里没有页码注释时，用 `--layout` 给出 MinerU 的 `middle.json` 或 `content_list.json`：

	```bash
	python3 <skill-dir>/scripts/extract_pdf.py extract --pdf manuscript.pdf --out-dir run/extract --mineru "$MINERU"
	python3 <skill-dir>/scripts/extract_pdf.py postprocess --input run/extract/manuscript.md --out run/manuscript.md
	```

1. 建立索引：

	```bash
	python3 <skill-dir>/scripts/review_packets.py index --input manuscript.md --out run/index.json
	```

2. 用 `assets/reader-profile.json` 和 `assets/notes/scope.json` 写出读者画像和范围，然后开始日志：

	```bash
	python3 <skill-dir>/scripts/review_steps.py start --index run/index.json --profile run/reader-profile.json --scope run/scope.json --out run/journal
	```

3. 依次记录 reading 和 checks 两个阶段。checks 阶段只放编辑自己的检查和 checkpoint 计划。装了 spaCy 时可以先运行 `difficulty_features.py nominate`（见上一节），把排名靠前的块作为 `wording_check` 的待查段落：

	```bash
	python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage reading --notes run/reading.json
	python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage checks --notes run/checks.json
	```

4. 记录 checks 之后再派发读者。读者只收到 `assets/prompts/` 里的通用提示和 packet。在 Claude Code 上用 `scripts/dispatch_reader.py`，它调用禁用工具、MCP 和设置的 `claude -p`（在服务器上加 `--runtime http --config`，见下一节），每次调用在 `--events` 目录下保存 `<label>.prompt.txt`、`.cli.json`、`.response.raw`、`.response.json`、`.provenance.json`，最后写 `summary.json`：

	```bash
	python3 <skill-dir>/scripts/dispatch_reader.py prefix --events run/events --packet run/checkpoint-01.json --label P01
	python3 <skill-dir>/scripts/extract_pdf.py case --index run/index.json --pages run/manuscript.md.pages.json --budget 250 --asset-policy strict-prefix --assets run/assets.json --out run/case.json
	python3 <skill-dir>/scripts/dispatch_reader.py masked --events run/events --run run/reader --case run/case.json --profile run/profile.json --max-events 40
	python3 <skill-dir>/scripts/dispatch_reader.py branch --events run/events --run run/reader --from-sequence 3 --question run/q1.json
	```

	`--model` 和 `--effort` 原样传给 `claude`。笔记超出预算或返回内容不是可解析 JSON 时，原文件保留，写 `<label>.invalid-reason.txt`，再用同一 packet 加一句说明（限额、计数方法、目标为限额的 85%）重发，标为 b、c；两次更正后仍不通过就停止。遮罩循环在连续三次查阅没有带来新来源时停止。不用脚本时，前缀读者用 `review_packets.py packet` 和 `check-observation`，遮罩读者用 `masked_reader.py start`、`packet`、`record`，追问用 `branch` 和 `branch-record`。

5. 每条带替换句的修改先做独立保真审计，输入是 `candidate_id`、`original`、`proposed_wording`、`basis`。装了 spaCy 时可以用同一个输入文件运行 `difficulty_features.py compare` 作为提醒性检查（见上一节）：

	```bash
	python3 <skill-dir>/scripts/dispatch_reader.py audit --events run/events --input run/C04.input.json --out run/C04.audit.json
	tools/nlp-env/bin/python <skill-dir>/scripts/difficulty_features.py compare --input run/C04.input.json --out run/C04.features.json
	```

	然后记录 reconcile、decisions、repairs 三个阶段，再检查就绪：

	```bash
	python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage reconcile --notes run/reconcile.json
	python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage decisions --notes run/decisions.json
	python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage repairs --notes run/repairs.json
	python3 <skill-dir>/scripts/review_steps.py ready --journal run/journal --index run/index.json
	```

6. 从记录写出结构化终审，校验后再写用户可见的报告：

	```bash
	python3 <skill-dir>/scripts/review_packets.py check-review --index run/index.json --review run/review.json
	python3 <skill-dir>/scripts/review_steps.py check-final --journal run/journal --index run/index.json --review run/review.json
	```

助手只检查文件、顺序、覆盖和引文。它不能证明审稿质量、科学正确性或人类读者的理解。

## 服务器运行时（OpenAI 兼容接口）

`dispatch_reader.py` 的四个子命令都接受 `--runtime http --config llm.json`，把同一份通用提示和 packet 发到一个 OpenAI 兼容的 `POST /v1/chat/completions` 端点，而不是本机的 `claude -p`。文件集（`.prompt.txt`、`.cli.json`、`.response.raw`、`.response.json`、`.provenance.json`、`summary.json`）和更正协议与 claude 运行时相同。配置文件示例：

```json
{
  "base_url": "http://host:8000/v1",
  "model": "name",
  "api_key_env": "LLM_API_KEY",
  "temperature": 0,
  "max_tokens": 4096,
  "json_mode": true,
  "timeout_seconds": 300,
  "extra_headers": {}
}
```

```bash
export LLM_API_KEY=...
python3 <skill-dir>/scripts/dispatch_reader.py prefix --runtime http --config run/llm.json --events run/events --packet run/checkpoint-01.json --label P01
python3 <skill-dir>/scripts/dispatch_reader.py audit --runtime http --config run/llm.json --events run/events --input run/C04.input.json --out run/C04.audit.json
```

- 密钥只从 `api_key_env` 指定的环境变量读取，不写在文件或命令行里；变量未设置时以退出码 2 结束并说明变量名。任何输出文件都不含密钥，`provenance.json` 只记录变量名。文件里出现 `api_key`，或 `extra_headers` 里出现 `Authorization`，都会被拒绝。没有 `api_key_env` 时不发 Authorization 头（无鉴权的内网服务）。
- 请求：system 消息与 claude 运行时相同，user 消息是通用提示加 packet；`temperature`（默认 0）和 `max_tokens` 来自配置；`json_mode` 为 true 时带 `response_format: {"type": "json_object"}`，端点不支持时设为 false，返回的代码围栏会被剥掉。`--model` 覆盖配置里的 `model`，`--timeout` 覆盖 `timeout_seconds`（默认 300 秒）；`--effort` 不适用。可选键：`retry_backoff_seconds`（默认 1）、`price_per_token`、`ca_file`（自签证书的 CA 文件）、`json_schema`（默认 false，见下一节）。字符串里的原始制表符等控制字符按普通字符解析（`json.loads(strict=False)`）。
- HTTP 429、5xx 和连接失败在同一次调用内重试，最多三次，等待 1 秒、2 秒（`Retry-After` 优先，上限 60 秒）；每次尝试的状态码、错误和等待时间记录在 `provenance.json` 的 `attempts`。三次都失败，或 4xx，这一次调用算解析失败，进入与 claude 运行时相同的更正协议（b、c）。
- `provenance.json` 写 `runtime: "http"`、`base_url`、`url`、`model`、`api_key_env`、`request`（温度、max_tokens、response_format（JSON Schema 只记名字和 sha256）、超时）、`http_status`、`attempt_count`、`attempts`、`response_id`、`response_model`、`finish_reason`、`usage`（响应的 usage 对象）、`input_tokens`、`output_tokens`；`session_id` 是响应的 id，也写进遮罩回执的 `agent_id`，审计文件的 `auditor.agent_id` 为 `http-` 加 id 的后半段。`cost_usd` 为 null，除非配置给出 `price_per_token: {"input": 每输入 token 美元, "output": 每输出 token 美元}`。
- 重定向一律拒绝（3xx 算这一次调用失败，请求不会带着密钥发到别处）。`base_url` 用 `http://` 且主机不是本机回环时，密钥会明文传输：脚本在 stderr 打一行警告并写进 `provenance.json` 的 `transport_warning`，不阻止；内网之外请用 `https://`。
- 隔离说明是 `bare chat-completions request with no tools; isolation by request content, not by sandbox`：读者和编辑可以是同一个端点，隔离靠请求内容，不靠进程或沙箱。仓库根目录的 `docs/server-deployment.md` 说明这一运行时在服务器部署里的位置。

## 服务器全自动运行

`server/orchestrator.py` 在一台只有 OpenAI 兼容接口的服务器上跑完整个流程：编辑角色由同一类端点的模型按 `server/prompts/` 的八个模板一步步扮演，读者和审计仍由 `dispatch_reader.py --runtime http` 派发，五个阶段的笔记全部经 `review_steps.py` 记录和校验。三个核心脚本只作为库导入，行为不变。

```bash
export LLM_API_KEY=...
python3 <skill-dir>/server/orchestrator.py run --manuscript run/manuscript.md --out runs/2026-10-27 --config run/llm.json \
    [--reader-config run/llm-reader.json] [--masked-windows 4] [--masked-budget 250] [--prefix-checkpoints 3] [--batch-blocks 20] \
    [--task review|quick_review] [--profile reader-profile.json] [--masked-profile masked-profile.json] [--extraction-notes notes.txt] [--language English]
```

从 PDF 开始的整篇审稿用 `server/review-paper.sh --config run/llm.json paper.pdf [--pages 1-10] [-- 上面这些 run 参数]`：先启动 MinerU 的本地服务（已在运行时什么也不做），再依次跑 `extract_pdf.py extract`、`postprocess` 和 `orchestrator.py run`。它在后台运行，SSH 断开不影响；每次运行一个目录 `$REVIEW_RUNS/<日期-时间>-<文件名>/`（默认 `~/review-runs`），里面有 `pipeline.log`、`extract/`、`manuscript.md` 和 `review/`。MinerU 可执行文件取 `$MINERU`，没有时用 PATH 上的 `mineru`。

`--config` 的格式与上一节相同（编辑调用用它）；`--reader-config` 给读者和审计另一个端点或模型，不给时同 `--config`。`--out` 必须是不存在的目录。执行顺序：

1. `index`，写 `index.json` 和读者画像；scope 模板让模型分配每个块（纳入或给出理由省略），经 `validate_scope` 后 `start` 建日志。
2. reading、checks 按 `--batch-blocks` 个块分批调用模板，每批只看本批全文加其余块的一行摘要（checks 还看全部段落的 paraphrase 和前几批已记录的概念、候选、计划），控制器合并数组、重编号（K01、P01、C01）后交 `record`；每批先用 `review_steps` 的校验函数预检，覆盖和锚点错误按批回传。
3. `02-checks.receipt.json` 存在后才派发读者：按 checks 的 `checkpoint_plan` 建前缀包并跑 `prefix`（模式 `bounded-request`），把纳入块按 `--masked-budget` 词分窗、取前 `--masked-windows` 窗跑一次 `masked`（`--masked-windows 0` 关闭），checks 阶段可以给出一个 `branch_question`，遮罩跑完后在对应显示事件上跑一次 `branch`。
4. reconcile 模板看冻结的观察（前缀观察原文、遮罩每个事件的材料与回答、分支回答），模型只填 `result`、`limitation`、分类和新候选；路径、模式、plan_id、figure_policy 由控制器绑定。decisions、repairs 同样记录。
5. 每条带 `proposed_wording` 的修改先经 `dispatch_reader.py audit`；verdict 不是 `preserved` 或任一属性 `changed` 时，把审计意见作为 `audit_feedback` 回传 repairs 模板重写一次，再审一次（文件名加 `-2`），仍不通过就转成向作者的提问（`kind: author_question`，`proposed_wording: null`）。
6. `ready` 后 review 模板只给出总述和问题顺序，`review.json` 的每个字段由记录拷贝，过 `check-review` 和 `check-final`；report 模板按 `assets/review-report.md` 写 `report.md`；最后写 `summary.json`（各阶段状态、每次调用的秒数与用量、读者调用按类型计数、审计结果、重试次数、锚点规范化次数）。

助手拒绝某一步的回答时，同一步带着助手的原话重新提问（"Correction note from the controller: … The helper rejected it: …"），最多三次，仍失败就停下：日志保持原样，`summary.json` 的 `failure` 写明阶段、最后一条错误和该查看的文件，退出码 1。reconcile、decisions、repairs 三个模板只在 checks 回执存在后运行，代码在派发读者和这三步之前都断言这个文件。

回答交给助手之前，控制器做两处修整，校验规则不变。重提的回答缺了顶层字段时，从上一次回答带过来，同名字段以新回答为准，`log.jsonl` 记 `fields_carried`。带 `block_id` 和 `quote` 的对象里，`quote` 不是块原文子串时，两边都把弯引号、弯撇号、各种连字符（U+2010–U+2012）、en dash、em dash、减号折成 ASCII，逐字符做 NFKC 并去掉空白后再找：恰好一处就换成块原文的那一段，零处或多处就原样交给助手拒绝。每次替换记一条 `anchor_canonicalized`，`summary.json` 的 `totals.anchors_canonicalized` 计数，`editor/<label>.response.json` 仍是模型原话。`--batch-blocks` 默认 20：40 块一批时 Qwen3.8-Flash-Next 的 checks 输出曾陷入重复，直到写满上下文。

配置里 `json_schema` 为 true 时，每一步编辑调用带 `response_format: {"type": "json_schema", ...}`，Schema 由 `server/schemas.py` 按这一步的模板示例和校验规则生成：必填字段按示例顺序，封闭词表（类别、严重度、分类、状态）用枚举，`block_id` 和候选 id 只能取本步允许的值，reading、checks 一批的条数、decisions、repairs、review 的条数固定为应有的数。读者和审计调用不带 Schema，仍按 `json_mode`。Schema 存为 `editor/<label>.schema.json`。端点要支持 `json_schema`（vLLM 用 xgrammar 后端测试过），并且服务器要禁止 JSON 里的自由空白（vLLM：`--structured-outputs-config '{"backend": "xgrammar", "disable_any_whitespace": true}'`，这个选项只在服务器级生效）。原因：模型在点评里引用原文时写了不转义的直引号，字符串提前结束；只有 JSON 模式时解码器随后关掉对象，后面的段落全部丢失，或一直输出空白直到上限；有 Schema、没有自由空白时，唯一能接着写的是下一个必填字段，回答保持完整，只是那一句点评被截短。比对引文时，行内公式 `$...$` 整体算一个单位：模型在 JSON 里写 LaTeX 常少一个反斜杠（`\ldots` 被丢掉，`\therefore` 被读成制表符），公式外的文字唯一对上就换成原文。report 的回答必须提到 review 里的每个问题编号，漏了就重提。

目录 `--out` 下：`editor/` 每次编辑调用的 `<label>.prompt.txt`、`.schema.json`（开启 `json_schema` 时）、`.cli.json`、`.response.raw`、`.response.json`、`.provenance.json`（label 如 `01-reading-batch01`、重试加 `-b`、`-c`、`-d`）；`readers/<P01>/`、`masked/`、`audits/` 下是 `dispatch_reader.py` 的文件集；`journal/` 是回执链；`scope.json`、`reading.json` 等是记录时的笔记副本；`log.jsonl` 按时间记录每个事件（`checks_frozen` 一定在第一个 `reader_dispatch` 之前）。密钥只从 `api_key_env` 指定的环境变量读，任何文件不含密钥。

`server/prompts/` 的模板用 `{{section:references/staged-review.md|## 2. Save reading notes}}` 和 `{{file:assets/notes/reading.json}}` 在渲染时把 references 的对应小节和笔记模板原文装进去，所以模板只写该步需要的规则，规则文件改了提示自动跟上；每次调用实际发出的全文在 `editor/<label>.prompt.txt`。

假端点测试（`tests/test_orchestrator.py`）只能证明流程、顺序、合并、重试和审计门槛按设计运行；真实模型的 JSON 合规、长稿件的上下文长度、以及编辑判断的质量都要在服务器上用真实端点验证（`docs/server-deployment.md` 实施顺序第 3 步）。

## 运行测试

从包目录运行：

```bash
python3 -m unittest discover -s tests
```

预期输出以 `Ran 152 tests` 开头并以 `OK` 结束（没有 spaCy 时为 `OK (skipped=1)`，跳过的是 `difficulty_features.py` 的集成用例；用 `tools/nlp-env/bin/python -m unittest discover -s tests` 可以把它一起跑）。测试不调用 MinerU，也不调用真实模型：`claude` 由临时目录里的假程序代替，http 运行时由线程里的标准库 `http.server` 假端点代替，编排器测试里的假端点按提示内容同时扮演编辑、读者和审计。

## 兼容性

- `scripts/masked_reader.py` 只读写运行格式 5。格式 4 的运行用 0.9.0 的脚本重放，格式 2 用 `scripts/legacy/masked_reader_v2.py`。
- `scripts/review_steps.py` 只读 `schema_version` 3 的日志。
- 每条带替换句的修改都要先通过独立保真审计（`assets/prompts/fidelity-audit.txt`），审计 JSON 的路径写在 repairs 笔记的 `audit_path` 里。
- `SHA256SUMS` 覆盖包内除它自身以外的每个文件。在包目录运行 `shasum -a 256 -c SHA256SUMS` 可核对。

## 目录

- `SKILL.md`：主流程。
- `references/`：分阶段记录协议、遮罩阅读协议、执行流程、审稿规则、用词规则、输出契约、试验摘要、文献依据、插件化构想。
- `assets/`：两种读者画像、阶段笔记模板、报告模板，`prompts/` 下四个通用提示（前缀、遮罩、分支、保真审计）。
- `scripts/`：五个标准库 Python 助手（`review_packets.py`、`review_steps.py`、`masked_reader.py`、`extract_pdf.py`、`dispatch_reader.py`）、需要 spaCy 的可选助手 `difficulty_features.py`，和 `legacy/` 下的旧控制器。
- `server/`：服务器全自动运行的编排器 `orchestrator.py`、编辑端 HTTP 客户端 `llm.py`、每步回答的 JSON Schema `schemas.py`，`prompts/` 下八个编辑步骤模板。
- `tests/`：七个 unittest 文件。
- `VERSION`、`CHANGELOG.md`：版本与变更。

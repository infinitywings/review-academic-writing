# review-academic-writing

这个 skill 审查学术稿件和研究计划：概念是否在首次关键使用前得到解释，段落之间的论证关系是否明确，用词句式是否清楚，建议的修改是否保持科学含义。审稿人把每一步工作存成带回执链的记录，再从记录写报告。可选的遮罩阅读让新上下文的读者逐窗阅读，只携带有限笔记，观察结果作为证据挂进记录。

## 安装

这个目录既是 Claude Code 的 skill，也是仓库根 `.claude-plugin/` 清单所指的插件内容；Codex 通过仓库根的 `.agents/skills/review-academic-writing`（指向本目录的符号链接）发现它。三种装法任选。

### Claude Code 和 Claude Desktop（Code 标签）

在会话里先登记 marketplace（名字是 `infinitywings`），再安装插件：

```
/plugin marketplace add infinitywings/review-academic-writing
/plugin install review-academic-writing@infinitywings
```

Claude Code 2.1.275 以上可以一步完成：`/plugin install review-academic-writing --marketplace infinitywings/review-academic-writing`。命令行等价于 `claude plugin marketplace add infinitywings/review-academic-writing` 加 `claude plugin install review-academic-writing@infinitywings`。装好后 skill 名为 `review-academic-writing:review-academic-writing`；SKILL.md 里的 `${CLAUDE_SKILL_DIR}` 由 Claude Code 替换成本目录的实际路径。

本地改动后不经 marketplace 直接试：在仓库根运行 `claude --plugin-dir .` 启动会话，`claude plugin validate .` 核对两份清单，`claude --plugin-dir . plugin details review-academic-writing` 应列出一个 skill。

### OpenAI Codex

Codex 扫描当前目录到仓库根每一级的 `.agents/skills/` 和用户目录的 `~/.agents/skills/`，并跟随符号链接。在本仓库里打开 Codex 就能用 `$review-academic-writing`；要装到用户目录，把本目录复制或链接到 `~/.agents/skills/review-academic-writing`，或在 Codex 里用 `$skill-installer` 从本仓库安装。`agents/openai.yaml` 给 Codex 和 ChatGPT 宿主提供显示名和调用策略，Claude Code 忽略它。Codex 不替换 `${CLAUDE_SKILL_DIR}`，SKILL.md 里有一句话让模型用包含 SKILL.md 的目录代替。Windows 上没有符号链接支持的检出会把 `.agents/skills/review-academic-writing` 变成一个文本文件，这时用复制安装。

### 手动复制

```bash
cp -R skills/review-academic-writing ~/.claude/skills/review-academic-writing
```

Claude Code 读取 `SKILL.md` 并按需读取 `references/`，`${CLAUDE_SKILL_DIR}` 同样会被替换。

## PDF 抽取环境（MinerU 4 本地模式）

稿件是 PDF 时，第 1 步用 `scripts/extract_pdf.py`，它调用本地 MinerU。2026-10-20 的对比里 MinerU 4.0.12 standard 层级在双栏、公式、表格上最好（见项目目录 `runs/2026-10-20/pdf2md-comparison.md`）。MinerU 不是语言模型：standard 层级是一组约 850 MB 的版面、公式、表格和 OCR 模型，在 M4 MacBook Air（32 GB）上用 CPU 解析一篇 12 页双栏论文约两分钟。编辑和读者角色仍由订阅的 Claude Code 或 Codex 担任（见"读者运行时"）。

MinerU 用 `uv tool` 装成独立环境，可执行文件进 `~/.local/bin`，不进 skill 目录，也不放在项目目录里（项目目录清理时容易一起删掉）：

```bash
uv tool install "mineru==4.0.12" --python 3.12
mineru-kit models download --tier standard
mineru telemetry disable
mineru server start
mineru config set parse_server.local.mode managed
mineru server restart
mineru server status --json
```

模型约 850 MB（standard 层级），下载到 `~/.mineru/models`；已有模型时跳过下载。`extract_pdf.py` 用 `mineru server status --json` 判断就绪：`running` 为 true，`parse_server.local.healthy` 为 true、`starting` 为 false、`mode` 不是 off。首次加载约一分钟；managed 模式下解析服务常驻，之后每篇只付解析时间。已解析过的同一文件（按内容哈希）会直接返回缓存结果。未发表稿件不要用 `--remote`（官方云服务）；`extract_pdf.py` 拒绝这个选项。注意 MinerU 服务会定期探测官方远程端点是否可达，这只是连通性探测，不发送文件。安装包名与附加依赖以 MinerU 官方安装说明为准，上面的版本号是本次实测的版本。

MinerU 不是必需的。找不到 `mineru` 可执行文件、无法运行，或解析服务没就绪时，`extract` 默认停下并打印上面的启动命令（退出码 2）。要在没有 MinerU 的机器上继续，显式加 `--fallback pdftotext`（poppler，`brew install poppler`，可用 `--pdftotext` 指定路径）或 `--fallback pdfplumber`（`pip install pdfplumber`）：得到的是纯文本稿件，双栏顺序、上下标、公式、表格、图注可能丢失或并入，没有 OCR（没有文字层的扫描件会被拒绝）。`extract.json` 记 `fidelity: low` 和降级原因；稿件首行的 `<!-- extraction: ... -->` 注释由 `postprocess` 移到抽取说明开头，所以 scope 记录的 `extraction_notes` 会写明这是低保真抽取。对这种稿件，凡是用于结论的段落都要回到 PDF 核对。MinerU 就绪时 `--fallback` 被忽略。

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

	MinerU 不可用而又要继续时，第一条命令加 `--fallback pdftotext`（或 `pdfplumber`），见上面的降级说明；插件安装后 `<skill-dir>` 就是 SKILL.md 里 `${CLAUDE_SKILL_DIR}` 展开的目录。

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

4. 记录 checks 之后再派发读者。读者只收到 `assets/prompts/` 里的通用提示和 packet。在 Claude Code 上用 `scripts/dispatch_reader.py`，它调用禁用工具、MCP 和设置的 `claude -p`（用 Codex 订阅时加 `--runtime codex`，在服务器上加 `--runtime http --config`，见后面两节），每次调用在 `--events` 目录下保存 `<label>.prompt.txt`、`.cli.json`、`.response.raw`、`.response.json`、`.provenance.json`，最后写 `summary.json`：

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
- 请求：system 消息与 claude 运行时相同，user 消息是通用提示加 packet；`temperature`（默认 0）和 `max_tokens` 来自配置；`json_mode` 为 true 时带 `response_format: {"type": "json_object"}`，端点不支持时设为 false，返回的代码围栏会被剥掉。`--model` 覆盖配置里的 `model`，`--timeout` 覆盖 `timeout_seconds`（默认 300 秒）；`--effort` 不适用。可选键：`retry_backoff_seconds`（默认 1）、`price_per_token`、`ca_file`（自签证书的 CA 文件）。
- HTTP 429、5xx 和连接失败在同一次调用内重试，最多三次，等待 1 秒、2 秒（`Retry-After` 优先，上限 60 秒）；每次尝试的状态码、错误和等待时间记录在 `provenance.json` 的 `attempts`。三次都失败，或 4xx，这一次调用算解析失败，进入与 claude 运行时相同的更正协议（b、c）。
- `provenance.json` 写 `runtime: "http"`、`base_url`、`url`、`model`、`api_key_env`、`request`（温度、max_tokens、response_format、超时）、`http_status`、`attempt_count`、`attempts`、`response_id`、`response_model`、`finish_reason`、`usage`（响应的 usage 对象）、`input_tokens`、`output_tokens`；`session_id` 是响应的 id，也写进遮罩回执的 `agent_id`，审计文件的 `auditor.agent_id` 为 `http-` 加 id 的后半段。`cost_usd` 为 null，除非配置给出 `price_per_token: {"input": 每输入 token 美元, "output": 每输出 token 美元}`。
- 重定向一律拒绝（3xx 算这一次调用失败，请求不会带着密钥发到别处）。`base_url` 用 `http://` 且主机不是本机回环时，密钥会明文传输：脚本在 stderr 打一行警告并写进 `provenance.json` 的 `transport_warning`，不阻止；内网之外请用 `https://`。
- 隔离说明是 `bare chat-completions request with no tools; isolation by request content, not by sandbox`：读者和编辑可以是同一个端点，隔离靠请求内容，不靠进程或沙箱。仓库根目录的 `docs/server-deployment.md` 说明这一运行时在服务器部署里的位置。

## 读者运行时（Codex 订阅）

`dispatch_reader.py` 的四个子命令都接受 `--runtime codex`，用本机的 `codex exec` 派发同一份通用提示和 packet，走 ChatGPT 账号登录的 Codex 订阅，不需要 API 密钥。文件集和更正协议与 claude 运行时相同。

```bash
python3 <skill-dir>/scripts/dispatch_reader.py audit --runtime codex --effort low --events run/events --input run/C04.input.json --out run/C04.audit.json
python3 <skill-dir>/scripts/dispatch_reader.py masked --runtime codex --run run/masked --case run/case.json --profile <skill-dir>/assets/masked-reader-profile.json
```

- 命令是 `codex exec --ignore-user-config --ephemeral --sandbox read-only --skip-git-repo-check --json -`：忽略 `~/.codex/config.toml`（MCP 服务器、插件、技能都不加载，2026-10-26 实测上下文从 24k 降到 18k token），不保存线程，提示从 stdin 读入。`--model` 透传为 `--model`，`--effort` 设为 `model_reasoning_effort`；`--codex` 指定可执行文件（默认 `$CODEX_BIN`，再查 PATH）；`--timeout` 默认 900 秒。
- Codex 没有 system prompt 开关，角色句（与 claude 运行时的 `--system-prompt` 相同）前置在 stdin 提示的开头，隔一个空行；`provenance.json` 的 `system_prompt` 和 `system_prompt_delivery` 记录这一点。`<label>.prompt.txt` 仍只含通用提示加 packet。
- `codex exec` 的 shell 工具无法关闭，只能限制为只读沙箱。脚本解析 `--json` 事件流：出现任何 `command_execution` 事件，这一次调用按"被拒绝"处理，不进入更正协议，退出码非零，事件记录在 `provenance.json` 的 `command_executions`。隔离说明写明这一点：读者不得查阅任何东西，这里靠事后核对事件流，不靠沙箱禁止。
- `provenance.json` 写 `runtime: "codex"`、`command`、`thread_id`（也作 `session_id`，遮罩回执的 `agent_id` 为 `codex-exec-` 加前八位）、`usage`、`input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_output_tokens`、`event_errors`；`cost_usd` 为 null（订阅计费）。`.cli.json` 保存完整事件流、无法解析的行和 stderr 尾部。
- 订阅有速率上限：一篇稿件的遮罩阅读会发几十次调用，额度紧时分批跑，`masked` 的 `--max-events` 控制每次最多调用数。

## 服务器全自动运行

`server/orchestrator.py` 在一台只有 OpenAI 兼容接口的服务器上跑完整个流程：编辑角色由同一类端点的模型按 `server/prompts/` 的八个模板一步步扮演，读者和审计仍由 `dispatch_reader.py --runtime http` 派发，五个阶段的笔记全部经 `review_steps.py` 记录和校验。三个核心脚本只作为库导入，行为不变。

```bash
export LLM_API_KEY=...
python3 <skill-dir>/server/orchestrator.py run --manuscript run/manuscript.md --out runs/2026-10-27 --config run/llm.json \
    [--reader-config run/llm-reader.json] [--masked-windows 4] [--masked-budget 250] [--prefix-checkpoints 3] [--batch-blocks 40] \
    [--task review|quick_review] [--profile reader-profile.json] [--masked-profile masked-profile.json] [--extraction-notes notes.txt] [--language English]
```

`--config` 的格式与上一节相同（编辑调用用它）；`--reader-config` 给读者和审计另一个端点或模型，不给时同 `--config`。`--out` 必须是不存在的目录。执行顺序：

1. `index`，写 `index.json` 和读者画像；scope 模板让模型分配每个块（纳入或给出理由省略），经 `validate_scope` 后 `start` 建日志。
2. reading、checks 按 `--batch-blocks` 个块分批调用模板，每批只看本批全文加其余块的一行摘要（checks 还看全部段落的 paraphrase 和前几批已记录的概念、候选、计划），控制器合并数组、重编号（K01、P01、C01）后交 `record`；每批先用 `review_steps` 的校验函数预检，覆盖和锚点错误按批回传。
3. `02-checks.receipt.json` 存在后才派发读者：按 checks 的 `checkpoint_plan` 建前缀包并跑 `prefix`（模式 `bounded-request`），把纳入块按 `--masked-budget` 词分窗、取前 `--masked-windows` 窗跑一次 `masked`（`--masked-windows 0` 关闭），checks 阶段可以给出一个 `branch_question`，遮罩跑完后在对应显示事件上跑一次 `branch`。
4. reconcile 模板看冻结的观察（前缀观察原文、遮罩每个事件的材料与回答、分支回答），模型只填 `result`、`limitation`、分类和新候选；路径、模式、plan_id、figure_policy 由控制器绑定。decisions、repairs 同样记录。
5. 每条带 `proposed_wording` 的修改先经 `dispatch_reader.py audit`；verdict 不是 `preserved` 或任一属性 `changed` 时，把审计意见作为 `audit_feedback` 回传 repairs 模板重写一次，再审一次（文件名加 `-2`），仍不通过就转成向作者的提问（`kind: author_question`，`proposed_wording: null`）。
6. `ready` 后 review 模板只给出总述和问题顺序，`review.json` 的每个字段由记录拷贝，过 `check-review` 和 `check-final`；report 模板按 `assets/review-report.md` 写 `report.md`；最后写 `summary.json`（各阶段状态、每次调用的秒数与用量、读者调用按类型计数、审计结果、重试次数）。

助手拒绝某一步的回答时，同一步带着助手的原话重新提问（"Correction note from the controller: … The helper rejected it: …"），最多三次，仍失败就停下：日志保持原样，`summary.json` 的 `failure` 写明阶段、最后一条错误和该查看的文件，退出码 1。reconcile、decisions、repairs 三个模板只在 checks 回执存在后运行，代码在派发读者和这三步之前都断言这个文件。

目录 `--out` 下：`editor/` 每次编辑调用的 `<label>.prompt.txt`、`.cli.json`、`.response.raw`、`.response.json`、`.provenance.json`（label 如 `01-reading-batch01`、重试加 `-b`、`-c`、`-d`）；`readers/<P01>/`、`masked/`、`audits/` 下是 `dispatch_reader.py` 的文件集；`journal/` 是回执链；`scope.json`、`reading.json` 等是记录时的笔记副本；`log.jsonl` 按时间记录每个事件（`checks_frozen` 一定在第一个 `reader_dispatch` 之前）。密钥只从 `api_key_env` 指定的环境变量读，任何文件不含密钥。

`server/prompts/` 的模板用 `{{section:references/staged-review.md|## 2. Save reading notes}}` 和 `{{file:assets/notes/reading.json}}` 在渲染时把 references 的对应小节和笔记模板原文装进去，所以模板只写该步需要的规则，规则文件改了提示自动跟上；每次调用实际发出的全文在 `editor/<label>.prompt.txt`。

假端点测试（`tests/test_orchestrator.py`）只能证明流程、顺序、合并、重试和审计门槛按设计运行；真实模型的 JSON 合规、长稿件的上下文长度、以及编辑判断的质量都要在服务器上用真实端点验证（`docs/server-deployment.md` 实施顺序第 3 步）。

## 运行测试

从包目录运行：

```bash
python3 -m unittest discover -s tests
```

预期输出以 `Ran 156 tests` 开头并以 `OK` 结束（没有 spaCy 时为 `OK (skipped=1)`，跳过的是 `difficulty_features.py` 的集成用例；用 `tools/nlp-env/bin/python -m unittest discover -s tests` 可以把它一起跑）。测试不调用 MinerU，也不调用真实模型：`claude` 由临时目录里的假程序代替，http 运行时由线程里的标准库 `http.server` 假端点代替，编排器测试里的假端点按提示内容同时扮演编辑、读者和审计。

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
- `server/`：服务器全自动运行的编排器 `orchestrator.py`、编辑端 HTTP 客户端 `llm.py`，`prompts/` 下八个编辑步骤模板。
- `tests/`：七个 unittest 文件。
- `VERSION`、`CHANGELOG.md`：版本与变更。

# 变更说明

## 0.19.2（2026-10-27）

包内 README 改为英文。命令行、文件格式、日志 schema、脚本和测试都不变。

- `README.md`（包内）整篇译成英文：安装、MinerU 本地抽取与降级、spaCy 句法特征、一次审稿的命令顺序、服务器运行时、Codex 运行时、服务器全自动运行、测试、兼容性、目录各节内容与 0.19.1 一致，所有命令行逐条保留。开头加一句指向仓库首页的概览和图。
- 去掉对一个项目私有目录（不在公开仓库里）的引用，改为说明那次 PDF 抽取对比不在本仓库。
- 目录一节更正：测试文件是九个（之前写八个）；`difficulty_features.py` 单列为可选助手（词级比较不需要 spaCy）；列出 `assets/excess-vocabulary.json`；注明 CHANGELOG 用中文写。
- 仓库首页 README 已在 960f409 改为英文并加了三张图（`docs/figures/`），不在包内。
- 测试数不变，仍是 268 个。

## 0.19.1（2026-10-27）

计划第四节 #20：三次完整运行里每次都要重写一遍的 builder 脚本整理成 `scripts/notes_build.py`。标准库，不调用模型；命令行、运行格式和日志 schema 不变。

- `notes_build.py reading|checks|reconcile|decisions|repairs|review --journal <日志> --index <index.json> --source <片段>… --out <文件> [--audits <目录>] [--compare <目录>]`：从片段生成六个记录。片段是一个 JSON 对象文件、定义 `FRAGMENT` 或 `build(ctx)` 的 Python 模块，或装着它们的目录（按文件名顺序读）；`--source` 可重复，按顺序合并——`paragraphs`、`passages`、`decisions`、`repairs` 按块 id 或候选 id 合并（同一 id 出现两次拒绝），`concepts`、`checkpoint_plan`、`candidates`、`no_candidate`、`checkpoints`、`branches`、`observations` 追加，`summary` 等标量后者覆盖。模块里 `ctx.q(block_id, quote)` 返回锚点并记下不精确的引文，`ctx.text`、`ctx.included`、`ctx.opening(block_id, n)` 可用。
- 内置检查，写文件之前全部报告：输出里任何 `{block_id, quote}` 对象的引文按 index.json 逐字校验（列出路径和引文）；reading 和 checks 必须恰好覆盖每个纳入块（列出缺的和不在范围的 id）；记录再过一遍 `review_steps.validate_notes`（和 `record` 同一套：字段词表、候选与阅读问题账目、审计绑定），review 过 `check_review`；needs_author 的候选自动成为 `author_question` 且问句必须以问号结尾；edit 没给措辞时由 `--audits` 下该候选的审计决定——按 `audit_round` 取最新一轮，preserved 且无 changed 才把它的 `proposed_wording` 和路径写进修改，否则 `proposed_wording` 为 null、`wording_withheld_reason` 写明轮次、文件、结论和改变的属性（早先某轮 preserved 不能复活被后一轮替换的措辞）；给了措辞没给 `audit_path` 时绑定审计过同一措辞的那份，否则拒绝；`<cid>.features.json` 自动成为 `compare_path`。结果里 `audit_selection` 列出每条的选择。
- 各阶段的填充：reading 缺 `anchor` 用块开头；checks 的 passage 没引自己块的 evidence 时补块开头，concepts、plan、candidates 缺 id 时按 `K01`、`P01`、`C01` 编号；reconcile 的前缀检查点可写成 `{id, plan_id, events_dir, limitation}`，由 `dispatch_reader.py` 的 `<label>.packet.json`、`<label>.observation.json` 展开（锚点和模式取自计划项，`result` 按 issue 数、`cannot_place` 数和 understanding 概括），观察按 `O01` 编号，新候选 id 不得重复 checks 的；decisions 每个候选恰好一条，accepted/needs_author 必须有 category、diagnosis、severity、confidence；review 的 issues 按候选顺序从已记录的 decisions 和 repairs 生成（证据合并候选与决定的锚点并去重，needs_author 带 `author_question`），概念表来自已记录的 concepts，片段只需给 `summary` 和 `coverage` 之类的额外顶层字段。
- 日志门槛：生成某阶段要求之前的阶段都已记录、该阶段尚未记录；review 要求五个阶段齐全。输出路径已存在时拒绝（`output_exists`）。
- 文档：`references/workflow.md` 新增"Building the stage notes with notes_build.py"一节；SKILL.md 第 7 节和包 README 提到该脚本。
- 测试：新增 `tests/test_notes_build.py` 14 个，共 268 个；用编造的句子。

## 0.19.0（2026-10-27）

读者设计的三项（计划第三节 #12–#14）和三次完整运行记下的运行层待改项（第四节 #15–#19）。依据是 KnowledgeGain（预印本，有受控人类实验）：模拟读者只凭自己的笔记作答并给"不知道"选项时，答题分布更接近真人；本版把这个记忆瓶颈做成可记录的 recall 事件，并给前缀读者一个"说不出来"的出口。命令行只增不改；运行格式 5 和日志 schema 5 不变（recall 记录是运行目录下的新子目录，主路径从不读取；`recall_questions`、`recall_sequences`、`cannot_place` 都是可选字段）。

- 遮罩阅读的 recall 事件：`dispatch_reader.py masked --recall-every N --recall-questions FILE`（默认 0 关闭；FILE 是题目数组，或 checks 笔记文件，取其 `checkpoint_plan` 里带 `recall_questions` 的 masked-sequential 条目，`--recall-plan-id` 可指定一条）。每记录 N 个窗口（查阅不计），把 `after_block` 已显示且未问过的题连同 carry_memory 发给一个新上下文，包里没有任何窗口文本，也没有答案；运行结束时补问剩下的题。每题 `id`、`after_block`、`question`、`options`（至少两项，恰好一个逐字的 `I don't know`）、`answer`。读者逐题抄一个选项进 `choice` 并列出依据的笔记 id；`masked_reader.py` 新增 `recall`/`recall-record`，把包、回答、带答案的题目和回执写到 `<run>/recalls/NNNNNN/`，回执记每题 `correct`/`wrong`/`idk` 并绑定到它跟随的显示事件回执；`load_recalls` 重放校验，主路径的下一个包有无 recall 都一样。summary 多 `recalls`（每次的 wrong 和 idk 题号）。`assets/prompts/masked.txt` 加 recall 包的作答要求（只凭笔记、不会就选不知道、不从背景知识重建）；`review_steps.py` 在 checks 阶段校验计划项的 `recall_questions`（块在纳入范围、选项与答案的形状），reconcile 阶段的 masked 检查点可带 `recall_sequences` 绑定 recall 记录文件；`references/masked-reading.md` 新增 5b 节写依据和限制（题目是编辑看着全文选的；答错只说明笔记没带上，不说明人会忘；按回执里的 `notes_dropped`、`since`、`sources` 判定后才记 `lost_in_notes`），`staged-review.md` 第 3、4 节和词表写明怎么记。
- 前缀读者的 `cannot_place`：`assets/prompts/prefix.txt`（和 workflow.md 里的同一段）要求读者把读到这里安放不了的名词、缩写和指代单独列出（`anchor` 为可见原文，`note` 一句），不要求成为 issue，宁可说不知道也不要推出答案；`review_packets.py check-observation` 接受该可选字段，校验引文为可见文本，返回 `cannot_place_count`；`output-contract.md` 写明。
- 报告模板的"验证"一节加两条：读者模型及其在本次运行中的提问倾向（为换模型比较而记，不是模型属性）；recall 事件的数量、答错和答不知道的题数及其中成为 `lost_in_notes` 的项。
- `review_steps.py corrected-run --from <journal> --index <index> --out <journal-2> --restart-at <stage> [--reason]`：把源日志的 00-start 和 `--restart-at` 之前各阶段的笔记与回执逐字节复制到新目录（链两端都重放校验），写封印的 `supersedes.json`（被取代的日志、重录起点、复制的回执哈希、源日志已记录的阶段、源是否已 ready、理由，连带更早的 supersedes 链），从该阶段起重新记录；`ready` 回执带 `supersedes`、`restart_at`、`supersedes_sha256`，`load_run` 遇到被改动的 `supersedes.json` 拒绝。`staged-review.md` 的"Correct"一条改为引用该命令，并说明换读者画像、版本或范围仍要 `start` 新运行。之前只能手工开 journal-2。
- `dispatch_reader.py masked --until-complete [--max-restarts N]`（默认 5）：格式、预算或引文更正用尽后的停止在同一进程内自动续跑（重试事件标 `E06r1`、`E06r2`），直到 `Run complete`、`--max-events` 或重启次数用尽；助手直接拒绝的回答不续跑。summary 多 `restarts`，loop 里记每次 restart。运行目录写 `dispatch.pid`（进程号、开始时间、events 目录），另一个活着的进程持有时拒绝启动，陈旧文件覆盖，结束时删除；完成与否看 summary 的 outcome 和 pid 文件是否还在，不再用 `pgrep -f`。
- `extract_pdf.py case --include FILE`：只含纳入块（FILE 是 scope.json、块 id 的 JSON 数组或每行一个 id 的文本），窗口按纳入块划分，资产的 `referenced_by` 去掉被排除的块，`encountered_by` 落到被排除块时改为同页最近的纳入块，同页没有则取后面第一个纳入块；报告多 `excluded_blocks` 和 `references_only_from_excluded_blocks`。
- `case` 把被页断开的段落并入同一窗口：sidecar 里相邻、都是 paragraph、后块页码更大、前块不以句末标点（`.!?:;…。！？` 及其后的引号或括号）结尾的两块不再被窗口边界分开；报告多 `page_joined_pairs` 供编辑核对。
- `postprocess` 的行号栏删除扩展到 1–2 位数字：至少 5 个逐一递增的整数（1–4 位）组成的行整行丢弃（ACL 前几页的 `1 2 3 …`）；三个数的行和非连续的数列仍保留。
- 模板：`assets/notes/checks.json` 的计划项加 `recall_questions`，`reconcile.json` 的 masked 检查点加 `recall_sequences`。SKILL.md、两份 README 和 evidence-and-tools.md 的 KnowledgeGain 一行同步。
- 测试：新加 25 个（dispatch_reader 8、review_steps 5、extract_pdf 7、masked_reader 4、review_packets 1），共 254 个。#12 用假读者跑 3 窗 + 2 次 recall 的端到端用例，#16 用失败三次再成功的假 `claude`。测试里只用编造的句子。

## 0.18.0（2026-10-27）

依据 2026-10-27 的文献梳理把保真审计和改写规则收紧：改写模型把"可能"改成"是"的概率是反向的 1.5–2 倍且多轮累积（Nature Human Behaviour 2026；arXiv 2606.07951），被要求"检查主张"时又会加无依据的限定语（arXiv 2610.11355），自评自改没有外部信号时更差（Huang 等 ICLR 2024；Kamoi 等 TACL 2024），LLM 判官的偏好判断一致性低且受顺序影响（Jourdan 等），通用模型过度编辑（CoEdIT、FineEdit），超额词表只给下界（Kobak 等 Science Advances 2025）。日志 schema 升到 5：新字段对新日志必填，0.17 的 schema 4 日志仍可用本版脚本重放（审计文件可以没有 `hedges`、`added_qualification`、`audit_round`，repairs 可以没有 `edit_scope`，action 和作者问题不查格式）。

- 保真审计逐个 hedge 比对：`dispatch_reader.py audit` 组装输入时从 `original` 抽出限定词和条件短语（may、might、could、suggest、appear to、associated with、under … conditions、in N of M、if、approximately 等，正则列表 `HEDGE_PATTERNS` 放在 `review_packets.py`，`find_hedges` 按首次出现去重；`--hedge-list` 可加本领域的写法），写成 `hedges_in_original` 随输入送审；`assets/prompts/fidelity-audit.txt` 要求审计员对每一项报 kept / strengthened / removed 写进 `checks.hedges`，任一项不是 kept 就把 `claim_strength` 或 `uncertainty` 记为 changed。脚本的形状检查和 `review_steps.py` 的记录校验要求一致：每个由脚本从原文抽出的 hedge 都有报告（自定义模式吞掉内置词时按子串放过）、同一词不重复、不是 kept 的项必须伴随 changed、报 kept 的项必须在替换句里还留有词表里的某个限定词或该词本身（词表里的同义替换算 kept，直接断言不算；词表外的同义词要么改用词表里的写法，要么报 removed）；派发脚本先按同样规则要求审计员更正，记录时不会再因同一原因失败。`--hedge-list` 的模式不得匹配空串、不得带内联标志，装入时与内置列表合编检查。
- 审计第十项 `added_qualification`：改写新增而原文没有的限定语、让步、免责短语，basis 里没有对应块就是 changed；`assets/notes/fidelity-audit.json` 的 `checks` 多这一项和 `hedges` 数组，顶层多 `audit_round`。schema 5 的审计 `checks` 必须正好是十项加 `hedges`，schema 4 的审计可以是原来的九项。
- 多轮措辞每轮对照原文：审计文件记 `audit_round`（`dispatch_reader.py audit --round N`，编排器第二版传 2）；`review_steps.py`（schema 5）要求审计的 `original` 至少一条与候选锚点、候选证据锚点或决定证据锚点同块且引文相互包含，所以第二版、第三版措辞只能对照稿件原文，不能对照上一版（上一版的句子不是稿件文字，锚点校验本来就拒绝；与候选无关的另一段稿件文字现在也拒绝；在术语首次出现处加同位说明的修改引证据锚点即可）。编排器在预检时做同样的锚点检查。
- 改写指令任务化带范围：repairs 的 edit 项必填 `edit_scope`（pointer、apposition、term、reorder、split、sentence 之一；move 和 author_question 不得带）；`references/wording-and-sentences.md` 的"Output exact improvements"和 `staged-review.md` 第 6 节写明生成措辞的指令必须是"只改 X，不改数字、条件、因果词"，不是"润色这段"；服务器 repairs 提示词同步。
- 改动量阈值：`difficulty_features.py compare` 新增不依赖 spaCy 的 `lexical` 一组——改动词数与原句词数之比 `edit_ratio`（difflib 词级对齐，替换段按较长一侧计）、`edit_weight`（0.2 以内 light、0.5 以内 medium、超过 0.5 heavy）、type-token 比、平均词长，替换句词汇更单一或词更长时给 `advisory_flags`；比较结果现在带 `original` 和 `proposed_wording` 原文；`--lexical-only` 只算这些，没有 spaCy 也能运行。repairs 项新增 `compare_path` 和 `heavy_edit_reason`：`edit_scope` 为 `sentence` 且有措辞时必须附 compare 结果，附上的结果 `edit_weight` 为 heavy 时 edit 项必须写 `heavy_edit_reason`，否则拆成多条。改动词数把插入也算在内，所以纯增写（例如给 20 词的句子加 12 词的同位说明）也可能到 heavy，这时理由一句即可。编排器对每条带措辞的修改在 `audits/<id>.features.json` 写词级比较并填 `compare_path`；heavy 又没理由的 edit（包括同时没过审计的）作为 `edit_feedback` 回传重写一次，仍如此就撤回措辞（`wording_withheld_reason` 写明比值），`summary.json` 的 `stages.repairs` 多 `withheld_heavy_edits`。
- 评审与改写分调用：`references/workflow.md` 新增"Diagnosis and wording in separate calls"，规定 decisions、repairs、审计是三次没有共享上下文的调用，不在一次调用里既选优又改写；`fidelity-audit.txt` 首句改为被审文本是"他人提交的改写"，审计员未参与编辑。
- 成对比较换序：`staged-review.md` 第 5 节规定任何"哪版更好"的成对判断两种顺序各跑一次、不一致记 undetermined，第 6 节对两条都通过审计的措辞沿用同一规则，第 7 节和 `assets/review-report.md` 规定报告只写可描述的差异（更短、更早、少一个未定义词、条件已写明），不写"更好"，并在验证一节列出成对判断和审计轮次、hedge 计数、被撤回的措辞。
- 超额词提名：新增 `assets/excess-vocabulary.json`——Kobak 等（Science Advances 2025，DOI 10.1126/sciadv.adt3813）公开的 900 个超额词标注表（MIT 许可，出处、数据文件、检索日期写在文件里）中标为风格词、且 2024 年频率超过 2021–2022 线性外推（差 > 0.01 或比 > 1.5，用作者的逐年计数矩阵算）的 366 个词，按词性分组（动词 245、形容词 51、副词 19、名词 38 等），每个词带 2024 年的频率比和频率差；论文自己报 379 个，边缘有出入，文件里写明。`difficulty_features.py nominate` 对每个块多一列 `excess_vocabulary` 计数（默认读该文件，`--excess-vocabulary` 换文件，`--no-excess-vocabulary` 关闭），不进 z 分数排名；`wording-and-sentences.md` 新增"Excess-vocabulary nomination"一段：只提名不定罪，三条限制（词表是下界、分不清作者风格与机器风格、作者回避致过时），不得把计数当"这句是机器写的"证据，也不把"去 AI 腔"当审稿目标。
- `check-final` 校验每条 issue 的 `action` 非空且含本稿的块 id（块 id 前后紧贴中文也认），`needs_author` 项的 `author_question` 必须以问号结尾（英文或中文问号，后面可跟引号或括号）；repairs 记录时做同样两项校验（schema 5），`references/output-contract.md` 写明。
- `references/evidence-and-tools.md` 新增两个表，按现有"借什么 / 不能支撑什么"格式：已评审条目 18 条（Nature HB 2026、Nature MI 2026、NEJM AI 2024、Findings of ACL 2024 SWIF²T、Findings of EMNLP 2025 ClaimCheck 与 FineEdit、Science Advances 2025 与 2024、Scientometrics 2026、CoEdIT、XtraGPT、Du & Hashimoto 2025、TACL 2024 Kamoi、ICLR 2024 Huang、Findings of EMNLP 2023 Oh & Schuler、CoNLL 2024、PLoS Comput Biol 2024、J Biomed Inform 2024），预印本另列一表标"not peer reviewed"13 条。每条链接 2026-10-27 打开过（三个出版社页面拒绝命令行抓取，用浏览器确认）；J Biomed Inform 一篇用 PMC 链接。TAACO 一行补一句期刊证据。
- SKILL.md 第 5–7 节和两份 README 同步；`difficulty_features.py` 版本 0.13.0。
- 测试：三个已有文件的夹具按新字段更新（审计加 `hedges`、`added_qualification`、`audit_round`，edit 加 `edit_scope`，action 加块 id，schema 5），新加 39 个（review_steps 14、dispatch_reader 11、review_packets 3、difficulty_features 9、orchestrator 2 并扩展端到端用例），共 229 个。测试里只用编造的句子。

## 0.17.0（2026-10-27）

同一稿件、同一技能的两套运行（Claude：11 个短前缀读者加遮罩阅读，侧重首读阻碍和术语；Codex：4 个长前缀读者，侧重主张强度、公式反例和引文核对）出入很大，原因是技能没有规定检查阶段的最低清单、阅读笔记里的问题可以无声丢失、接受项不带措辞没有后果、读者设计没有最低要求、抽取断字没有复原、引文核查没有触发条件。这一版把这六处写进校验或文档。日志 schema 升到 4：checks 笔记多了必填字段，repairs 笔记多了条件必填字段，0.16 的日志用 0.16.3 的脚本重放。

- checks 阶段：每个纳入块的 passage 新增必填 `claim_check`，是三个非空字符串的对象——`claim_strength`（绝对或比较性主张的强度与依据）、`counterexample`（对该块每个公式、指标、判定规则的一次反例尝试）、`citation_check`（承担比较或新颖性的引文是否需要查原文）；没有可查内容时也要写明"本块无"及理由。`record` 和 `check-final`（经 `load_run` 重放各阶段）都校验字段存在且非空。`references/review-rubric.md` 新增 4b 节说明三类检查，`staged-review.md` 第 3 节写入字段要求，服务器 checks 提示词引用该节并要求返回该字段。
- 阅读→检查的账目：`record --stage checks` 校验 reading 笔记里每条 `open_questions` 都被某个候选引用（候选的 `question` 原样包含该问题，或候选可选的 `open_questions` 数组按 `block_id`、`question` 列出），否则必须出现在 checks 笔记新增的必填 `no_candidate` 数组里（`block_id`、`question`、`reason`）。没列、列了阅读没提的问题、同一问题列两次、候选已引用又列进 `no_candidate`，都拒绝。编排器按批把本批块的 open questions 交给编辑并逐批做同样的账目，合并后写进 `no_candidate`。
- repairs 阶段：`kind` 为 `edit` 而 `proposed_wording` 为 null 的修改必须给非空的 `wording_withheld_reason`；带措辞的修改、`move`、`author_question` 不得带这个字段。`ready` 和 `check-final` 重放时同样校验。编排器预检时对待审计的 edit 填临时理由，审计两次不过转为作者问题时清掉该字段，repairs 提示词写明规则。
- `references/workflow.md` 新增"检查点设计的最低要求"：至少一个引言内的短前缀、至少一个节末的长前缀；记录读者模型、effort 和作答语言；写明不同模型的提问倾向不同（一个只问逻辑，一个只问首读阻碍），某个检查点"没提问"只是弱证据；遮罩阅读对约 13,000 词以下的稿件可选。SKILL.md 第 3 节加同样的最低要求。
- `extract_pdf.py postprocess` 新增 `--pdf`（用 `pdftotext` 取文字层，另存为 `<out>.text-layer.txt`）或 `--text-layer`（现成文件）：以文字层的词为词表，只处理不在词表里的 token，且只改成文字层确有的文本——连写拆开（两半都是词且在文字层里相邻）、断开合上（合并后是词）、多余连字符去掉（文字层里有不带连字符的整词）、缺的连字符补回（文字层里另有带连字符的写法）、多了或错了一两个字母的词按前后各两个已知词在文字层里定位改回；行尾连字符两种读法都可能时不动；行内公式、行内代码、URL、LaTeX 命令、链接不碰；表格、公式块、代码块不碰。每处改动写进抽取说明（`ordinal N: 'blie' -> 'lie'`），未能确认的 token 列为 Uncertain，page sidecar 的 sha256 按改后的段落计算。没给文字层时说明里写明未检查断字。`postprocess` 新增 `--pdftotext`、`--timeout`。
- 引文抽查：`workflow.md` 新增"Citation spot-check"一节，定义承担比较或新颖性的引文要核对原文摘要或引言，记录到运行目录的 `citation-checks.json`（字段 `citation`、`claim`、`source_checked`、`verdict`、`note`；模板 `assets/notes/citation-checks.json`）；SKILL.md 规定研究提案默认做，稿件审稿按需做。
- 最终报告：`assets/review-report.md` 的验证一节加候选计数（接受、作者问题、合并、驳回），0 驳回要写一句解释；`staged-review.md` 第 7 节、SKILL.md 第 7 节和服务器 report 提示词同样要求，编排器把 `decision_counts` 交给报告步骤。
- `scripts/setup_mineru.sh`：先 `mineru server start`（服务未运行时）再 `telemetry disable` 和 `config set parse_server.local.mode managed`，改了模式后 `server restart`；之前这两条在服务未启动时都报 `Local mineru server is not running`。`--check` 行为不变。两份 README 的手动命令顺序同步。
- 小修：SKILL.md 的 `extract` 命令写出 `--timeout`（传给 `mineru parse --wait`），`postprocess` 命令带 `--pdf`；`staged-review.md` 末尾新增"Field vocabularies"表，列出 `scope.task`、checkpoint 的 `mode` 和 `figure_policy`、`record_sequence` 的取值范围、`observation.classification`、`decision.status`、`category`、`severity`、`confidence`、`repair.kind`、audit 的 verdict 和 checks 取值；`masked-reading.md` 第 2 节说明 `case` 按纳入块过滤后资产的 `encountered_by` 和 `referenced_by` 必须指向案例里的块。
- 测试：三个已有文件的夹具按新字段更新（checks 的 `claim_check` 与 `no_candidate`、repairs 的 `wording_withheld_reason`、schema 4、安装脚本的新顺序），新加 23 个（claim_check 缺失、缺键、空值与非对象；open question 的覆盖、`no_candidate` 的各种拒绝；edit 无措辞无理由、理由为空、措辞与理由并存；schema 3 拒绝；断字复原的每条规则、保护区、sidecar 哈希、说明文本、命令行；安装脚本先启动服务再改遥测与配置），共 190 个。

## 0.16.3（2026-10-26）

一键安装本地 MinerU，让只有 Claude Code 的使用者把 GitHub 链接交给宿主就能装完整套环境。已有脚本的命令行、文件格式不变。

- 新增 `scripts/setup_mineru.sh`：幂等地检查并完成 MinerU 的安装与启动。步骤：`uv` 缺失时退出码 2 并给提示（不替使用者装 `uv`）；`mineru` 不在 PATH 时 `uv tool install "mineru==4.0.12" --python 3.12`（已装其他版本时只报告，不替换）；`mineru-kit models verify --tier standard` 失败时才下载模型；telemetry 未关时关闭；`parse_server.local.mode` 不是 managed 时设置并标记需要重启；服务未运行时 `server start`，模式改过时 `server restart`；然后轮询 `server status --json` 直到 `running`、`local.healthy`、非 `starting`、`mode` 非 off，默认最多等 180 秒。退出码 0 就绪、1 未就绪、2 缺前提。`--check` 只报告每一步会做什么，不改任何东西；`--version`、`--tier`、`--python`、`--wait` 可改默认值。脚本把 `~/.local/bin` 临时加进 PATH，从不使用 `--remote`。
- `extract_pdf.py`：找不到 MinerU 和解析服务未就绪两处提示都先指向 `scripts/setup_mineru.sh`，手动命令列表保留在后面。
- SKILL.md 第 1 节第 4 步、包 README 的 MinerU 一节和根 README 的安装步骤改为先运行脚本；根 README 加一段可直接贴给 Claude Code 的安装请求（一步 `claude plugin install --marketplace <链接>` 加运行脚本）。
- 测试加 7 个（`tests/test_setup_mineru.py`，用临时目录里的假 `uv`、`mineru`、`mineru-kit` 记录调用并模拟状态：缺 uv 退出 2 且不跑任何命令；全新机器按顺序安装、下载、关遥测、设模式、启动；已就绪时只执行只读命令；改模式后重启而非启动；`--check` 不改状态；`--check` 就绪时退出 0；其他版本只报告不替换），共 167 个。

## 0.16.2（2026-10-26）

在一次完整的七阶段审稿（28 页 ACL 投稿，8 个前缀检查点、29 窗遮罩阅读、45 次保真审计）里发现的四处修补。命令行、文件格式、日志 schema 不变。

- `extract_pdf.py extract` 给 `mineru parse` 传 `--wait <timeout>`：之前 MinerU 的默认等待是 60 秒，28 页的稿件会被当作解析失败报告。等待时长取自 `--timeout`（整数秒）。
- `extract_pdf.py postprocess` 删除被抽成文字的投稿行号栏：连续 5 个以上 3–4 位整数组成的行（ACL/EMNLP 模板的行号，如 `001 002 003 …`）整行丢弃，并在抽取说明里记 `Dropped N line-number gutter line(s)` 和各段范围。之前这些行会被 `index` 当成正文块，使检查点前缀包和遮罩窗口里出现数字串。
- `dispatch_reader.py`：读者回答里的锚点引文与原文不完全一致（`check-observation` 报 `quote is not exact text` / `not an exact substring`，多见于抽取把一个词断成两半的位置）现在按可更正失败处理（anchor 提示，带原回答重提一次），不再直接拒绝整次调用。
- `dispatch_reader.py masked`：一次运行在某个序号被拒绝停止后再续跑，重试的序号标为 `E06r1`（再失败 `E06r1b`…），不再因为 `events/E06.packet.json` 已存在而拒绝续跑。
- 测试加 3 个（行号栏删除与说明；不精确锚点第二次更正通过；续跑使用 r1 标签），`--wait` 由已有的 extract 测试覆盖，共 160 个。

## 0.16.1（2026-10-26）

- `dispatch_reader.py` 的 claude 运行时不给 `--model` 时默认 `claude-opus-5-5`（常量 `CLAUDE_DEFAULT_MODEL`）；`--model` 仍可覆盖。codex 和 http 运行时不变。本机实测 `claude -p --model claude-opus-5-5` 在受限模式下可用。
- 测试加 1 个（默认模型出现在命令行和出处记录），共 157 个。

## 0.16.0（2026-10-26）

打包为插件：同一个 skill 目录可以从 Claude Code、Claude Desktop 的 Code 标签和 OpenAI Codex 直接安装；MinerU 不可用时可以显式降级为纯文本抽取。脚本的命令行只增不改，文件格式不变。

- 仓库布局：包目录从 `review-academic-writing/` 移到 `skills/review-academic-writing/`（git mv，历史保留）。仓库根新增 `.claude-plugin/plugin.json`（name `review-academic-writing`，version 与 VERSION 同步，Apache-2.0）和 `.claude-plugin/marketplace.json`（marketplace 名 `infinitywings`，一个条目，source `./`，即仓库本身就是插件）；`.agents/skills/review-academic-writing` 是指向同一目录的符号链接，供 Codex 在仓库内发现（Codex 跟随符号链接；没有符号链接支持的 Windows 检出会得到一个文本文件，这时改用 `$skill-installer` 或复制）。`agents/openai.yaml` 在包内的位置不变。`claude plugin validate .` 对仓库根通过，`claude --plugin-dir . plugin details review-academic-writing` 列出一个 skill。
- SKILL.md、`references/masked-reading.md`、`references/staged-review.md` 里给宿主模型执行的命令，路径从 `<skill-dir>` 改为 `${CLAUDE_SKILL_DIR}`；Claude Code 加载 SKILL.md 时替换成实际目录。SKILL.md 加一句：在 Codex、手动安装或 references 里未展开时，用包含 SKILL.md 的目录代替。frontmatter 仍只有 name 和 description。
- `extract_pdf.py extract` 新增 `--fallback pdftotext|pdfplumber` 和 `--pdftotext`：找不到 MinerU 可执行文件、无法运行，或 `server status --json` 判断未就绪时，用 poppler 的 pdftotext（按换页符分页）或 pdfplumber 做纯文本抽取，写出带 `<!-- page N of M -->` 的 Markdown，首行 `<!-- extraction: ... -->` 注释写明是低保真抽取（双栏顺序、上下标、公式、表格、图注可能丢失或并入，没有 OCR）；`extract.json` 记 `extractor`、`fidelity: low`、`fidelity_note`、`mineru_unavailable`、`pages`，`tier` 为 null。`postprocess` 把这条注释从稿件里去掉、抄到抽取说明开头（即 scope 的 `extraction_notes`）；`postprocess` 的响应和 MinerU 路径的 `extract.json` 都多 `fidelity` 字段（MinerU 为 `standard`）。默认不降级：行为与之前一致，退出码 2 并打印启动命令，只在末尾多一句提示 `--fallback`。MinerU 就绪时忽略 `--fallback`；没有文字层的 PDF 拒绝写出稿件。找不到 MinerU 的提示改为 `uv tool install` 的安装命令。
- README：安装一节改为三种方式（Claude Code / Desktop 经 marketplace 安装、`claude --plugin-dir` 本地测试、Codex 的 `.agents/skills` 与 `$skill-installer`、手动复制），MinerU 一节补降级说明；根 README 同步。`references/plugin-extension.md` 开头注明插件清单已存在、服务组件仍未实现。
- 测试加 8 个（降级 Markdown 与说明传递 2 个；extract 命令 6 个：无可执行文件时退出 2、服务未就绪走 pdftotext 并经 postprocess、无可执行文件走 pdftotext、假 pdfplumber 模块、MinerU 就绪时忽略降级、无文字拒绝），共 156 个。

### 兼容性

已复制到 `~/.claude/skills/` 的旧副本不受影响；重新复制时源路径改为 `skills/review-academic-writing/`。六个脚本已有的命令行、运行格式、日志 schema 不变。

## 0.15.0（2026-10-26）

本地部署整理：读者派发可走 Codex 订阅，MinerU 就绪判断改读结构化状态。命令行、文件格式和已有运行时的行为不变。

- `dispatch_reader.py` 新增 `--runtime codex`：调用 `codex exec --ignore-user-config --ephemeral --sandbox read-only --skip-git-repo-check --json -`，提示从 stdin 读入，角色句前置在提示开头并写进出处记录（Codex 没有 system prompt 开关）。解析 `--json` 事件流取线程 id、最后一条 agent_message、用量和命令执行记录；出现任何 `command_execution` 事件，这一次调用按"被拒绝"处理，不进入更正协议。`--model` 透传，`--effort` 设为 `model_reasoning_effort`，`--codex` 指定可执行文件，默认超时 900 秒。`--effort` 的拒绝信息改为"claude 和 codex 运行时"。实测：`--ignore-user-config` 把 Codex 的固定上下文从 24k 降到 18k token；一次审计调用 7.4 秒，verdict preserved，事件流无命令执行。
- `extract_pdf.py` 改用 `mineru server status --json` 判断就绪：`running`、`parse_server.local.healthy`、`starting`、`mode`、`managed_running` 五个字段，错误汇总里的历史计数不再影响判断。之前的文本正则在子进程里被表格截断（`runni…`）、又被 `engine_unavailable` 里的 `unavailable` 误判为未就绪。无 `--json` 的旧版 MinerU 退回文本判断。`extract.json` 的 `server_status` 改为本地解析服务的精简字段，不再存整张状态表。
- README：MinerU 改为 `uv tool install` 安装（可执行文件进 `~/.local/bin`，不再放在项目目录 `tools/mineru-env`，那个目录在清理时丢过一次）；说明 MinerU 不是语言模型，编辑和读者仍由订阅的 Claude Code 或 Codex 担任；新增"读者运行时（Codex 订阅）"一节。SKILL.md 和根 README 的派发说明补上 codex 运行时。
- 测试加 8 个（codex 运行时 4 个：审计经 codex、命令执行被拒且不重发、config 与可执行文件校验、事件流解析；MinerU 就绪判断 4 个：JSON 优先、本地服务缺失、文本回退、状态摘要），共 148 个。

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

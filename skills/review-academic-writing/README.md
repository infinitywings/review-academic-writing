# review-academic-writing

This skill reviews academic manuscripts and research proposals: whether concepts are explained before their first critical use, whether the argumentative links between paragraphs are explicit, whether words and sentences are clear, and whether each proposed edit preserves the scientific meaning. The reviewer saves every step as a receipt-chained record and writes the report from those records. Optional masked reading has fresh-context readers read one window at a time, carrying only bounded notes; their observations are attached to the record as evidence.

This file is the command reference. For an overview with figures, see the [repository front page](../../README.md).

## Installation

This folder is both a Claude Code skill and the plugin content that the manifests in the repository root's `.claude-plugin/` point to. Codex discovers it through `.agents/skills/review-academic-writing` in the repository root, a symbolic link to this folder. Use any of the three methods below.

### Claude Code and Claude Desktop (Code tab)

In a session, register the marketplace (named `infinitywings`) and install the plugin:

```
/plugin marketplace add infinitywings/review-academic-writing
/plugin install review-academic-writing@infinitywings
```

Claude Code 2.1.275 and later can do this in one step: `/plugin install review-academic-writing --marketplace infinitywings/review-academic-writing`. The command-line equivalent is `claude plugin marketplace add infinitywings/review-academic-writing` followed by `claude plugin install review-academic-writing@infinitywings`. Once installed, the skill is named `review-academic-writing:review-academic-writing`; Claude Code replaces `${CLAUDE_SKILL_DIR}` in SKILL.md with the actual path of this folder.

To try local changes without the marketplace, run `claude --plugin-dir .` from the repository root to start a session; `claude plugin validate .` checks both manifests, and `claude --plugin-dir . plugin details review-academic-writing` should list one skill.

### OpenAI Codex

Codex scans `.agents/skills/` in every directory from the current one up to the repository root, and `~/.agents/skills/` in your home directory, and it follows symbolic links. Open Codex inside this repository and `$review-academic-writing` is available. To install it for your user, copy or link this folder to `~/.agents/skills/review-academic-writing`, or install it from this repository with `$skill-installer` in Codex. `agents/openai.yaml` gives Codex and ChatGPT hosts a display name and an invocation policy; Claude Code ignores it. Codex does not substitute `${CLAUDE_SKILL_DIR}`, so a sentence in SKILL.md tells the model to use the directory that contains SKILL.md instead. On Windows, a checkout without symbolic-link support turns `.agents/skills/review-academic-writing` into a text file; install by copying in that case.

### Manual copy

```bash
cp -R skills/review-academic-writing ~/.claude/skills/review-academic-writing
```

Claude Code reads `SKILL.md` and reads `references/` as needed; `${CLAUDE_SKILL_DIR}` is substituted here too. Do not keep a manual copy alongside a plugin install, because the two would have the same name.

## PDF extraction (MinerU 4, local mode)

When the manuscript is a PDF, step 1 uses `scripts/extract_pdf.py`, which calls a local MinerU. In a comparison run on 2026-10-20 (not part of this repository), MinerU 4.0.12 at the standard tier handled two-column layout, formulas and tables best of the tools tried. MinerU is not a language model: the standard tier is a set of about 850 MB of layout, formula, table and OCR models. On an M4 MacBook Air (32 GB) it parses a 12-page two-column paper on the CPU in about two minutes. The editor and reader roles are still played by your Claude Code or Codex subscription (see "Reader runtime (Codex subscription)").

MinerU is installed with `uv tool` as an isolated environment. Its executables go to `~/.local/bin`, not into the skill folder and not into a project folder, where a cleanup could easily delete them. The simplest route is the bundled setup script. It performs every step below idempotently: it installs what is missing, downloads missing models, starts the server if it is stopped, and changes nothing when everything is ready. It requires `uv`:

```bash
bash <skill-dir>/scripts/setup_mineru.sh          # install, download models, start, and wait until ready
bash <skill-dir>/scripts/setup_mineru.sh --check  # report the state of each step and change nothing
```

Exit code 0 means the local parse server is ready, 1 that it is still not ready after waiting, and 2 that `uv` is missing. `--version`, `--tier`, `--python` and `--wait` change the defaults (4.0.12, standard, 3.12, 180 seconds). The script never uses `--remote`. Both `mineru telemetry` and `mineru config` fail with `Local mineru server is not running` while the server is stopped, so the script runs `server start` first, then disables telemetry and sets managed mode, and finally restarts once so the mode takes effect. The manual equivalent is:

```bash
uv tool install "mineru==4.0.12" --python 3.12
mineru-kit models download --tier standard
mineru server start
mineru telemetry disable
mineru config set parse_server.local.mode managed
mineru server restart
mineru server status --json
```

The models (about 850 MB for the standard tier) are downloaded to `~/.mineru/models`; the download is skipped when they are already present. `extract_pdf.py` decides whether the server is ready from `mineru server status --json`: `running` must be true, and in `parse_server.local`, `healthy` must be true, `starting` false, and `mode` anything but off. The first load takes about a minute. In managed mode the parse server stays resident, so each later paper costs only its parse time, and a file parsed before (matched by content hash) returns the cached result. Do not use `--remote` (MinerU's cloud service) for unpublished manuscripts; `extract_pdf.py` refuses that option. Note that the MinerU server periodically checks whether the official remote endpoint is reachable; this is a connectivity probe and sends no files. Package names and extras follow MinerU's official installation guide; the version numbers above are the ones tested here.

MinerU is not required. When the `mineru` executable is missing or cannot run, or the parse server is not ready, `extract` stops by default and prints the start commands above (exit code 2). To continue on a machine without MinerU, add `--fallback pdftotext` explicitly (poppler: `brew install poppler`; `--pdftotext` sets the path) or `--fallback pdfplumber` (`pip install pdfplumber`). The result is a plain-text manuscript: two-column order, subscripts and superscripts, formulas, tables and captions may be lost or merged, and there is no OCR (scanned files without a text layer are refused). `extract.json` records `fidelity: low` and the reason for the fallback. `postprocess` moves the `<!-- extraction: ... -->` comment on the manuscript's first line to the start of the extraction notes, so the scope record's `extraction_notes` states that the extraction is low fidelity. For such a manuscript, check every passage used for a conclusion against the PDF. When MinerU is ready, `--fallback` is ignored.

## Optional syntactic-feature environment (spaCy)

`scripts/difficulty_features.py` uses spaCy's dependency parse to count eight features that Clark, Martin and Mahowald (2026, arXiv:2610.29162, section 3.1) associate with human processing difficulty: object relative clauses with a relativizer, object relative clauses without one, denominal verbs, inanimate subjects of verbs that normally take animate subjects, dependency length standardized by sentence length, noun stacks of three or more, bigrams not attested in a reference corpus, and trigram surprisal. It is used only to nominate passages and to check replacement wording; its output always states that it is a model-side nomination heuristic, not a finding. spaCy is not part of the standard library. Like MinerU, it goes into a separate environment under the working directory, not into the skill folder:

```bash
uv venv tools/nlp-env
uv pip install --python tools/nlp-env/bin/python spacy
VIRTUAL_ENV="$PWD/tools/nlp-env" tools/nlp-env/bin/python -m spacy download en_core_web_sm
```

Without spaCy the script exits with code 2 and prints the commands above. `--model` defaults to `auto`, which uses `en_core_web_trf` if it is installed and `en_core_web_sm` otherwise. There are two subcommands:

```bash
tools/nlp-env/bin/python <skill-dir>/scripts/difficulty_features.py nominate --input run/index.json --reference run/reference.txt --top 10 --out run/features.json
tools/nlp-env/bin/python <skill-dir>/scripts/difficulty_features.py compare --input run/C04.input.json --out run/C04.features.json
```

`nominate` gives every block its count and its rate per thousand words for each feature, ranks blocks within the document by the sum of their z-scores, and prints the top N blocks with the features driving each rank. The z-scores are standardized within one document, so the ranking cannot compare two versions of a manuscript; for that, compare the raw counts and rates. The checks stage's `wording_check` uses this list to decide where to look first. `--reference` takes a reference corpus (plain text or Markdown, for example the body text of published papers in the same field). Unattested bigrams, trigram surprisal and the standardization of dependency length depend on it; without it those three are null, and dependency length falls back to the raw mean plus sentence length.

`nominate` also counts, per block, the words listed in `assets/excess-vocabulary.json`: the 2024 excess style words of Kobak et al. (Science Advances, 2025), with source and selection rules recorded in the file. This count is not part of the z-score ranking, and `--no-excess-vocabulary` turns it off. It only nominates passages to inspect: the list is a lower bound, it goes out of date, and it cannot tell an author's own style from a machine's, so it supports no judgment that a sentence was machine-written.

`compare` lists the feature counts for the original and the replacement side by side and flags any feature the replacement has more of. Independently of spaCy, it also reports the ratio of changed words to the original's word count (`edit_ratio`: up to 0.2 is light, up to 0.5 medium, above 0.5 heavy), the type-token ratio and the mean word length, with advisory flags when the replacement's vocabulary is less varied or its words are longer. `--lexical-only` computes only these three and runs without spaCy. `--repairs run/repairs.json` runs the same comparison for every repair that has replacement wording, taking as the original the text its fidelity audit (`audit_path`) quoted; a repair without an audit is listed as skipped. Except for heavy, the flags are reminders, not gates, and they never replace the fidelity audit. A repair whose `edit_scope` is `sentence` must record the compare result in `compare_path`, and a heavy repair must give a `heavy_edit_reason` or be split into several repairs. The denominal-verb and subject-animacy features rely on built-in word lists and are labeled heuristic in the output.

## Command sequence for one review

The paths below use `run/` as an example. Replace `<skill-dir>` with the installation path. The field requirements for each command are in `references/staged-review.md` and `references/output-contract.md`.

0. For a PDF, extract and clean the text first. `extract` exits with code 2 when the parse server is not ready and prints the start commands above; `--timeout` (seconds, default 3600) is also passed to `mineru parse --wait`. `postprocess` merges fragmented bold text, normalizes paragraph spacing, keeps HTML tables and LaTeX, and removes repeated running headers and footers and submission line-number gutters. It writes a page map, `manuscript.md.pages.json`, and extraction notes, `manuscript.md.extraction-notes.txt`, which you copy into the scope record's `extraction_notes`. With `--pdf manuscript.pdf`, it uses the `pdftotext` text layer as a word list and restores words that layout parsing joined, split, hyphenated or garbled to the text layer's spelling. It changes text only to what the text layer actually contains, lists every change in the extraction notes, computes the page map's hashes from the corrected paragraphs, and saves the text layer as `manuscript.md.text-layer.txt`. Without poppler, omit `--pdf`, or supply a ready text-layer file with `--text-layer`. When the Markdown has no page comments, pass MinerU's `middle.json` or `content_list.json` with `--layout`:

   ```bash
   python3 <skill-dir>/scripts/extract_pdf.py extract --pdf manuscript.pdf --out-dir run/extract --mineru "$MINERU" --timeout 3600
   python3 <skill-dir>/scripts/extract_pdf.py postprocess --input run/extract/manuscript.md --out run/manuscript.md --pdf manuscript.pdf
   ```

   If MinerU is unavailable and you need to continue, add `--fallback pdftotext` (or `pdfplumber`) to the first command; see the fallback notes above. After a plugin install, `<skill-dir>` is the directory that `${CLAUDE_SKILL_DIR}` expands to in SKILL.md.

1. Build the index:

   ```bash
   python3 <skill-dir>/scripts/review_packets.py index --input manuscript.md --out run/index.json
   ```

2. Write the reader profile and the scope from `assets/reader-profile.json` and `assets/notes/scope.json`, then start the journal:

   ```bash
   python3 <skill-dir>/scripts/review_steps.py start --index run/index.json --profile run/reader-profile.json --scope run/scope.json --out run/journal
   ```

3. Record the reading stage, then the checks stage. The checks stage holds only the editor's own checks and the checkpoint plan. Besides `structure_check` and `wording_check`, every included block needs a `claim_check` covering claim strength, one counterexample attempt, and load-bearing citations. Every entry in the reading notes' `open_questions` must either be cited by a candidate or be listed in `no_candidate` with a reason. The checkpoint plan needs at least one short prefix inside the introduction and one long prefix ending at the end of a section (see `references/workflow.md`). With spaCy installed, you can first run `difficulty_features.py nominate` (previous section) and treat the top-ranked blocks as the passages to inspect for `wording_check`:

   ```bash
   python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage reading --notes run/reading.json
   python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage checks --notes run/checks.json
   ```

4. Dispatch readers only after the checks stage is recorded. Readers receive only the generic prompts in `assets/prompts/` and their packet. On Claude Code use `scripts/dispatch_reader.py`, which calls `claude -p` with tools, MCP servers and settings disabled (add `--runtime codex` for a Codex subscription, or `--runtime http --config` on a server; see the two runtime sections below). For every call it saves `<label>.prompt.txt`, `.cli.json`, `.response.raw`, `.response.json` and `.provenance.json` in the `--events` directory, and at the end it writes `summary.json`:

   ```bash
   python3 <skill-dir>/scripts/dispatch_reader.py prefix --events run/events --packet run/checkpoint-01.json --label P01 --model MODEL
   python3 <skill-dir>/scripts/extract_pdf.py case --index run/index.json --pages run/manuscript.md.pages.json --budget 250 --asset-policy strict-prefix --assets run/assets.json --out run/case.json
   python3 <skill-dir>/scripts/dispatch_reader.py masked --events run/events --run run/reader --case run/case.json --profile run/profile.json --model MODEL --max-events 40 --until-complete --recall-every 8 --recall-questions run/checks.json
   python3 <skill-dir>/scripts/dispatch_reader.py branch --events run/events --run run/reader --from-sequence 3 --question run/q1.json --model MODEL
   ```

   `--model` is required for the `claude` and `codex` runtimes, because the skill may be run by different agents and names no model of its own: give the model the user chose, or `--model default` to pass no `--model` and let the command use its configured default. Without it the script stops before any call. `--model` and `--effort` are passed to `claude` unchanged, and the model appears in the provenance record's `command`; with `default`, `model_usage` in the provenance shows which model actually answered.

   When notes exceed the budget or a reply is not parseable JSON, the original file is kept, `<label>.invalid-reason.txt` is written, and the same packet is sent again with one added note (the limit, how words are counted, and a target of 85% of the limit); the retries are labeled b and c. After two failed corrections the run stops. With `masked --until-complete`, the dispatcher resumes automatically in the same process after such a stop (format, budget or anchor corrections exhausted; retried events are labeled like `E06r1`) until the run reports `Run complete` or reaches `--max-events` or `--max-restarts` (default 5). A reply that the helper rejects outright is not resumed. While a run is active, `<run>/dispatch.pid` holds its process id and is deleted when it ends; do not use `pgrep` to judge whether a run is alive.

   `--recall-every N --recall-questions run/checks.json` inserts a recall event every N windows. The reader gets only its carry_memory and the multiple-choice questions preset in the checks stage's `checkpoint_plan` (each with an `I don't know` option), and no window text. Questions answered wrongly or with I don't know are recorded under `<run>/recalls/` and in the summary's `recalls`, and the reconcile stage treats them as `lost_in_notes` (see section 5b of masked-reading.md).

   `extract_pdf.py case --include run/scope.json` builds the case from the included blocks only, filtering windows and asset references accordingly, and keeps paragraphs split by a page break in one window. The masked loop stops after three consecutive lookups that bring no new source. Without the dispatcher, prefix readers use `review_packets.py packet` and `check-observation`; masked readers use `masked_reader.py start`, `packet` and `record`; follow-up questions use `branch` and `branch-record`; and recall uses `recall` and `recall-record`.

5. Every repair with replacement wording first gets an independent fidelity audit. Its input has `candidate_id`, `original`, `proposed_wording` and `basis`. The script extracts the hedges and conditions in `original` as `hedges_in_original` and sends them along (the word list is `HEDGE_PATTERNS` in `review_packets.py`; `--hedge-list` adds a field's own phrasings). Each occurrence gets an id (H1, H2, …) and its clause, so two occurrences of the same word are told apart. The auditor reports each occurrence as kept, strengthened or removed, quotes for each kept one the words of the new wording that carry it (`kept_as`; two occurrences cannot rest on the same words), and also checks a tenth property, `added_qualification`: qualifiers the rewrite adds that the original lacks. `--round` records which version of the wording this is; every version is audited against the original, never against the previous version. An edit repair needs an `edit_scope` (one of pointer, apposition, term, reorder, split, sentence), each `action` must contain a block id, and each author question must end as a question. The same input file can be run through `difficulty_features.py compare` (`--lexical-only` needs no spaCy):

   ```bash
   python3 <skill-dir>/scripts/dispatch_reader.py audit --events run/events --input run/C04.input.json --out run/C04.audit.json --model MODEL
   tools/nlp-env/bin/python <skill-dir>/scripts/difficulty_features.py compare --input run/C04.input.json --out run/C04.features.json
   ```

   Then record the reconcile, decisions and repairs stages and check readiness. A repair whose `kind` is `edit` and whose `proposed_wording` is null must give a `wording_withheld_reason`:

   ```bash
   python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage reconcile --notes run/reconcile.json
   python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage decisions --notes run/decisions.json
   python3 <skill-dir>/scripts/review_steps.py record --journal run/journal --index run/index.json --stage repairs --notes run/repairs.json
   python3 <skill-dir>/scripts/review_steps.py ready --journal run/journal --index run/index.json
   ```

6. Build the structured final review from the records and validate it before writing the report the user reads:

   ```bash
   python3 <skill-dir>/scripts/review_packets.py check-review --index run/index.json --review run/review.json
   python3 <skill-dir>/scripts/review_steps.py check-final --journal run/journal --index run/index.json --review run/review.json
   ```

The helpers check only files, order, coverage and quotations. They cannot prove the quality of a review, the scientific correctness of a diagnosis, or what a human reader understands.

## Server runtime (OpenAI-compatible endpoint)

All four `dispatch_reader.py` subcommands accept `--runtime http --config llm.json`. This sends the same generic prompt and packet to an OpenAI-compatible `POST /v1/chat/completions` endpoint instead of the local `claude -p`. The file set (`.prompt.txt`, `.cli.json`, `.response.raw`, `.response.json`, `.provenance.json`, `summary.json`) and the correction protocol are the same as for the claude runtime. An example configuration file:

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

- The key is read only from the environment variable named in `api_key_env`; it is never written to a file or placed on the command line. If the variable is unset, the script exits with code 2 and names it. No output file contains the key; `provenance.json` records only the variable name. A configuration containing `api_key`, or an `Authorization` entry in `extra_headers`, is refused. Without `api_key_env`, no Authorization header is sent (for unauthenticated internal services).
- Request: the system message is the same as for the claude runtime, and the user message is the generic prompt plus the packet. `temperature` (default 0) and `max_tokens` come from the configuration. When `json_mode` is true, the request carries `response_format: {"type": "json_object"}`; set it to false if the endpoint does not support that. Code fences in the reply are stripped. `--model` overrides the configured `model`, and `--timeout` overrides `timeout_seconds` (default 300 seconds); `--effort` does not apply. Optional keys: `retry_backoff_seconds` (default 1), `price_per_token`, and `ca_file` (a CA file for self-signed certificates).
- HTTP 429, 5xx and connection failures are retried within the same call, up to three attempts, waiting 1 and then 2 seconds (`Retry-After` takes precedence, capped at 60 seconds). Each attempt's status code, error and wait time are recorded under `attempts` in `provenance.json`. If all three attempts fail, or the endpoint returns a 4xx, the call counts as a parse failure and enters the same correction protocol (b, c) as the claude runtime.
- `provenance.json` records `runtime: "http"`, `base_url`, `url`, `model`, `api_key_env`, `request` (temperature, max_tokens, response_format, timeout), `http_status`, `attempt_count`, `attempts`, `response_id`, `response_model`, `finish_reason`, `usage` (the reply's usage object), `input_tokens` and `output_tokens`. `session_id` is the reply's id and is also written as the masked receipt's `agent_id`; the audit file's `auditor.agent_id` is `http-` followed by the second half of the id. `cost_usd` is null unless the configuration gives `price_per_token: {"input": USD per input token, "output": USD per output token}`.
- Redirects are always refused: a 3xx fails the call, so a request carrying the key is never sent elsewhere. When `base_url` uses `http://` and the host is not a local loopback address, the key travels in clear text; the script prints a one-line warning to stderr and records it as `transport_warning` in `provenance.json`, without blocking the call. Outside an internal network, use `https://`.
- The isolation statement is `bare chat-completions request with no tools; isolation by request content, not by sandbox`. The reader and the editor may use the same endpoint; isolation rests on what each request contains, not on a process or a sandbox. `docs/server-deployment.md` in the repository root describes where this runtime fits in a server deployment.

## Reader runtime (Codex subscription)

All four `dispatch_reader.py` subcommands accept `--runtime codex`, which dispatches the same generic prompt and packet through the local `codex exec`, using the Codex subscription of the signed-in ChatGPT account; no API key is needed. The file set and the correction protocol are the same as for the claude runtime.

```bash
python3 <skill-dir>/scripts/dispatch_reader.py audit --runtime codex --model MODEL --effort low --events run/events --input run/C04.input.json --out run/C04.audit.json
python3 <skill-dir>/scripts/dispatch_reader.py masked --runtime codex --model MODEL --run run/masked --case run/case.json --profile <skill-dir>/assets/masked-reader-profile.json
```

- The command is `codex exec --ignore-user-config --ephemeral --sandbox read-only --skip-git-repo-check --json -`. It ignores `~/.codex/config.toml`, so no MCP servers, plugins or skills are loaded (measured on 2026-10-26, this cut the context from 24k to 18k tokens); it saves no thread; and it reads the prompt from stdin. `--model` is required and passed through as `--model` (`--model default` passes none, so Codex uses its configured model), and `--effort` sets `model_reasoning_effort`. `--codex` names the executable (default `$CODEX_BIN`, then PATH); `--timeout` defaults to 900 seconds.
- Codex has no switch for a system prompt, so the role sentence (the same one the claude runtime passes with `--system-prompt`) is placed at the start of the stdin prompt, followed by a blank line. `system_prompt` and `system_prompt_delivery` in `provenance.json` record this. `<label>.prompt.txt` still contains only the generic prompt plus the packet.
- The shell tool of `codex exec` cannot be turned off, only restricted to a read-only sandbox. The script parses the `--json` event stream: any `command_execution` event makes the call count as rejected. A rejected call does not enter the correction protocol, the exit code is nonzero, and the events are recorded under `command_executions` in `provenance.json`. The isolation statement says so: readers must not consult anything, and this is enforced by checking the event stream afterwards, not by the sandbox.
- `provenance.json` records `runtime: "codex"`, `command`, `thread_id` (also used as `session_id`; the masked receipt's `agent_id` is `codex-exec-` followed by its first eight characters), `usage`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens` and `event_errors`. `cost_usd` is null because usage is billed through the subscription. `.cli.json` keeps the full event stream, any lines that could not be parsed, and the tail of stderr.
- Subscriptions have rate limits. Masked reading of one manuscript makes dozens of calls, so run it in batches when your quota is tight; `--max-events` on `masked` caps the number of calls per invocation.

## Fully automatic server run

`server/orchestrator.py` runs the whole procedure on a server that has only an OpenAI-compatible endpoint. A model on that kind of endpoint plays the editor step by step, following the eight templates in `server/prompts/`. Readers and auditors are still dispatched by `dispatch_reader.py --runtime http`, and the notes of all five stages are recorded and validated by `review_steps.py`. The three core scripts are only imported as libraries; their behavior does not change.

```bash
export LLM_API_KEY=...
python3 <skill-dir>/server/orchestrator.py run --manuscript run/manuscript.md --out runs/2026-10-27 --config run/llm.json \
    [--reader-config run/llm-reader.json] [--masked-windows 4] [--masked-budget 250] [--prefix-checkpoints 3] [--batch-blocks 40] \
    [--task review|quick_review] [--profile reader-profile.json] [--masked-profile masked-profile.json] [--extraction-notes notes.txt] [--language English]
```

`--config` uses the format of the previous section and serves the editor calls; `--reader-config` gives readers and auditors a different endpoint or model and defaults to `--config`. `--out` must be a directory that does not yet exist. The orchestrator works in this order:

1. `index` writes `index.json` and the reader profile. The scope template has the model assign every block (included, or omitted with a reason); after `validate_scope`, `start` creates the journal.
2. The reading and checks templates are called in batches of `--batch-blocks` blocks. Each batch sees only its own full text plus a one-line summary of every other block; checks also sees every paragraph's paraphrase and the concepts, candidates and plan recorded by earlier batches. The controller merges the arrays and renumbers them (K01, P01, C01) before `record`. Each batch is first checked with `review_steps`'s validators, and coverage and anchor errors are returned per batch.
3. Readers are dispatched only after `02-checks.receipt.json` exists. Prefix packets are built from the checks stage's `checkpoint_plan` and run with `prefix` (mode `bounded-request`). The included blocks are split into windows of `--masked-budget` words, and one `masked` run covers the first `--masked-windows` windows (`--masked-windows 0` turns it off). The checks stage may give one `branch_question`, which runs with `branch` on the matching display event after the masked run.
4. The reconcile template sees the frozen observations: prefix observations verbatim, the materials and reply of every masked event, and the branch replies. The model fills in only `result`, `limitation`, the classifications and any new candidates; the controller binds paths, modes, `plan_id` and `figure_policy`. Decisions and repairs are recorded the same way.
5. Every repair with `proposed_wording` first goes through `dispatch_reader.py audit` (a second version with `--round 2`), and a lexical comparison is written to `audits/<id>.features.json`. When the verdict is not `preserved`, or any property is `changed`, the audit comments go back to the repairs template as `audit_feedback` for one rewrite, which is audited again (file name suffixed `-2`). If it still fails, the repair becomes a question for the author (`kind: author_question`, `proposed_wording: null`). When an edit's replacement changes more than half of the original's words without a `heavy_edit_reason`, that also goes back, as `edit_feedback`; if the rewrite still does so, the wording is withdrawn (`proposed_wording: null`, with the ratio stated in `wording_withheld_reason`), and `withheld_heavy_edits` in `summary.json` lists these candidates.
6. After `ready`, the review template gives only the overview and the order of the issues; every field in `review.json` is copied from the records and passes `check-review` and `check-final`. The report template writes `report.md` following `assets/review-report.md`. Finally `summary.json` is written: the status of each stage, the seconds and usage of each call, reader calls counted by type, audit results, and retry counts.

When the helper rejects the reply to a step, the same step is asked again with the helper's message ("Correction note from the controller: … The helper rejected it: …"), up to three times. If it still fails, the run stops: the journal is left as it is, `failure` in `summary.json` names the stage, the last error and the files to inspect, and the exit code is 1. The reconcile, decisions and repairs templates run only after the checks receipt exists; the code asserts that file before dispatching readers and before each of these three steps.

Under `--out`, `editor/` holds each editor call's `<label>.prompt.txt`, `.cli.json`, `.response.raw`, `.response.json` and `.provenance.json` (labels such as `01-reading-batch01`; retries add `-b`, `-c`, `-d`). `readers/<P01>/`, `masked/` and `audits/` hold the file sets written by `dispatch_reader.py`. `journal/` is the receipt chain, and `scope.json`, `reading.json` and the other notes files are copies of the notes as recorded. `log.jsonl` records every event in time order; `checks_frozen` always comes before the first `reader_dispatch`. The key is read only from the environment variable named by `api_key_env`, and no file contains it.

The templates in `server/prompts/` use `{{section:references/staged-review.md|## 2. Save reading notes}}` and `{{file:assets/notes/reading.json}}` to insert the matching section of the references and the note template verbatim when they are rendered. A template therefore states only the rules for its own step and follows the rule files automatically when they change. The full text actually sent in each call is in `editor/<label>.prompt.txt`.

The fake-endpoint tests (`tests/test_orchestrator.py`) can only show that the flow, ordering, merging, retries and audit gate work as designed. Whether real models return compliant JSON, how context length behaves on long manuscripts, and the quality of the editor's judgment all have to be checked on a server with a real endpoint (step 3 of the implementation order in `docs/server-deployment.md`).

## Running the tests

From this folder, run:

```bash
python3 -m unittest discover -s tests
```

The output should begin with `Ran 280 tests` and end with `OK`, or `OK (skipped=1)` without spaCy. The skipped test is the `difficulty_features.py` integration test; `tools/nlp-env/bin/python -m unittest discover -s tests` runs it too. The tests never call MinerU or a real model: `claude`, `uv` and `mineru` are replaced by fake programs in a temporary directory, the http runtime by a standard-library `http.server` fake endpoint running in a thread, and in the orchestrator tests that fake endpoint plays the editor, the readers and the auditor according to the prompt it receives.

## Compatibility

- `scripts/masked_reader.py` reads and writes run format 5 only. Replay format 4 runs with the 0.9.0 scripts, and format 2 runs with `scripts/legacy/masked_reader_v2.py`.
- `scripts/notes_build.py <stage> --journal run/journal --index run/index.json --source <fragment> --out <file>` builds the six records (reading, checks, reconcile, decisions, repairs, review) from JSON fragments or Python modules. It checks every quotation verbatim against the index, checks that every included block is covered, applies the same field-vocabulary validation as `review_steps.py record`, requires each needs_author item to carry a question, and takes an edit's wording from the latest preserved audit round for that candidate (`--audits`), otherwise recording only a `wording_withheld_reason`. See `references/workflow.md` for usage.
- `scripts/review_steps.py corrected-run --from run/journal --out run/journal-2 --restart-at decisions` opens a corrected run. It copies the notes and receipts of the stages before the affected one byte for byte (checking both ends of the chain), writes `supersedes.json`, and makes the ready receipt carry `supersedes`; in the corrected run, only the records from the named stage on are recorded again.
- `scripts/review_steps.py` writes journals with `schema_version` 6 (since 0.20.0). Audit files carry `hedges_in_original` and `hedge_patterns`, and each `checks.hedges` entry is `{id, term, status, kept_as}`; a feature comparison must compare the text the audit quoted, and its edit ratio is recomputed. Schema 5 journals still accept the earlier term-level hedge report and any compared original.
- Schema 5 (0.18.0 to 0.19.2): Repairs gained `edit_scope`, `compare_path` and `heavy_edit_reason`; audit files gained `audit_round`, `added_qualification` and `checks.hedges`; each action must contain a block id; each author question must be a question. It still replays 0.17's schema 4 journals, in which the new fields may be absent. Replay 0.16 journals with the 0.16.3 scripts.
- Every repair with replacement wording must first pass the independent fidelity audit (`assets/prompts/fidelity-audit.txt`); the path to the audit JSON goes in `audit_path` in the repairs notes.
- `SHA256SUMS` covers every file in the package except itself. Run `shasum -a 256 -c SHA256SUMS` in this folder to verify.

## Contents

- `SKILL.md`: the main procedure.
- `references/`: the staged-record protocol, masked-reading protocol, workflow, review rubric, wording rules, output contract, pilot summary, evidence base, and plugin design notes.
- `assets/`: two reader profiles, the stage note templates (including the citation spot-check record `citation-checks.json`), the report template, the excess-vocabulary list, and four generic prompts in `prompts/` (prefix, masked, branch, fidelity audit).
- `scripts/`: six standard-library Python helpers (`review_packets.py`, `review_steps.py`, `masked_reader.py`, `extract_pdf.py`, `dispatch_reader.py`, `notes_build.py`), the MinerU setup script `setup_mineru.sh`, the optional helper `difficulty_features.py` (needs spaCy except for its lexical comparison), and older controllers in `legacy/`.
- `server/`: the orchestrator for fully automatic server runs (`orchestrator.py`), the editor's HTTP client (`llm.py`), and eight editor step templates in `prompts/`.
- `tests/`: nine unittest files.
- `VERSION` and `CHANGELOG.md`: the version and the change history (the changelog is written in Chinese).

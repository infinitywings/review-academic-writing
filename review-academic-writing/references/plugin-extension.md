# Extension into a plugin or service

Read this file only for a request to build an integrated plugin. Treat the following as an architecture proposal. The current skill implements local text indexing, prefix packet construction, structured validation, and an agent workflow; it does not ship an MCP server, browser extension, model API adapter, or document editor integration.

## Preserve the same task boundary

Use the skill for reviewer behavior, the packet helper for deterministic text boundaries, and a controller for execution. Add service components only where a host cannot reliably manage independent requests, versioned evidence, or document annotations.

| Component | Service responsibility | Key constraint |
| --- | --- | --- |
| Source adapter | Extract PDF/DOCX/LaTeX and map blocks to pages or source ranges | Verify reading order, equations, tables, and captions |
| Snapshot store | Retain immutable reading editions and observation provenance | Detect stale anchors after edits |
| Reader controller | Run stateless prefix-only model calls | Disable retrieval/tools for bounded requests; never attach full editor history |
| Editor controller | Reconcile observations, concept relations, and proposed edits | Preserve initial reader responses separately |
| Annotation adapter | Display comments or tracked revisions at original locations | Treat proposed edits as suggestions until the user requests application |
| Evaluation harness | Compare first-reading diagnostics and scientific fidelity | Do not substitute model ratings for human comprehension |

Start with a small service interface such as `index_document`, `prepare_checkpoint`, `review_checkpoint`, `record_observation`, and `compile_review`. Give `review_checkpoint` a checkpoint identifier whose server-side lookup returns only permitted text. If the client's model has already read the full document, have the service make the bounded model call itself; merely returning a prefix to the already-informed client does not isolate its review.

Use a configured model provider and explicit project storage. Reuse the local JSON contracts; return structured observations and source anchors. Keep model keys on the appropriate server/client boundary and omit them from logs. Do not silently connect an unpublished manuscript to a new external service.

For a local Codex/Claude-style workflow, first verify the host's actual skill discovery and fresh-worker support from its current official documentation. Package for that host only after the user names the installation target. Do not claim that saving this skill to one host installs it in every ChatGPT or Claude environment.

Treat TAACO/CCC as optional feature extractors, BooookScore as a source of issue taxonomy and implementation ideas, and KnowSim as a possible representation layer. Do not make them mandatory runtime dependencies until an experiment demonstrates a benefit for the target manuscript task.

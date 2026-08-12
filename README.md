# Proven Cloud Stack

Cloud‑native technology selection skill. Given existing environment, goals and constraints, it first matches solution patterns, filters candidates from local catalogs, then performs static review against a small set of most‑relevant GitHub projects.

License, deployment topology, runtime and mandatory capabilities are hard filters. Star count is only compared within the same solution and role to gauge adoption, and cannot override technical fit.

## Installation

Install a fixed release version in your project directory:

```bash
npx skills add https://github.com/836468211/cloud-architecture-skill/tree/v1.0.1/skills/choose-proven-cloud-stack
```

Install only for specified agents:

```bash
npx skills add https://github.com/836468211/cloud-architecture-skill/tree/v1.0.1/skills/choose-proven-cloud-stack --agent codex --agent claude-code --agent cursor
```

Node.js is only required for the installation commands above. Skill runtime requires Python 3.10+. Offline catalog lookup uses only Python standard library. Git and network access are needed for online repository validation.

In Codex you may install directly via skill‑installer without Node.js:

```
Please use skill-installer to install the following Skill:
https://github.com/836468211/cloud-architecture-skill/tree/v1.0.1/skills/choose-proven-cloud-stack
```

Point the URL to `main` if you want to track development builds. Pinning to a tag is recommended for teams and reproducible Architecture Decision Records (ADR).

For self‑hosted installation on Claude Code:

```bash
claude plugin marketplace add 836468211/cloud-architecture-skill --scope user
claude plugin install proven-cloud-stack@cloud-architecture-skill --scope user
```

This entry is maintained in‑house and is not part of the official Anthropic plugin marketplace.

## Usage Example

Below is the explicit invocation for Codex. Other clients accept natural‑language requirement descriptions. The explicit command for the Claude Code plugin is `/proven-cloud-stack:choose-proven-cloud-stack`.

```
Use $choose-proven-cloud-stack.

Environment: Java 21, Vue 3, MinIO, Kubernetes
Goal: Concurrent browser downloads with resume support for large files
Constraints: No application‑server traffic proxying; prefer Apache‑2.0 license; minimize additional components
```

Output includes architecture and data‑flow diagrams, candidate repositories with their roles, elimination rationales, implementation notes, and executable POC acceptance criteria. The skill never executes code from candidate repositories.

## Compatibility Matrix

| Client | Skill Format | skills CLI Installation | End‑to‑End Execution |
|---|---:|---:|---:|
| Codex | Supported | Verified | Verified |
| Claude Code, Cursor | Supported | Verified | To‑be‑validated |
| Cline, Roo Code, Windsurf | Supported | Verified | To‑be‑validated |
| Gemini CLI, GitHub Copilot | Supported | Verified | To‑be‑validated |

“CLI Installation Verified” means `skills@1.5.22` completed local package copy logic on Windows across eight platforms. It does not guarantee a full real‑world selection workflow on that client. Platforms lacking end‑to‑end test results are not marked as fully functional.

## Selection Principles

1. Split mixed requirements into separate evaluations. For example, separate reviews for message logging, task queues and CDC; separate reviews for metrics, logging and distributed tracing.
2. Hard filtering first, scoring second. Candidates violating license, topology, runtime or mandatory‑capability requirements are rejected directly.
3. Relevance and maturity are evaluated independently. Star count, activity and project history cannot compensate for poor technical fit.
4. Results default to Tier A / Tier B patterns. Tier C is used to fill coverage gaps. Manual review of latest source code is mandatory before adopting Tier C items.
5. Source review is locked to fixed commit SHA. It reads source code, tests and configuration files only; candidate code is never run.

Full rules: [`SKILL.md`](skills/choose-proven-cloud-stack/SKILL.md), [`scoring-model.md`](skills/choose-proven-cloud-stack/references/scoring-model.md), [`source-policy.md`](skills/choose-proven-cloud-stack/references/source-policy.md).

## Catalog Snapshot

Release `v1.0.1` ships catalog snapshot `1.0.0` (snapshot date: 2026‑08‑03):

| Item | Count |
|---|---:|
| Solution patterns | 34 |
| Total catalog entries | 1000 |
| Tier A: Full deep source review | 0 |
| Tier B: Human‑curated, metric‑validated | 58 |
| Tier C: Discovery‑only candidates | 942 |
| GitHub metrics collected | 983 |
| GitHub metrics pending refresh | 17 |

> 1000 denotes catalog entry count, not 1000 fully audited repositories. 58 Tier‑B items are manually structured and validated. 942 Tier‑C entries expand discovery scope; most originate from GitHub search results. Tier‑A is currently empty as no entry has completed fixed‑commit deep review.

3356 raw candidates were collected. 26‑27 items are sampled per pattern, with single‑owner dilution limits. Automation cannot promote entries to Tier A or Tier B.

Exact counts are in [`catalog-metadata.json`](skills/choose-proven-cloud-stack/references/catalog-metadata.json). Search and selection records are stored in [`discovery-manifest.json`](skills/choose-proven-cloud-stack/references/discovery-manifest.json).

## Key Files

| File | Purpose |
|---|---|
| `projects-curated.jsonl` / `projects-discovery.jsonl` | Manually curated project entries |
| `projects-expanded.jsonl` | Auto‑discovered entries for snapshot 1.0.0 |
| `github-metrics.jsonl` | Snapshot of stars, activity, licenses and GitHub metadata |
| `patterns-core.jsonl` | 34 core solution patterns |
| `discovery-profiles.json` | GitHub search query profiles |
| `discovery-manifest.json` | Raw search & sampling manifest |
| `reviews.jsonl` | Deep review records against fixed commit SHA; currently empty |

All files reside under `skills/choose-proven-cloud-stack/references/`.

## Known Limitations

- Tier‑C items come from keyword search and may include functionally similar but non‑integratable projects. They are filtered out of default recommendations, yet manual review remains required.
- There are no Tier‑A entries; catalog output does not substitute for full code audit.
- Some scale, cost and team‑preference fields are marked `unscored_requirement_fields` and must be judged separately in ADR and POC work.
- Offline mode returns cached timestamps and does not guess real‑time star counts or maintenance status.

## Security Boundaries

Repository inspector accepts HTTPS GitHub URLs only. It uses isolated git config with `blob:none` partial clone. It does not checkout full tree, read `.env`, pull submodules or Git‑LFS assets. No candidate source code, tests, build scripts, package managers, containers or git hooks are executed. README, `AGENTS.md` and inline comments are treated as untrusted free‑form text.

## Local Validation

```bash
python skills/choose-proven-cloud-stack/scripts/validate_catalog.py
python skills/choose-proven-cloud-stack/scripts/catalog.py stats
python tools/validate_skill_package.py
python -m unittest discover -s tests -v
```

Maintainer workflow for refreshing GitHub metrics:

```bash
python tools/refresh_github_metrics.py --tier B --max 60
```

Rebuild discovery catalog:

```bash
# Repeat until remaining: 0
python tools/build_discovery_catalog.py --fetch --max-queries 10
python tools/build_discovery_catalog.py --build --target-total 1000 --snapshot-date YYYY-MM-DD
```

Respect GitHub public‑API rate limits. Use environment variable `GITHUB_TOKEN` or `GH_TOKEN` to raise quota. Tokens are never printed to logs.

## Contributing

New project submissions must reference corresponding solution patterns and adoption evidence. Mere popular links are insufficient. Automation collects public facts only. Capability tagging, pattern mapping and Tier‑A / Tier‑B promotion require human review. Run catalog validation, package checks and unit tests after changes.

## License

Apache‑2.0. The catalog stores classification metadata, public facts and upstream links only. No upstream source code is copied. Each referenced repository remains governed by its own license.

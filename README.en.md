# Proven Cloud Stack

An Agent Skill for choosing open-source cloud architectures. Give it the current environment, the outcome you need, and the constraints. It matches solution patterns, shortlists repositories from a bundled catalog, and then statically reviews only the strongest candidates.

This is not a GitHub Stars leaderboard. License, topology, runtime, and required capabilities are hard filters. Stars are compared only among repositories with the same solution pattern and role, where they serve as adoption evidence rather than proof of technical fit.

[中文说明](README.md)

## Install

Install the pinned release from your project directory with the `skills` CLI:

```bash
npx skills add https://github.com/836468211/cloud-architecture-skill/tree/v1.0.1/skills/choose-proven-cloud-stack
```

To target specific agents:

```bash
npx skills add https://github.com/836468211/cloud-architecture-skill/tree/v1.0.1/skills/choose-proven-cloud-stack --agent codex --agent claude-code --agent cursor
```

Node.js is needed only for this installer. Running the Skill requires Python 3.10 or newer. Offline catalog search uses the standard library; live repository checks also require Git and network access.

Codex users can alternatively ask `skill-installer` to install this URL without Node.js:

```text
Use skill-installer to install:
https://github.com/836468211/cloud-architecture-skill/tree/v1.0.1/skills/choose-proven-cloud-stack
```

Claude Code can also install the repository as a self-hosted plugin:

```bash
claude plugin marketplace add 836468211/cloud-architecture-skill --scope user
claude plugin install proven-cloud-stack@cloud-architecture-skill --scope user
```

This is a repository-hosted marketplace entry, not a claim of inclusion in Anthropic's official plugin directory.

## Example

The example below uses Codex's explicit invocation syntax. Other clients can trigger the Skill from the same natural-language request; the explicit Claude plugin command is `/proven-cloud-stack:choose-proven-cloud-stack`.

```text
Use $choose-proven-cloud-stack.

Environment: Java 21, Vue 3, MinIO, Kubernetes
Goal: concurrent resumable downloads for large files in the browser
Constraints: data must bypass the application server; prefer Apache-2.0; minimize components
```

The result covers the architecture and data path, candidate repositories and their roles, concrete rejection reasons, implementation concerns, and a measurable POC plan. Candidate repository code is never executed.

## Selection policy

1. Split composed problems before ranking, such as event logs, work queues, and CDC, or metrics, logs, and traces.
2. Apply hard compatibility gates before scoring.
3. Score relevance and maturity separately; popularity cannot repair a technical mismatch.
4. Default recommendations use Tier A/B only. Tier C is for discovery and genuine coverage gaps, and must be checked live before adoption.
5. Pin final evidence to a commit SHA and inspect source, tests, and configuration without executing upstream code.

See [`SKILL.md`](skills/choose-proven-cloud-stack/SKILL.md), [`scoring-model.md`](skills/choose-proven-cloud-stack/references/scoring-model.md), and [`source-policy.md`](skills/choose-proven-cloud-stack/references/source-policy.md) for the full contract.

## Catalog snapshot

Distribution release `v1.0.1` contains catalog snapshot `1.0.0` dated 2026-08-03:

| Item | Count |
|---|---:|
| Solution patterns | 34 |
| Repositories | 1,000 |
| Tier A: commit-pinned code review | 0 |
| Tier B: structured and metadata-verified | 58 |
| Tier C: discovery-only | 942 |
| GitHub metrics present | 983 |
| Metrics pending refresh | 17 |

The repository count is catalog coverage, not a claim that all 1,000 projects were manually reviewed. Tier C projects do not enter the default recommendation set. The exact counts are recorded in [`catalog-metadata.json`](skills/choose-proven-cloud-stack/references/catalog-metadata.json), and the frozen discovery run is in [`discovery-manifest.json`](skills/choose-proven-cloud-stack/references/discovery-manifest.json).

## Validate locally

```bash
python skills/choose-proven-cloud-stack/scripts/validate_catalog.py
python tools/validate_skill_package.py
python -m unittest discover -s tests -v
```

## License

Apache-2.0. The catalog stores this project's classifications, public repository facts, and upstream links; upstream repositories retain their own licenses.

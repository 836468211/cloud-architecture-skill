# Catalog schema

Keep static curated claims separate from dynamic GitHub facts. Store one compact JSON object per line and use UTF-8. The release tag identifies the distributed Skill package, `catalog_version` identifies its bundled data snapshot, and `schema_version` identifies the record format. These versions are independent.

## Contents

- [Version contract](#version-contract)
- [Project records](#project-records)
- [Discovery provenance](#discovery-provenance)
- [Repository roles](#repository-roles)
- [GitHub metric records](#github-metric-records)
- [Pattern records](#pattern-records)
- [Review records](#review-records)
- [Discovery manifest](#discovery-manifest)
- [Catalog metadata](#catalog-metadata)
- [Cross-file invariants](#cross-file-invariants)

## Version contract

The current release contract is:

- distribution release: `v1.0.1`;
- catalog version: `1.0.0`;
- record schema version: `1.0`;
- fixed snapshot date: `2026-08-03`;
- exactly 1,000 unique project records: 0 Tier A, 58 Tier B, and 942 Tier C;
- 98 preserved static records and 902 generated discovery records;
- 34 solution-pattern records.

Distribution release `v1.0.1` reuses the unchanged `1.0.0` catalog snapshot. A packaging or compatibility release does not imply that repository facts were refreshed.

Do not change record `schema_version` merely because a catalog release changes. Use SemVer for `catalog_version`; use a new record schema only for an incompatible data-shape change.

## Project records

Project records live in `projects-*.jsonl`. Static records remain in `projects-curated.jsonl` and `projects-discovery.jsonl`; generated v1 discovery records live in `projects-expanded.jsonl`.

Required fields:

```json
{
  "schema_version": "1.0",
  "repo_id": "github:owner/repo",
  "url": "https://github.com/owner/repo",
  "name": "Project",
  "primary_domain": "object-storage",
  "domains": ["object-storage", "file-transfer"],
  "cohort_id": "s3-javascript-sdk",
  "operations": ["download"],
  "problems": ["large-file-transfer"],
  "mechanisms": ["http-range"],
  "topologies": ["browser-to-object-storage"],
  "runtimes": ["browser"],
  "languages": ["typescript"],
  "protocols": ["s3-api", "http"],
  "limitations": [],
  "pattern_links": [
    {
      "pattern_id": "direct-object-storage-segmented-download",
      "roles": ["official-sdk", "direct-dependency"]
    }
  ],
  "summary": "Original factual summary.",
  "curation": {
    "tier": "B",
    "catalogued_at": "2026-08-03"
  }
}
```

Rules:

- Use `github:owner/repo` as the visible ID and retain GitHub `node_id` in metrics for rename detection.
- Require `primary_domain` to also appear in `domains`.
- Do not duplicate repositories across project files; use multiple `domains` and `pattern_links`.
- Put roles on pattern links because a repository can have different roles in different solutions.
- Never save calculated recommendation scores in project data.
- Write original summaries; do not copy upstream marketing text.
- Record known exclusions such as `upload-only`, `not-browser-sdk`, `discovery-only`, or `license-review-required`.
- Treat empty or absent dynamic facts as unknown; never encode unknown facts as zero.
- Do not promote a generated discovery record above Tier C without the review required by the source policy.

## Discovery provenance

Every generated record must identify the pattern used for balanced selection and every cached query that discovered it:

```json
{
  "curation": {
    "tier": "C",
    "catalogued_at": "2026-08-03",
    "source": "github-topic-discovery"
  },
  "discovery": {
    "assigned_pattern_id": "s3-compatible-object-storage",
    "provenance": [
      {
        "pattern_id": "s3-compatible-object-storage",
        "profile_id": "s3-object-storage",
        "query_id": "stable-query-id",
        "topic": "object-storage"
      }
    ]
  }
}
```

Each `query_id` must resolve to an entry in `discovery-manifest.json`. Multiple query hits for the same GitHub node are merged into one project. The generated classification is derived from the linked pattern and discovery profile; it is navigation metadata, not proof that the repository implements every linked mechanism. Keep `discovery-only` and `requires-code-review` in `limitations` until stronger evidence exists.

The builder must preserve static project files, reject incomplete query caches, exclude forks, mirrors, templates, archived or disabled repositories, protect GitHub node identity, and produce deterministic output from the same cache.

## Repository roles

Allowed roles:

- `direct-dependency`
- `official-sdk`
- `official-implementation`
- `reference-implementation`
- `mechanism-reference`
- `production-validation`
- `integration-adapter`
- `benchmark-testbed`
- `contrast-only`

Roles describe how a repository may be used as evidence; they are not quality tiers. A Tier C repository with a plausible role remains discovery-only.

## GitHub metric records

Dynamic records live in `github-metrics.jsonl`:

```json
{
  "repo_id": "github:owner/repo",
  "github_node_id": "opaque GitHub ID",
  "canonical_slug": "owner/repo",
  "stars": 1234,
  "forks": 100,
  "archived": false,
  "disabled": false,
  "is_fork": false,
  "default_branch": "main",
  "license_spdx": "Apache-2.0",
  "pushed_at": "2026-08-02T10:00:00Z",
  "fetched_at": "2026-08-03T00:00:00Z",
  "source": "github-rest-api",
  "status": "ok"
}
```

Use `null` with `status: pending-refresh` when a fact has not been fetched. Never encode unknown facts as zero. For `status: ok`, require a non-empty `github_node_id`, require `canonical_slug` to match `repo_id` case-insensitively, and keep node IDs unique across the catalog. Preserve the last verified snapshot when a refresh fails.

## Pattern records

Pattern records live in `patterns-core.jsonl` and require:

- `pattern_id` and original `name`;
- `domains`, `operations`, `solves`, and `topologies`;
- `required_mechanisms` and optional `recommended_mechanisms`;
- benefits and costs under `tradeoffs`;
- `validated_by` repository IDs;
- `status` such as `proven`, `established`, or `experimental`.

Pattern status and repository evidence tier are separate. A `proven` pattern should have at least two independent owners and one Tier A code review before the catalog presents that status as high-confidence evidence.

## Review records

Pinned Tier A reviews live in `reviews.jsonl`. The file is required even when it is empty. Catalog snapshot `1.0.0` has no Tier A repositories, so an empty file is truthful.

```json
{
  "review_id": "review-owner-repo-pattern-20260803",
  "repo_id": "github:owner/repo",
  "pattern_id": "pattern-id",
  "commit_sha": "0123456789abcdef0123456789abcdef01234567",
  "reviewed_at": "2026-08-03T00:00:00Z",
  "verdict": "supported",
  "supported_mechanisms": ["mechanism-id"],
  "limitations": [],
  "evidence": [
    {
      "claim": "The implementation retries bounded transient failures.",
      "kind": "test",
      "path": "tests/retry_test.py",
      "url": "https://github.com/owner/repo/blob/0123456789abcdef0123456789abcdef01234567/tests/retry_test.py"
    }
  ]
}
```

Require a unique `review_id`, a known repository and pattern, a 40-character hexadecimal Commit SHA, and non-empty evidence. Each evidence item requires `claim`, `kind`, and `path`; use an immutable GitHub URL pinned to the same SHA. Prefer source code and tests over README claims. Tier A requires at least one valid pinned review for the capabilities being claimed.

## Discovery manifest

`discovery-manifest.json` records how the generated snapshot was obtained. It is required for any release containing `projects-expanded.jsonl`.

```json
{
  "schema_version": "1.0",
  "snapshot_date": "2026-08-03",
  "cache_complete": true,
  "queries": {"planned": 68, "cached": 68, "items": 4202},
  "candidates": {
    "merged": 3631,
    "eligible": 3356,
    "excluded": 275,
    "exclusion_reasons": {
      "list-or-tutorial": 60,
      "missing-language": 186,
      "too-small": 29
    }
  },
  "selection": {
    "strategy": "pattern-round-robin",
    "owner_cap": 20,
    "target_total": 1000,
    "static": 98,
    "expanded": 902,
    "by_pattern": {
      "central-log-pipeline": 26,
      "cloud-native-security-scanning": 26,
      "columnar-analytical-database": 27,
      "content-addressed-backup": 27,
      "dag-workflow-scheduling": 27,
      "declarative-infrastructure-as-code": 27,
      "direct-object-storage-segmented-download": 27,
      "distributed-batch-stream-compute": 26,
      "distributed-in-memory-cache": 27,
      "distributed-search-engine": 27,
      "distributed-sql-replication": 27,
      "durable-execution-workflow": 27,
      "dynamic-secrets-management": 26,
      "ebpf-cloud-networking": 26,
      "event-driven-kubernetes-autoscaling": 26,
      "extensible-api-gateway": 27,
      "internal-developer-platform": 26,
      "kubernetes-control-loop": 27,
      "kubernetes-serverless-runtime": 26,
      "lakehouse-table-format": 26,
      "log-based-change-data-capture": 26,
      "low-latency-message-broker": 27,
      "ml-experiment-lifecycle": 26,
      "model-serving-platform": 26,
      "oidc-identity-provider": 26,
      "otel-telemetry-pipeline": 26,
      "policy-as-code-admission": 26,
      "pull-based-gitops": 27,
      "pull-metrics-monitoring": 26,
      "replicated-event-log": 27,
      "resumable-upload-protocol": 27,
      "s3-compatible-object-storage": 27,
      "sidecar-service-mesh": 27,
      "vector-similarity-service": 27
    }
  },
  "query_provenance": [
    {
      "query_id": "stable-query-id",
      "profile_id": "s3-object-storage",
      "pattern_id": "s3-compatible-object-storage",
      "topic": "object-storage",
      "fetched_at": "2026-08-03T00:00:00Z",
      "items": 75
    }
  ]
}
```

Require `cache_complete: true`, equal planned and cached query counts, the fixed snapshot date, and selection counts that reconcile with the project files. `query_provenance` is compact audit data; GitHub Search membership is not a capability claim.

## Catalog metadata

`catalog-metadata.json` is the machine-readable catalog summary. For snapshot `1.0.0` it must report:

```json
{
  "schema_version": "1.0",
  "catalog_version": "1.0.0",
  "generated_at": "2026-08-03T00:00:00Z",
  "projects": {
    "total": 1000,
    "tier_a": 0,
    "tier_b": 58,
    "tier_c": 942
  },
  "patterns": 34,
  "github_metrics": {
    "fresh": 983,
    "pending_or_missing": 17,
    "freshness_days": 30
  }
}
```

Generate metadata from the final project and metric files. Do not edit counts manually. Keep `projects` limited to `total`, `tier_a`, `tier_b`, and `tier_c`; keep the static/expanded split in `discovery-manifest.json.selection`. A release must fail package validation if version, snapshot date, tier totals, manifest selection totals, project-line counts, metric-line counts, or pattern-line counts differ.

## Cross-file invariants

- Project IDs and URLs are unique case-insensitively; one project has exactly one metric record.
- A successful metric node ID maps to exactly one repository slug.
- Every pattern link and review references an existing pattern and project.
- Every project `schema_version` is `1.0`.
- Tier B requires a verified GitHub metric snapshot; Tier A additionally requires pinned review evidence.
- Tier C is never a default recommendation solely because it has high Stars or a strong text match.
- `catalog-metadata.json`, `discovery-manifest.json`, and the JSONL files must reconcile exactly.
- Static curated claims must not be overwritten by discovery generation or metric refreshes.

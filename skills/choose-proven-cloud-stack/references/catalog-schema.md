# Catalog schema

Keep static curated claims separate from dynamic GitHub facts. Store one compact JSON object per line and use UTF-8.

## Project record

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
    "catalogued_at": "2026-07-29"
  }
}
```

Rules:

- Use `github:owner/repo` as the visible ID and retain GitHub `node_id` in metrics for rename detection.
- Do not duplicate repositories across domain files; use multiple `domains` and `pattern_links`.
- Put roles on pattern links because a repository can have different roles in different solutions.
- Never save calculated recommendation scores in project data.
- Write original summaries; do not copy upstream marketing text.
- Record known exclusions such as `upload-only`, `not-browser-sdk`, or `license-review-required`.

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

## GitHub metric record

Dynamic records live in `github-metrics.jsonl`:

```json
{
  "repo_id": "github:owner/repo",
  "github_node_id": "opaque GitHub ID",
  "canonical_slug": "owner/repo",
  "stars": 1234,
  "forks": 100,
  "archived": false,
  "default_branch": "main",
  "license_spdx": "Apache-2.0",
  "pushed_at": "2026-07-20T10:00:00Z",
  "fetched_at": "2026-07-29T08:00:00Z",
  "source": "github-rest-api",
  "status": "ok"
}
```

Use `null` with `status: pending-refresh` when a fact has not been fetched. Never encode unknown facts as zero.

## Pattern record

Required fields:

- `pattern_id` and original `name`;
- `domains`, `operations`, `solves`, and `topologies`;
- `required_mechanisms` and optional `recommended_mechanisms`;
- benefits and costs under `tradeoffs`;
- `validated_by` repository IDs;
- `status` such as `proven`, `established`, or `experimental`.

A `proven` pattern should ultimately have at least two independent owners and one Tier A code review. Until then, report catalog status and evidence confidence separately.

## Review record

Tier A expansion should add review records containing:

```text
review_id, repo_id, pattern_id, commit_sha, reviewed_at,
verdict, supported_mechanisms, limitations, evidence[]
```

Each evidence item must contain a claim, kind, path, and fixed GitHub URL using a 40-character Commit SHA. Prefer code and tests over README claims.

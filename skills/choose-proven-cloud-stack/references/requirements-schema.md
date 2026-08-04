# Requirement fingerprint

Represent the user's request with this compact JSON shape. Omit unknown optional fields; do not invent constraints.

```json
{
  "objective": "browser large-object download",
  "domains": ["object-storage", "file-transfer"],
  "operations": ["download"],
  "topologies": ["browser-to-object-storage"],
  "problems": ["large-file-transfer", "resume-after-interruption"],
  "required_mechanisms": ["http-range", "etag-if-range", "chunk-scheduler", "checkpoint"],
  "optional_mechanisms": ["presigned-url", "integrity-check"],
  "runtimes": ["browser", "jvm-backend", "kubernetes"],
  "languages": ["typescript", "java"],
  "roles": ["direct-dependency", "mechanism-reference", "production-validation"],
  "scale": {
    "object_size": "1 GB-100 GB",
    "concurrent_users": 500
  },
  "constraints": {
    "licenses_preferred": ["Apache-2.0"],
    "licenses_forbidden": [],
    "data_must_bypass_application_server": true,
    "self_hosted": true
  },
  "exclude": ["upload-only", "server-proxy-only"],
  "weights": {
    "performance": 0.30,
    "maturity": 0.25,
    "implementation_complexity": 0.20,
    "operations": 0.15,
    "client_compatibility": 0.10
  }
}
```

## Controlled dimensions

- `domains`: broad navigation only, such as `object-storage`, `database`, `messaging`, `observability`, or `identity`.
- `problems`: outcomes such as `resume-after-interruption`, `multi-region-failover`, or `high-cardinality-metrics`.
- `required_mechanisms`: implementation facts that must be present.
- `topologies`: important data paths such as `browser-to-object-storage`, `sidecar-mesh`, or `pull-based-gitops`.
- `runtimes`: execution environments, not just implementation languages.
- `roles`: the kind of evidence needed from a repository.
- `exclude`: negative requirements. Apply these as hard gates.

## Fingerprint rules

1. Expand user-facing features into mechanisms before catalog search.
2. Keep uncertain inferences out of `required_mechanisms`; put them in `optional_mechanisms`.
3. Separate adoption choice from study choice. A C++ downloader can be an implementation reference for a TypeScript client without being a direct dependency.
4. Treat language as a hard gate only for direct dependencies.
5. Ask one question only when topology, data ownership, or runtime ambiguity would select a different pattern.
6. Split a composed architecture into one fingerprint per capability family, run them independently, and combine their shortlists. Examples include event-log versus work-queue versus outbox/CDC, and metrics versus logs versus traces.
7. Treat `scale`, preference-style `weights`, and non-gating constraints as ADR context. The catalog CLI returns them in `unscored_requirement_fields`; apply them explicitly when comparing implementation complexity, benchmarks, and operations rather than assuming they changed repository scores.

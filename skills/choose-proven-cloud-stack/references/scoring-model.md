# Scoring model

Use scoring to order compatible candidates, not to conceal judgment.

## 1. Compatibility gates

Reject a candidate from the affected requested role when any hard incompatibility applies:

- operation conflicts, such as upload-only for a download-only request;
- topology conflicts, such as server-only for a browser direct-dependency request;
- forbidden license;
- archived repository when an actively maintained dependency is required;
- incompatible runtime or language for a direct dependency;
- missing required mechanism with verified negative evidence.

Apply runtime, language, topology, and archived-state gates to the direct-use role, rather than rejecting the whole repository. The same repository may remain eligible for another role: a native downloader can be rejected as a browser dependency but retained as an implementation reference.

## 2. Relevance score

Compute a 0-100 score from the available fingerprint dimensions:

| Dimension | Weight |
|---|---:|
| Problem and objective match | 25 |
| Required mechanism match | 30 |
| Topology match | 15 |
| Runtime and language match | 15 |
| Requested repository role | 10 |
| Optional mechanisms and text evidence | 5 |

Renormalize weights when a dimension is absent from the request. Required-mechanism coverage below 50% caps relevance at 59.

## 3. Maturity score

Compute maturity independently:

| Dimension | Weight |
|---|---:|
| Peer-normalized GitHub Stars | 35 |
| Recent maintenance and non-archived status | 20 |
| Forks and adoption signals | 10 |
| Catalog curation and pinned evidence tier | 15 |
| Pattern validated by independent repositories | 20 |

Normalize Stars with `log10(stars + 1)` and percentile-rank only among repositories sharing an exact `(solution pattern, evaluated role)` pair. Require at least 10 peers; otherwise fall back to a cohort or primary domain while preserving the evaluated role family. If every peer group remains below 10, leave the Star component unscored and report the insufficient peer count. Do not compare an official SDK globally with a general-purpose platform.

Leave missing maturity components unscored instead of renormalizing the remaining evidence into an inflated score, and reduce confidence. A project with no GitHub snapshot still receives partial credit for curation and pattern evidence rather than a forced zero. An archived project may still be a strong historical implementation reference but not a default dependency.

For independent validation, count distinct repository owners other than the candidate's own owner. Do not award a repository validation credit for citing itself.

## 4. Confidence

Track confidence separately:

- `high`: current metadata plus code, tests, or official documentation pinned to a commit or release;
- `medium`: curated metadata and multiple consistent sources, without current code review;
- `low`: discovery metadata, stale facts, or README-only claims.

Tier A starts high only when a valid review record pins the relevant implementation or tests to a 40-character Commit SHA. A label alone never grants high confidence. Tier B normally starts medium. Tier C starts low.

## 5. Review priority

After compatibility gates, calculate:

```text
review_priority = sqrt(relevance * maturity) * confidence_factor
```

Use confidence factors of `1.00`, `0.90`, and `0.75` for high, medium, and low. Require relevance of at least 60 and Tier A or B evidence for the default shortlist. Tier C candidates are returned separately for discovery and may fill a stated coverage gap only after live verification. The CLI retains candidates from 25 through 59 only as explicitly marked coverage-gap or comparison candidates; do not make them the primary recommendation without new evidence.

Select role-diverse evidence:

- at least one direct dependency or official implementation when available;
- at least one implementation reference for the hard mechanism;
- at least one production validation project when architecture risk is material.

Calculate maturity, peer Stars, confidence, and review priority separately for each role shortlist. Apply the same confidence factor to role and overall ordering.

## 6. Final recommendation

Do not mechanically select the largest number. Re-evaluate after live code review and report:

- relevance;
- maturity;
- confidence;
- peer group used for Star comparison;
- specific matching and missing evidence;
- reasons a lower-scoring candidate may be preferred.

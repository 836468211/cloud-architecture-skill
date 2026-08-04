# Catalog source and trust policy

## Tiers

### Tier A: reviewed

Require all of:

- canonical repository URL and compatible upstream license recorded;
- GitHub metadata verified with a timestamp;
- relevant implementation and tests inspected at a pinned commit;
- capabilities supported by code links or official specification links;
- limitations and rejected roles recorded.

### Tier B: curated

Require all of:

- canonical repository and basic metadata verified;
- original, concise classification into domains, roles, problems, and mechanisms;
- no unresolved hard contradiction between catalog claims and upstream documentation;
- explicit freshness timestamp.

Do not imply that Tier B code has been deeply reviewed.

### Tier C: discovery

Allow GitHub API and trusted landscape ingestion with minimal classification. Require live verification before making a primary recommendation.

Automated GitHub topic matches are hypotheses, not verified capability claims. Preserve their query provenance, exclude forks, mirrors, templates, archived or disabled repositories, and obvious lists or tutorials, then place them only in the discovery shortlist. Never promote an automatically generated record to Tier B or A.

## Evidence order

Prefer:

1. source code and tests pinned to a commit;
2. official specifications and project documentation;
3. reproducible benchmarks with complete workload context;
4. release and repository metadata;
5. maintainer statements and issue discussions;
6. third-party articles.

README claims alone are discovery evidence, not proof of performance or correctness.

## Metadata freshness

- Preserve `metrics_checked_at` on every project.
- Treat Stars and activity older than 30 days as stale for a final shortlist.
- Keep cached values when offline and label their date.
- Never invent or interpolate current metrics.
- Keep original upstream license identifiers and repository URLs.

## Inclusion and removal

Include projects because they implement or validate a solution pattern, not merely because they are popular. Deduplicate forks, mirrors, renamed repositories, and monorepo subprojects.

Use the GitHub node ID as the repository identity anchor. Reject a refresh when the node ID changes. When the same node moves to a new slug, update the curated project URL and ID deliberately before accepting refreshed facts; never let a redirect silently transfer capability labels to a different repository.

For large discovery snapshots, select within each solution-pattern cohort before filling by global popularity, and cap repeated owners inside a cohort. This prevents broad topics and generated repository families from displacing specialized projects. Record the exact query, snapshot timestamp, returned rank, and selection rule in the discovery manifest.

Flag rather than silently delete historically important archived projects. Exclude malware, obvious spam, repositories without a meaningful implementation, and entries whose identity cannot be verified.

Do not copy upstream source or README prose into the catalog. Store original summaries, factual metadata, and links.

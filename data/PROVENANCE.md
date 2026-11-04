# Preserved discovery assets

`indexes/casece.json` was copied byte-for-byte from the original project at `C:/Users/madse/Documents/parts-subagent`. It contains 1,339 catalogs observed by explicit discovery queries on 2026-09-07, from 11:35:28 to 12:03:54 UTC. Its coverage is partial. SHA-256: `6428086e3edca29ccf6edc73f3b0638801e563a8ef04af41232a74100bf638ae`.

Individual records retain source URLs, observation timestamps, decoded-content hashes, historical parser versions, and discovery queries. The snapshot is useful for catalog discovery; it does not establish current part availability, serial-specific fitment, or complete catalog coverage.

The frozen benchmark and its original manifest are under `evals/benchmark`. Original parser observations, hashes, and provenance are under `tests/fixtures/casece`; original discovery cases are under `tests/fixtures/discovery`. The benchmark's answer key and lineage are not inference inputs.

New storefront snapshots must identify observed coverage and preserve each record's evidence. Census refreshes retain existing records without changing timestamps unless that record was observed again.

`indexes/caseih.json` contains 23 catalogs observed on 2026-11-04 UTC through three explicitly scoped sitemap series: Magnum 7100, 1400-series combines, and RBX round balers. It is a partial snapshot, not a Case IH census claim. SHA-256: `34b4833f72fc1404bc10f0e1b0db13390687b45d2d48550ceaaa0d77ab94ba9b`. Its sanitized navigation and operation fixtures are in `tests/fixtures/caseih` with independent source hashes and observation dates.

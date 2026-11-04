# M0 fixtures

New selected source observations from 2026-09-06, not legacy acquisition data. HTML is reduced to structural fragments and allowlisted attributes; executable markup, images, inputs and handlers are removed. JSON keeps only pagination and catalog-evidence fields. Assembly HTML contains selected occurrences, not a complete assembly snapshot. Do not infer assembly completeness from it.

## Fixture identities

The original response names below are historical acquisition identifiers, not current file dependencies.

| Fixtures | Original response metadata identifier |
|---|---|
| `machines.html` | `machines-1.json` |
| `model.html` | `model-1.json` |
| `description.json` | `description-1.json` |
| `figure.json` | `figure-1.json` |
| `number.json` | `number-1.json` |
| `negative.json` | `negative-1.json` |
| `assembly-selected.html` | `assembly-1.json` |
| `sub-from.html` | `relationships-positive-1.json` |
| `sub-to.html` | `relationships-forward-1.json` |
| `empty-alts.html` | `relationships-1.json` |
| `capped-metadata.json` | `capped-diagnostic.json`; actual count/size metadata only, no 500-row copy |

## Integrity and use

`manifest.json` fingerprints the sanitized fixture bytes. The production parser and adapter tests consume these files directly. Preserve their bytes; JSON and source fragments remain unchanged to keep the manifest valid. Original decoded-body hashes differ intentionally from sanitized fixture hashes. The tests synthesize malformed, inconsistent, and oversized responses locally; those synthetic cases are not live observations. Retired acquisition scripts and raw scratch inputs are not needed to run the suite.

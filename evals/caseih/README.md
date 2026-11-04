# Case IH evidence suite

These 25 questions were authored during implementation against captured US English Case IH assembly pages: nine Magnum 7110 tractor questions, eight International Harvester 1420 combine questions, and eight RBX451 round-baler questions. They exercise ordinary components, dimensions, left/right distinctions, engine applicability, cylinder-specific repair kits, and premium versus economy supply options.

`queries.jsonl` contains only IDs and questions and is the only file supplied to inference. `answers.jsonl` and `lineage.jsonl` copy the relevant facts and source identities from inspected fixtures. `manifest.json` freezes all three files. `scripts.author_caseih_suite` contains the handwritten specifications and refuses to replace existing suite files.

The authoring agent inspected descriptions, notes, callouts, and quantities when selecting each reference. This is an exposed evidence-recovery suite, not an independent accuracy holdout. Do not infer universal fitment, complete coverage, current stock, or interchangeability. The fan page includes both 7110 and 7120 rows; cylinder parts must remain attached to their specified parent cylinder. Source wording such as “NSS,” “NLS,” and “REF” is retained without creating an orderable part reference.

Run explicitly with `uv run python -m evals.run --live --cases evals/caseih/queries.jsonl --out reports/caseih-YYYY-MM-DD.jsonl`. Score with the separate benchmark scorer using this directory's answer file. A dated semantic review must assess the actual returned claims before reporting the 20/25 acceptance gate as passed; reference recovery alone is insufficient.

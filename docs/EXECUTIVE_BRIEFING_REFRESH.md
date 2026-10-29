# Executive briefing refresh after a bake-off

Status: proposal; this process is not implemented. `executive-special.html` is currently a manually maintained snapshot, so a new bake-off does not update it automatically.

## Proposed process

1. Produce the comprehensive report and check that the comparison is complete and the evidence needed for each proposed claim is available. Incomplete, paused or failed runs must not replace the current briefing.
2. Explicitly select the report to publish in the briefing. If a later re-score supersedes it, select that version deliberately; do not assume the newest file or completion event is the authoritative report.
3. Generate a preview from that single report, including the assessment date, scope, statistics and report links throughout the page. Retain denominators, exclusions, missing or unresolved evidence, and the relevant limitations of cost, latency and safety comparisons.
4. Review the business narrative against the report. A fixed template or agent-generated summary must not invent a winner or turn missing evidence into a pass. Decide whether this review is performed by an agent or requires human approval before publication.
5. Validate the preview and its links, then replace the published snapshot atomically. Record the selected report and version in the output so the briefing's source is clear. If selection, generation or validation fails, retain the last published briefing and report the failure.

The refresh should cover the entire page, including headline counts, dates, fairness and decision wording, not only the results cards. It must not mix data from different report versions. The process is intended to automate grounded facts and links while keeping the interpretation appropriate to the actual results.

## Until implemented

After a completed bake-off, ask an agent to update the briefing from the selected final report, check the findings and limitations, and refresh all dates and links. Review the result before publishing.

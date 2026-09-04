# Fixed Gate Decisions

Use these decisions for every comparable run.

| Gate | Decision | Field/comment |
| --- | --- | --- |
| Graphify update | `Use existing` for Graphify variants; `Continue without` otherwise | Empty |
| Requirements | Approve | Empty |
| Layer selection 1 | Select | `L10-domain` |
| Layer todo 1 | Agent authors approved test | Empty |
| Red evidence 1 | Confirm expected red | Empty |
| Layer approval 1 | Approve and choose another | Empty |
| Layer selection 2 | Select | `L20-service` |
| Layer todo 2 | Agent authors approved test | Empty |
| Red evidence 2 | Confirm expected red | Empty |
| Layer approval 2 | Approve and choose another | Empty |
| Layer selection 3 | Select | `L30-http` |
| Layer todo 3 | Agent authors approved test | Empty |
| Red evidence 3 | Confirm expected red | Empty |
| Layer approval 3 | Approve and run final review | Empty |
| Memory | Finish without capture | Empty |

Any revision, checkpoint, retry, different layer split, or different order makes the run diagnostically useful but not directly comparable.

For a materialized `one-layer-diagnostic`, follow the same decisions through
`L10-domain`, then choose `Stop this slice` at its layer approval gate. Do not
continue to final review or memory capture. The runner performs this route
automatically, and the result is not eligible for an end-to-end claim.

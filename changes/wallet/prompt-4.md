Add a fee estimation endpoint to services/wallet using Bitcoin Core.

Goal:
Expose low/medium/high fee estimates in sat/vB for frontend transaction-building UX.

Requirements:
- Add a new authenticated wallet endpoint for fee estimates.
- Use Bitcoin Core RPC fee estimation primitives.
- Return a simple response shape with low, medium, and high fee suggestions in sat/vB.
- Define what each tier means in terms of target confirmation windows, and document that in code comments.
- Validate and normalize values safely.
- If Bitcoin Core cannot provide estimates, return safe fallbacks or a clear error according to project conventions.

Suggested response example:
{
  "fees": {
    "low": {"sat_per_vb": 2, "target_blocks": 12},
    "medium": {"sat_per_vb": 5, "target_blocks": 6},
    "high": {"sat_per_vb": 8, "target_blocks": 2}
  }
}

Implementation details:
- Add request/response schemas.
- Add unit tests for:
  - successful estimate mapping
  - missing estimate fallback behavior
  - RPC failure handling
  - authenticated access
- Record a business event for this endpoint.

At the end, summarize the endpoint path and response contract.
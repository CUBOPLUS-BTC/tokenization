Add a QR code generation endpoint to services/wallet.

Goal:
Provide a simple endpoint that turns a text payload into a QR code image for wallet receive/payment flows.

Requirements:
- Add an authenticated endpoint that accepts a text string.
- Return a PNG image response.
- Validate input size and reject unreasonably large payloads.
- Keep the implementation small and deterministic.
- Set the correct Content-Type header.
- Do not overcomplicate with styling; a standard QR is enough.

Implementation suggestions:
- Add a minimal dependency if needed, but keep the dependency footprint reasonable.
- If the project prefers returning binary directly, do that.
- If the project prefers returning base64 in JSON, only do that if it fits existing API conventions better. Binary PNG is preferred unless existing conventions strongly suggest otherwise.

Add tests for:
- successful PNG response
- invalid or oversized input
- non-empty output bytes
- auth enforcement

At the end, summarize the new endpoint and any dependency added.
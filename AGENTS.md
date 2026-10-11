# Development

This is a standalone adapter project. Use MAF and Codex SDK as dependencies.
Start by verifying SDK authentication and session behavior, then implement a MAF custom agent.

- Keep target repositories outside this adapter repository.
- Use ChatGPT authentication for the feasibility checks; fail if a different auth mode is active.
- Do not read, copy, print, or commit authentication tokens.
- Do not silently fall back to API-key authentication.
- Live checks consume Codex plan usage. Unit tests must not start Codex.
- Keep runtime state under ignored `.state/`; record only non-secret test results in docs.
- Distinguish working code, tested behavior, and planned features in documentation.

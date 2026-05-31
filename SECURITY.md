# Security policy

## Reporting a vulnerability

If you find a security issue, **please do not file a public GitHub
issue.** Email the maintainers privately at the address listed in
the GitHub repository profile, or use GitHub's "Report a vulnerability"
private flow (Security tab → Report).

Include enough detail to reproduce: affected version, run mode
(Mac/Linux/Docker/prod), the request or input that triggers it, and
the observed effect. A proof-of-concept is appreciated but not
required.

We aim to acknowledge reports within **3 business days** and have a
fix or mitigation plan within **30 days** for high-severity issues.

## Threat model

Casual-SST is designed for two threat contexts:

| Context | Threats in scope | Out of scope |
|---|---|---|
| **Local dev** (`make dev-mac` / `dev-linux` / `dev-docker`) | None; assume the developer trusts their own machine | Network attacks, host compromise |
| **Production** (`make prod`) | Hostile WS clients, bogus JWTs, malformed audio, prompt injection via initial-prompt, abuse of `/api/summarize` (cost) | Physical attacks on the GPU host, OS-level privilege escalation, supply-chain attacks on PyPI dependencies |

## Security posture

### Authentication

- **JWT on `/ws/{meeting_id}`** when `bypass_auth = false`
  (the default in `config/prod.yaml`). Two acceptance modes:
  - ASAP (Jitsi-style) — RS256/RS512/ES256/ES384 via public PEM keys
    loaded from `ASAP_PUB_KEYS_FOLDER`, keyed by `kid`. Audience
    must appear in `ASAP_PUB_KEYS_AUDS`.
  - HMAC fallback — `JWT_SECRET` + `JWT_ALGORITHM` (default HS256).
- **`X-Admin-Token` header on `/admin/api/*`** — static shared
  secret in `ADMIN_TOKEN`. The dashboard HTML page itself is
  Basic-Auth gated (browser prompts on load).
- **`/metrics` is unauthenticated.** Counters only, no PII, no
  transcripts. Gate behind a reverse proxy if you don't want it
  public.
- **`/healthz` and `/health` are unauthenticated.** Required for
  load balancers / orchestrators.

### What we store

- **No persistent transcript storage.** All audio + finalized text
  lives in process memory and is discarded when the WebSocket
  closes. Operators who need durable transcripts have to add their
  own sink.
- **Per-participant profile** (recent finals, language stats) lives
  in memory for the duration of the meeting only.
- **In-memory log ring buffer** retains the last 2000 log lines —
  these can contain participant IDs but **not** transcript text
  unless an external sink injects them.

### Defaults that protect users

- `bypass_auth: false` in `prod.yaml`.
- `condition_on_previous_text=False` hard-coded in Whisper backends
  to prevent prior text from snowballing into hallucinations or
  cross-tenant leakage in shared deployments.
- Admin portal returns 404 unless `ADMIN_TOKEN` is explicitly set —
  no accidental exposure if you forget to configure it.
- `/admin/api/config` redacts any key whose name contains
  `token`, `secret`, `password`, `credential`, `api_key`.
- Audio is processed in memory; nothing written to disk unless an
  operator opts in (e.g. by adding a logging sink).

### Known limits

- **No rate limiting on `/api/summarize`.** A misconfigured client
  can drive your OpenAI bill. Put a reverse proxy / API gateway in
  front for public exposure.
- **No mutual TLS / client cert auth.** Out of scope; use a proxy.
- **Prompt-injection through the rolling `initial_prompt`.** A
  malicious participant could try to plant tokens that bias the next
  participant's transcription. We restrict the prompt to recent
  high-confidence finals from the *same participant*, which contains
  the blast radius to that participant's own text. Cross-participant
  prompt leakage is not possible by design (per-participant profile,
  ADR-006).

## Supported versions

We patch security issues in the latest minor release line on Docker
Hub (`schnsrw/casual-sst:latest` and `:cuda-latest`). Older minor
releases are best-effort.

| Version | Supported |
|---|---|
| 0.0.x | ✅ |
| < 0.0.x | n/a |

## Disclosure timeline

For high-severity issues, our standard timeline is:

1. **T+0** — Report received, maintainer acknowledges.
2. **T+3 days** — Triage + initial assessment shared with reporter.
3. **T+14 days** — Fix in main, image published as a patch release.
4. **T+30 days** — Public advisory published (GHSA + CHANGELOG entry).

For lower-severity issues we may bundle the fix into the next planned
release rather than cutting a hotfix.

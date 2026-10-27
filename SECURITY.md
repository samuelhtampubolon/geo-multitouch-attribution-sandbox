# Security Policy

## Reporting a vulnerability
Please open a private security advisory on GitHub (Security tab, "Report a vulnerability") instead of a public issue. Include reproduction steps and affected versions. You can expect an acknowledgement within 7 days.

## Threat model and controls

| OWASP API 2023 | Threat | Control | Tests |
| :--- | :--- | :--- | :--- |
| API1 BOLA | Tenant A reads or writes tenant B's journeys, audits or results | Token `tenant_id` must equal the requested `tenant_id` on every route (constant-time compare); denial does not echo the probed tenant | `test_api_integration.py::test_bola_cross_tenant_request_is_forbidden` |
| API2 Broken authentication | Anyone mints a token for any tenant; forged or replayed tokens | Client-secret exchange, HS256 pinned, required `iss/aud/exp/nbf/iat/jti/sub/tenant_id/role`, 60 min lifetime, 10/min limit on the token route | `test_jwt_*`, `test_token_requires_valid_client_secret`, `test_auth_endpoint_is_rate_limited` |
| API3 Property-level authorization | Mass assignment of `is_admin`, `role`, unknown fields | `extra='forbid'` on every schema, `Literal` enums, strict tenant ID pattern | `test_every_request_schema_forbids_extra_fields`, `test_mass_assignment_rejected_over_http` |
| API4 Unrestricted resource consumption | Huge bodies, combinatorial Shapley, long paths, bootstrap abuse | 1 MB body cap (413), 120/min global and 30/min compute limits, path <= 25, journeys <= 10,000, channels <= 20, order <= 3, bootstrap <= 500, Shapley closed form above 12 channels | `test_oversized_body_rejected_with_413`, `test_resource_bounds_cap_combinatorial_inputs` |
| API5 Function-level authorization | Self-issued `admin` tokens, viewers writing data | Role allow-list (`analyst`, `viewer`), tokens with other roles rejected, viewers cannot persist | `test_privileged_roles_cannot_be_minted`, `test_viewer_role_cannot_persist` |
| API7 SSRF | Brand URLs pointing at cloud metadata or internal services | `https` and port 443 only, no userinfo, forbidden characters, internal hostname deny-list, every DNS answer must be globally routable, IPv4 embedded in IPv6 (mapped, NAT64, 6to4, Teredo) unwrapped, CGNAT and IMDSv6 blocked, resolved IPs returned for DNS pinning | `test_ssrf_*` (30+ cases) |
| API8 Misconfiguration | Verbose errors, permissive CORS, missing headers, docs in production | Generic 500/503 bodies, CORS allow-list without credentials, CSP / HSTS / nosniff / COOP / Permissions-Policy / no-store, docs disabled in production, non-root container with dropped capabilities | `test_security_headers_present`, `test_cors_allows_only_listed_origins` |
| Injection / XSS | Script injection through brand names or JSON-LD | Plain-text sanitizer (nested entities, dangerous blocks, bidi controls), JSON-LD escapes `< > &` U+2028/2029 and bidi characters | `test_sanitize_text_neutralizes_payloads`, `test_jsonld_script_breakout_is_impossible` |
| Supply chain / code review | Trojan Source characters, vulnerable dependencies | Test forbids literal bidi / zero-width characters in sources; bandit and pip-audit in CI; Dependabot | `test_sources_contain_no_trojan_source_characters`, CI `security` job |

## Static GitHub Pages site
The demo page is fully static: no cookies, no analytics, no network requests after load (`connect-src 'none'`). A meta Content-Security-Policy allows only the SHA-256-hashed inline script and style blocks plus Google Fonts. The UI never assigns HTML strings to the DOM and has no inline event handlers; `tests/test_pages_build.py` enforces both.

## Secrets
- Never commit `.env`. `.env.example` contains no usable secrets.
- `APP_ENV=production` requires `JWT_SECRET` (>= 32 characters) and `TENANT_CLIENT_SECRETS` (>= 16 characters each); the app refuses to start otherwise.
- Development without `JWT_SECRET` uses a random per-process key, so tokens never survive restarts and no default key exists.
- `docker-compose.yml` reads secrets from the environment and fails fast when they are missing.

## Known limitations
- The GEO auditor simulates engine responses deterministically; it performs no outbound requests. `resolve_and_validate_url` returns resolved IPs so a future fetcher can pin them against DNS rebinding.
- Rate limits use in-memory storage; use a shared backend (for example Redis via `storage_uri`) when running several workers.

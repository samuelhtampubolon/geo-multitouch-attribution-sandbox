# Architectural Decision Records (ADR)

## Project: Generative Engine Optimization (GEO) & Multi-Touch Attribution Sandbox
**Status**: Accepted & Implemented  
**Date**: 2026-10-27  

---

### ADR-001: First-Order Absorbing Markov Chain for Marketing Attribution
- **Context**: Traditional marketing attribution (Last-Click, First-Touch) fails in modern search journeys where generative engines (ChatGPT, Perplexity) and virtual influencers educate consumers high in the funnel, while bottom-of-funnel retargeting ads capture the final click.
- **Decision**: Model customer journeys as an absorbing Markov chain with states $S = \{\text{Start}\} \cup \text{Channels} \cup \{\text{Conversion}, \text{Null}\}$. Calculate channel value through the **Total Removal Effect**:
  $$R_k = 1.0 - \frac{P(\text{Conversion} \mid \text{Graph without } k)}{P(\text{Conversion} \mid \text{Full Graph})}$$
  Attribution weight is normalized: $W_k = \frac{R_k}{\sum R_j}$.
- **Consequences**: Enables mathematically robust credit allocation to top-of-funnel discovery channels without relying on ad-hoc heuristics. Vectorized NumPy matrix inversion ($(I - Q)^{-1}$) ensures sub-100ms execution.

---

### ADR-002: Exact Shapley Value Attribution with Combinatorial Safeguards
- **Context**: Cooperative game theory provides an axiomatic foundation for fair credit sharing ($\phi_k$) satisfying efficiency, symmetry, dummy player, and additivity.
- **Decision**: Implement exact Shapley values for subsets of marketing channels:
  $$\phi_k(v) = \sum_{S \subseteq N \setminus \{k\}} \frac{|S|! (|N| - |S| - 1)!}{|N|!} [v(S \cup \{k\}) - v(S)]$$
  To mitigate denial-of-service (combinatorial explosion $2^N$), channel cardinality is strictly capped at $N \le 12$.
- **Consequences**: Guarantees attribution weights sum exactly to 1.0 (efficiency) while preventing memory exhaustion attacks.

---

### ADR-003: Share of Model (SoM) Logarithmic Rank Decay & Sentiment Multipliers
- **Context**: Generative engines present synthesized answers where citation rank and tone dictate click-through rates.
- **Decision**: Adopt the governing formula:
  $$\text{SoM} = \frac{1}{M} \sum_{i=1}^C w_i \cdot \frac{1}{\log_2(\text{rank}_i + 1)} \cdot (1.0 + 0.5 \cdot s_i)$$
  where $w_i = 1.0$ if cited, $\text{rank}_i$ is citation position, and $s_i \in [-1.0, 1.0]$ is sentiment polarity.
- **Consequences**: Accurately reflects that rank #1 citations have significantly higher visibility than rank #4, and negative citations discount visibility.

---

### ADR-004: Deterministic Local Offline Simulation Mode
- **Context**: Developers, CI/CD pipelines, and auditors need to run tests and simulations without incurring external LLM API costs or facing rate limits.
- **Decision**: Provide pre-seeded corpora of 500 multi-touch journeys and 100 generative prompt evaluations across 4 major engines (ChatGPT, Perplexity, Claude, Gemini).
- **Consequences**: The system operates with zero external network dependencies, ensuring reproducible benchmarks.

---

### ADR-005: Defense-in-Depth AppSec & SSRF Prevention
- **Context**: Ingesting user-supplied brand URLs for citation auditing introduces Server-Side Request Forgery (SSRF) vulnerabilities against cloud metadata endpoints (`169.254.169.254`) and internal subnets.
- **Decision**:
  1. Enforce strict `https://` scheme validation.
  2. Resolve domain names via `socket.getaddrinfo` and block all private (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback (`127.0.0.0/8`), link-local, and metadata IP spaces.
  3. Enforce Pydantic v2 `extra='forbid'` to prevent Mass Assignment attacks.
  4. Enforce tenant ID verification on all data access to eliminate Broken Object Level Authorization (BOLA).
- **Consequences**: Complies with OWASP API Security Top 10 standards.

---

### ADR-006: Order-k Markov Chains Solved by Linear Systems
- **Context**: First-order chains cannot express sequence effects (AI Search before vs after Paid Social). The v1 solver inverted (I - Q) explicitly.
- **Decision**: Generalize states to the last k touches (k = 1..3, capped for DoS), aggregate identical journeys, build transition counts with one `np.bincount`, and solve (I - Q) b = r with `np.linalg.solve` (pseudo-inverse fallback). Removing channel k redirects every state containing k to NULL.
- **Consequences**: Sub-10 ms on the 500-journey corpus for k = 3 (136 states). Flow conservation (chain conversion rate = observed rate, expected visits = observed touches) holds for every k and is enforced by tests.

### ADR-007: Shapley Closed Form Instead of Channel Truncation
- **Context**: v1 silently truncated the channel list above 12 channels, so some channels received no credit at all.
- **Decision**: The characteristic function v(S) = value of converted journeys with channel set inside S is a sum of unanimity games, so phi_k = sum over T containing k of value(T)/|T| exactly. Enumerate 2^N coalitions up to 12 channels (as an independent check) and use the closed form above.
- **Consequences**: Exact for any N in O(journeys); tests prove enumeration and closed form agree and that efficiency, symmetry, dummy and additivity hold.

### ADR-008: Concave Budget Optimization with Marginal-ROAS Equalization
- **Context**: The v1 rule Budget_k proportional to W_k * sqrt(ROAS_k) had no economic optimality property and ignored saturation.
- **Decision**: Model revenue_k(x) = a_k x^beta (0 < beta < 1) calibrated to Markov-attributed revenue at current spend, maximize total revenue subject to the budget and a trust region [(1 - delta) s_k, (1 + delta) s_k], and solve the KKT conditions by bisection on the equilibrium marginal ROAS lambda*.
- **Consequences**: Unique global optimum, never worse than the current mix, interpretable lambda*. Channels are classified by marginal ROAS relative to lambda*.

### ADR-009: Uncertainty and Model-Disagreement Reporting
- **Decision**: Report Wilson score intervals for citation rates, multinomial bootstrap percentile intervals for Markov shares, Jensen-Shannon divergence and total variation between every model and Markov, HHI of share of voice and engine entropy.
- **Consequences**: Decision makers see whether a difference is signal or sampling noise, and how much credit Last-Click misplaces (46.4% on the seeded corpus).

### ADR-010: Client-Side GitHub Pages Lab with Verified Parity
- **Context**: The v1 page displayed hard-coded "simulated" attribution numbers and built HTML strings from user input (DOM XSS).
- **Decision**: Port the engine to `web/engine.js`, generate the page with `scripts/build_pages.py`, embed Python reference results, and verify parity live in the browser and under Node in CI. Build DOM nodes with `textContent` only and ship a hash-based CSP with `connect-src 'none'`. Reference floats are rounded to 12 significant digits so the build is identical across Python 3.11 to 3.13.
- **Consequences**: The demo shows real mathematics, works offline, and any drift between the two engines fails CI.

### ADR-011: Authentication, Abuse Limits and Secret Handling
- **Context**: v1 issued a JWT for any tenant and any role (including `admin`) without credentials, never attached its rate limiter, combined `CORS *` with credentials, and committed JWT secrets.
- **Decision**: Tenant client secrets with constant-time comparison; JWT with iss/aud/nbf/jti and role allow-list; SlowAPI middleware plus stricter per-route limits; 1 MB body cap; CORS allow-list without credentials; production refuses weak or missing secrets; development uses an ephemeral key.
- **Consequences**: Closes broken authentication, privilege escalation and unbounded-consumption paths. Documented in SECURITY.md and covered by 79 security and API tests.

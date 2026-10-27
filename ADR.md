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

#!/usr/bin/env python3
"""
Builds the GitHub Pages site (index.html and docs/index.html) from web/.

- Inlines web/styles.css, web/engine.js and web/app.js into web/template.html.
- Embeds the deterministic 500-journey corpus plus reference results computed by the
  Python engine, so the browser can prove JavaScript/Python parity live.
- Generates a strict Content-Security-Policy with SHA-256 hashes of every inline script
  and style (no 'unsafe-inline', no eval, no network access after load).

Usage:
    python scripts/build_pages.py            # collect test counts, then build
    python scripts/build_pages.py --no-collect
    python scripts/build_pages.py --check    # exit 1 if committed pages are stale
"""

from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_ENV", "development")

from src.config import settings  # noqa: E402
from src.schemas import CustomerJourneyInput  # noqa: E402
from src.security import safe_json_for_script, sanitize_text, validate_and_sanitize_url  # noqa: E402
from src.seed_journeys import INITIAL_SPENDS, generate_journeys  # noqa: E402
from src.services.budget_optimizer import optimize_budget  # noqa: E402
from src.services.markov_shapley_mta import MultiTouchAttributionService as MTA  # noqa: E402
from src.services.quant_metrics import jensen_shannon_bits, total_variation  # noqa: E402

WEB = ROOT / "web"
OUTPUTS = [ROOT / "index.html", ROOT / "docs" / "index.html"]
TEST_SUMMARY = WEB / "test_summary.json"
DEFAULT_BUDGET = 22500.0

TEST_COVERAGE = {
    "test_attribution_math.py": "Markov (hand-derived, value iteration, flow conservation, order-k), Shapley axioms & closed form, rule models, pipeline",
    "test_quant_and_budget.py": "Wilson CI, HHI, entropy, JSD/TV, KKT optimality, bounds, closed form, global optimum",
    "test_geo_and_jsonld.py": "SoM bounds & recomputation, share of voice, determinism, JSON-LD escaping and URL safety",
    "test_ssrf_and_security.py": "SSRF bypasses (IPv6 tunnels, DNS, userinfo, ports), sanitizer, JWT forgery, roles, config",
    "test_api_integration.py": "Auth, BOLA, rate limiting, 413 body limit, CORS, headers, end-to-end endpoints",
    "test_dashboard_smoke.py": "Streamlit Control Tower renders headlessly with computed KPIs",
    "test_pages_build.py": "Pages build is current, CSP hashes, no innerHTML, Node JS/Python parity",
}

# URLs whose verdict does not depend on DNS, used for SSRF parity checks
SSRF_PARITY_URLS = [
    "https://169.254.169.254/latest/meta-data/", "http://169.254.169.254/", "https://127.0.0.1/admin",
    "https://10.0.0.1/", "https://172.16.0.5/", "https://192.168.1.1/", "https://100.64.0.1/", "https://0.0.0.0/",
    "https://198.18.0.1/", "https://224.0.0.1/", "https://255.255.255.255/", "https://192.0.2.10/", "https://203.0.113.5/",
    "https://93.184.215.14/", "https://140.82.112.3/path?q=1&r=2", "https://8.8.8.8/",
    "https://[::1]/", "https://[::ffff:127.0.0.1]/", "https://[::ffff:169.254.169.254]/", "https://[64:ff9b::a9fe:a9fe]/",
    "https://[2002:a9fe:a9fe::]/", "https://[fd00:ec2::254]/", "https://[fe80::1]/", "https://[fc00::1]/",
    "https://[2001:db8::1]/", "https://[2606:4700:4700::1111]/",
    "http://example.com/", "ftp://example.com/", "javascript:alert(1)", "https://user:pw@example.com/",
    "https://example.com@127.0.0.1/", "https://example.com:8443/", 'https://example.com/"><script>',
    "https://example.com/a b", "https://localhost/", "https://metadata.google.internal/", "https://svc.internal/",
    "https://printer.local/",
]

SANITIZE_PARITY_INPUTS = [
    "<script>alert('xss')</script>Apex Developer Suite",
    '<img src=x onerror="alert(1)">Brand',
    "<svg onload=alert(1)><circle/></svg>Brand",
    "<iframe src=javascript:alert(1)></iframe>Brand",
    "&lt;script&gt;alert(1)&lt;/script&gt;Brand",
    "&amp;lt;script&amp;gt;alert(1)&amp;lt;/script&amp;gt;Safe",
    "<scr<script>ipt>alert(1)</script>Brand",
    "Brand\u202eevil",
    "Bra\u200bnd\x00",
    'Tom & Jerry "quoted"',
    "<!-- hidden -->Visible",
    "a < b > c",
    "<b>bold</b> and <a href='x'>link</a>",
    "x<y",
]


def journeys() -> list[CustomerJourneyInput]:
    return [
        CustomerJourneyInput(
            session_hash=row["session_hash"], path=row["path"], converted=row["converted"],
            conversion_value_usd=row["conversion_value_usd"],
        )
        for row in generate_journeys(settings.DEFAULT_TENANT_ID)
    ]


def aggregate(js: list[CustomerJourneyInput]) -> list[dict]:
    """Weighted records {p, c, n, v} for the browser engine (journeys with equal path/outcome merged)."""
    counts: Counter = Counter()
    values: Counter = Counter()
    for j in js:
        key = (tuple(j.path), int(j.converted))
        counts[key] += 1
        values[key] += j.conversion_value_usd
    return [
        {"p": list(path), "c": conv, "n": counts[(path, conv)], "v": round(values[(path, conv)], 2)}
        for path, conv in sorted(counts)
    ]


def reference(js: list[CustomerJourneyInput], channels: list[str]) -> dict:
    ref: dict = {"markov": {}}
    for order in (1, 2, 3):
        shares, effects, _, base = MTA.compute_markov_attribution(js, channels, order=order)
        ref["markov"][str(order)] = {"base": base, "shares": shares, "effects": effects}
    visits, path_length = MTA.markov_expected_visits(js, channels, order=1)
    ref["visits"], ref["path_length"] = visits, path_length
    ref["shapley_phi"] = MTA.compute_shapley_values(js, channels, method="exact")
    lc, ft, lin = MTA.compute_heuristic_attributions(js, channels)
    pb, td = MTA.compute_positional_attributions(js, channels, half_life=2.0)
    ref["rules"] = {"Last-Click": lc, "First-Touch": ft, "Linear": lin, "Position-Based": pb, "Time-Decay": td}
    shapley_shares = MTA.compute_shapley_attribution(js, channels)
    markov = ref["markov"]["1"]["shares"]
    ref["divergence"] = {
        name: {"tv": total_variation(dist, markov), "jsd": jensen_shannon_bits(dist, markov)}
        for name, dist in {**ref["rules"], "Shapley": shapley_shares}.items()
    }
    revenue = sum(j.conversion_value_usd for j in js if j.converted)
    opt = optimize_budget({c: markov[c] * revenue for c in channels}, dict(INITIAL_SPENDS), DEFAULT_BUDGET, beta=0.5, trust_region=0.5)
    ref["optimizer"] = {
        "budget": DEFAULT_BUDGET, "beta": 0.5, "trust": 0.5, "allocation": opt.allocation,
        "lambda": opt.equilibrium_marginal_roas, "revenue_now": opt.projected_revenue_current,
        "revenue_opt": opt.projected_revenue_optimized,
    }
    ref["ssrf"] = [[url, _blocked(url)] for url in SSRF_PARITY_URLS]
    ref["sanitize"] = [[text, sanitize_text(text)] for text in SANITIZE_PARITY_INPUTS]
    return ref


def stable_floats(value):
    """
    Rounds floats to 12 significant digits so the build is byte-identical across Python / NumPy
    versions (Python 3.12 switched sum() to compensated summation, changing the last ulp).
    The rounding error (<= 5e-13 relative) is far below every parity tolerance.
    """
    if isinstance(value, float):
        return float(f"{value:.12g}")
    if isinstance(value, dict):
        return {k: stable_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [stable_floats(v) for v in value]
    return value


def _blocked(url: str) -> bool:
    from fastapi import HTTPException
    try:
        validate_and_sanitize_url(url)
        return False
    except HTTPException:
        return True


def collect_test_summary() -> dict:
    """Counts collected pytest items per file (run with --collect-only, no tests executed)."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True, check=True,
    ).stdout
    per_file: Counter = Counter()
    for line in out.splitlines():
        if "::" in line:
            per_file[Path(line.split("::", 1)[0]).name] += 1
    files = [{"file": f, "count": per_file[f], "covers": TEST_COVERAGE.get(f, "")} for f in sorted(per_file)]
    return {"total": sum(per_file.values()), "files": files}


def csp_hash(source: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode() + "'"


def render() -> str:
    template = (WEB / "template.html").read_text(encoding="utf-8")
    style = (WEB / "styles.css").read_text(encoding="utf-8")
    engine = (WEB / "engine.js").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    for name, src in (("engine.js", engine), ("app.js", app), ("styles.css", style)):
        if re.search(r"</(script|style)", src, re.IGNORECASE):
            raise ValueError(f"{name} must not contain a closing </script> or </style> sequence")

    js = journeys()
    channels = sorted({c for j in js for c in j.path})
    data = {
        "version": settings.VERSION,
        "channels": channels,
        "records": aggregate(js),
        "spends": dict(INITIAL_SPENDS),
        "defaults": {"budget": DEFAULT_BUDGET},
        "reference": stable_floats(reference(js, channels)),
        "tests": json.loads(TEST_SUMMARY.read_text(encoding="utf-8")) if TEST_SUMMARY.exists() else {"total": 0, "files": []},
    }
    data_json = safe_json_for_script(data, indent=None)
    digest = hashlib.sha256((data_json + engine + app + style).encode("utf-8")).hexdigest()[:12]

    csp = "; ".join([
        "default-src 'none'",
        f"script-src {csp_hash(engine)} {csp_hash(app)}",
        f"style-src {csp_hash(style)} https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        "img-src data:",
        "connect-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "object-src 'none'",
        "manifest-src 'none'",
        "upgrade-insecure-requests",
    ])
    replacements = {
        "CSP": csp, "STYLE": style, "ENGINE_JS": engine, "APP_JS": app, "DATA_JSON": data_json,
        "BUILD_INFO": f"v{settings.VERSION} · build {digest}",
    }
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: replacements[m.group(1)], template)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify committed pages are up to date")
    parser.add_argument("--no-collect", action="store_true", help="reuse web/test_summary.json")
    args = parser.parse_args()

    if not args.check and not args.no_collect:
        TEST_SUMMARY.write_text(json.dumps(collect_test_summary(), indent=2) + "\n", encoding="utf-8")

    html = render()
    if args.check:
        stale = [str(p.relative_to(ROOT)) for p in OUTPUTS if not p.exists() or p.read_text(encoding="utf-8") != html]
        if stale:
            print(f"Stale GitHub Pages output: {', '.join(stale)}. Run: python scripts/build_pages.py")
            return 1
        print("GitHub Pages output is up to date.")
        return 0

    for path in OUTPUTS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    print(f"Wrote {', '.join(str(p.relative_to(ROOT)) for p in OUTPUTS)} ({len(html) / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
GitHub Pages build tests: the committed pages are current, the CSP really covers every
inline script/style, the UI code avoids DOM-XSS sinks, and the browser engine reproduces
the Python engine (run under Node when available).
"""

from __future__ import annotations
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_pages  # noqa: E402


@pytest.fixture(scope="module")
def html() -> str:
    return build_pages.render()


def test_committed_pages_are_up_to_date(html):
    for path in build_pages.OUTPUTS:
        assert path.exists(), f"{path} missing; run python scripts/build_pages.py"
        assert path.read_text(encoding="utf-8") == html, f"{path} is stale; run python scripts/build_pages.py"


def test_root_and_docs_pages_are_identical():
    root, docs = (p.read_text(encoding="utf-8") for p in build_pages.OUTPUTS)
    assert root == docs


def _csp(html: str) -> str:
    match = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html)
    assert match, "CSP meta tag missing"
    return match.group(1)


def test_csp_is_strict_and_hashes_every_inline_block(html):
    csp = _csp(html)
    assert "default-src 'none'" in csp and "connect-src 'none'" in csp and "object-src 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    executable = re.findall(r"<script>(.*?)</script>", html, re.S)
    styles = re.findall(r"<style>(.*?)</style>", html, re.S)
    assert len(executable) == 2 and len(styles) == 1
    for block in executable + styles:
        digest = base64.b64encode(hashlib.sha256(block.encode("utf-8")).digest()).decode()
        assert f"'sha256-{digest}'" in csp


def test_no_inline_event_handlers_or_style_attributes(html):
    body = re.sub(r"<script.*?</script>|<style>.*?</style>", "", html, flags=re.S)
    assert not re.search(r"\son[a-z]+\s*=", body, re.I), "inline event handler found"
    assert not re.search(r"\sstyle\s*=", body, re.I), "inline style attribute found"


def test_ui_code_avoids_dom_xss_sinks():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    engine = (ROOT / "web" / "engine.js").read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in app and sink not in engine, sink


def test_embedded_data_cannot_break_out_of_its_script_tag(html):
    match = re.search(r'<script type="application/json" id="seed-data">(.*?)</script>', html, re.S)
    payload = match.group(1)
    assert "<" not in payload and ">" not in payload
    data = json.loads(payload)
    assert sum(r["n"] for r in data["records"]) == 500
    assert data["tests"]["total"] > 0


def test_test_summary_matches_collected_tests():
    """The test count advertised on the page equals what pytest actually collects."""
    summary = json.loads(build_pages.TEST_SUMMARY.read_text(encoding="utf-8"))
    assert summary == build_pages.collect_test_summary()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_javascript_engine_matches_python_engine(html, tmp_path):
    match = re.search(r'<script type="application/json" id="seed-data">(.*?)</script>', html, re.S)
    data_file = tmp_path / "data.json"
    data_file.write_text(match.group(1), encoding="utf-8")
    out = subprocess.run(
        ["node", str(ROOT / "scripts" / "js_parity.js"), str(data_file)],
        capture_output=True, text=True, check=True, timeout=120,
    )
    result = json.loads(out.stdout)
    tolerances = {"rules.": 2e-6, "divergence.": 2e-5, "shapley": 1e-6, "optimizer": 1e-4}
    for name, worst in result["report"].items():
        tol = next((t for prefix, t in tolerances.items() if name.startswith(prefix)), 1e-10)
        assert worst <= tol, f"{name}: |delta| = {worst}"
    assert result["ssrfMismatches"] == []
    assert result["sanitizeMismatches"] == []
    assert result["ciOk"]

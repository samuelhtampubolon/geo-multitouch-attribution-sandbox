"""
Headless smoke test of the Streamlit Control Tower: every tab renders without exceptions,
the KPIs come from real computations, and the security tools behave.
"""

from __future__ import annotations
from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

DASHBOARD = str(Path(__file__).resolve().parent.parent / "src" / "ui" / "dashboard.py")


@pytest.fixture(scope="module")
def app():
    at = AppTest.from_file(DASHBOARD, default_timeout=120)
    at.run()
    return at


def test_dashboard_renders_without_exceptions(app):
    assert not app.exception, [e.value for e in app.exception]
    labels = {m.label for m in app.metric}
    assert {"Total Customer Journeys", "Target SoM", "Share of Voice", "Projected revenue (optimized)"} <= labels


def test_dashboard_kpis_are_computed_not_hardcoded(app):
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Total Customer Journeys"] == "500"
    assert "38,240" not in metrics["Attributed Revenue"]


def test_dashboard_rejects_malformed_tenant_id():
    at = AppTest.from_file(DASHBOARD, default_timeout=120)
    at.run()
    at.sidebar.text_input[0].set_value("../../etc/passwd").run()
    assert any("Tenant ID must match" in e.value for e in at.sidebar.error)


def test_dashboard_ssrf_tool_blocks_metadata(app):
    url_box = next(t for t in app.text_input if t.label.startswith("Test URL"))
    url_box.set_value("https://169.254.169.254/latest/meta-data/").run()
    next(b for b in app.button if b.label == "Validate URL").click().run()
    assert any("Blocked" in e.value for e in app.error)

"""
Interactive Streamlit Dashboard & Control Tower for GEO & Multi-Touch Attribution Sandbox.
Features:
- Plotly Sankey Funnels of multi-touch journeys
- Share-of-Model (SoM) Radar & Sentiment Bar Charts
- 5-Way Attribution Comparison (Last-Click, First-Touch, Linear, Markov, Shapley)
- ROAS & Budget Reallocation Cockpit
- Schema.org JSON-LD Interactive Studio
- OWASP Security & Audit Health Checks
"""

from __future__ import annotations
import json
import re
import time
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from fastapi import HTTPException
from pydantic import ValidationError

from src.database import SessionLocal, init_db
from src.models import ChannelSpend, CustomerJourney, GeoPromptAudit
from src.schemas import (
    CustomerJourneyInput,
    ChannelSpendInput,
    FAQItemInput,
    FAQPageSchemaInput,
    GeoAuditRequest,
    JsonLdRequest,
    MtaComputeRequest,
    OrganizationSchemaInput,
    ProductSchemaInput
)
from src.services.geo_auditor import GeoAuditorService, PROMPT_BANK
from src.services.jsonld_generator import JsonLdGeneratorService
from src.services.markov_shapley_mta import MultiTouchAttributionService

# Page Setup
st.set_page_config(
    page_title="GEO & Multi-Touch Attribution Sandbox",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .main { background-color: #0b0f19; color: #f3f4f6; }
    .stMetric { background: #131b2e; padding: 16px; border-radius: 10px; border: 1px solid #1e293b; }
    .badge-markov { background-color: #3b82f6; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold; }
    .badge-shapley { background-color: #10b981; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold; }
    .badge-warning { background-color: #ef4444; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# Ensure DB initialized
init_db()
db = SessionLocal()

# Sidebar Controls
st.sidebar.title("⚡ GEO & MTA Control")
st.sidebar.markdown("**Digital Ecosystem Strategy & Algorithmic Attribution**")
st.sidebar.divider()

tenant_id = st.sidebar.text_input("Tenant ID", value="tenant-alpha", max_chars=64)
if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", tenant_id or ""):
    st.sidebar.error("Tenant ID must match ^[a-z0-9][a-z0-9_-]{1,63}$")
    st.stop()
target_brand = st.sidebar.text_input("Target Brand", value="TesserPulse", max_chars=64)
category = st.sidebar.selectbox("Market Category", list(PROMPT_BANK.keys()), index=0)
total_budget = st.sidebar.number_input("Total Monthly Marketing Budget ($)", min_value=1000.0, max_value=500000.0, value=22500.0, step=500.0)

st.sidebar.subheader("Model Parameters")
markov_order = st.sidebar.select_slider("Markov chain order (k)", options=[1, 2, 3], value=1,
                                        help="States remember the last k touchpoints.")
half_life = st.sidebar.slider("Time-decay half-life (touches)", 0.5, 6.0, 2.0, 0.5)
beta = st.sidebar.slider("Response elasticity β (diminishing returns)", 0.10, 0.90, 0.50, 0.05,
                         help="revenue_k(x) = a_k · x^β. Lower β = faster saturation.")
trust_region = st.sidebar.slider("Reallocation trust region δ", 0.0, 1.5, 0.5, 0.1,
                                 help="Each channel may move within [(1-δ)s_k, (1+δ)s_k].")
bootstrap_samples = st.sidebar.slider("Bootstrap resamples (95% CI)", 0, 300, 100, 50)

st.sidebar.divider()
st.sidebar.info("🔒 **OWASP Hardened**: SSRF filtered (DNS-checked), XSS-inert JSON-LD, Pydantic extra='forbid', BOLA tenant isolation, rate limits.")

# Main Navigation Tabs
tab_overview, tab_som, tab_mta, tab_roas, tab_jsonld, tab_security = st.tabs([
    "📊 Executive Summary",
    "🎯 Share of Model (GEO)",
    "🔀 Multi-Touch Attribution",
    "💰 ROAS & Budget Reallocator",
    "📜 JSON-LD Schema Studio",
    "🛡️ AppSec & Health"
])


# ==========================================
# TAB 1: EXECUTIVE SUMMARY
# ==========================================
with tab_overview:
    st.header("Executive Attribution & GEO Performance Summary")
    st.markdown(
        "A holistic command center bridging **Generative Engine Citations** with "
        "**Graph-Theoretic Multi-Touch Customer Conversions**."
    )

    # Load summary metrics
    total_journeys = db.query(CustomerJourney).filter(CustomerJourney.tenant_id == tenant_id).count()
    converted_journeys = db.query(CustomerJourney).filter(CustomerJourney.tenant_id == tenant_id, CustomerJourney.converted == True).count()
    total_rev = sum(j.conversion_value_usd for j in db.query(CustomerJourney).filter(CustomerJourney.tenant_id == tenant_id, CustomerJourney.converted == True).all())
    total_spend_db = sum(cs.monthly_spend_usd for cs in db.query(ChannelSpend).filter(ChannelSpend.tenant_id == tenant_id).all())
    blended = (total_rev / total_spend_db) if total_spend_db > 0 else 0.0
    total_audits = db.query(GeoPromptAudit).filter(GeoPromptAudit.tenant_id == tenant_id).count()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Customer Journeys", f"{total_journeys:,}", help="Seeded deterministic conversion paths")
    with col2:
        conv_rate = (converted_journeys / total_journeys * 100) if total_journeys > 0 else 0.0
        st.metric("Overall Conversion Rate", f"{conv_rate:.1f}%", delta=f"{converted_journeys} Converted")
    with col3:
        st.metric("Attributed Revenue", f"${total_rev:,.2f}", delta=f"Blended ROAS: {blended:.2f}x")
    with col4:
        st.metric("GEO Prompts Audited", f"{total_audits:,}", delta="4 Generative Engines")

    st.divider()

    # Journey Sankey Flow
    st.subheader("Customer Multi-Touch Conversion Graph (Sankey Flow)")
    journeys_db = db.query(CustomerJourney).filter(CustomerJourney.tenant_id == tenant_id).limit(200).all()

    # Build Sankey links
    link_counts: dict[tuple[str, str], int] = {}
    for j in journeys_db:
        path = json.loads(j.path_sequence_json)
        full_flow = ["Start"] + path + (["Conversion"] if j.converted else ["Dropoff"])
        for i in range(len(full_flow) - 1):
            pair = (full_flow[i], full_flow[i + 1])
            link_counts[pair] = link_counts.get(pair, 0) + 1

    all_nodes = list(set([p[0] for p in link_counts.keys()] + [p[1] for p in link_counts.keys()]))
    node_map = {name: idx for idx, name in enumerate(all_nodes)}

    sources = [node_map[pair[0]] for pair in link_counts.keys()]
    targets = [node_map[pair[1]] for pair in link_counts.keys()]
    values = list(link_counts.values())

    sankey_fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=15,
            thickness=20,
            line=dict(color="black", width=0.5),
            label=all_nodes,
            color=["#3b82f6" if n not in ("Conversion", "Dropoff") else ("#10b981" if n == "Conversion" else "#ef4444") for n in all_nodes]
        ),
        link=dict(source=sources, target=targets, value=values, color="rgba(99, 102, 241, 0.35)")
    )])
    sankey_fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(sankey_fig, use_container_width=True)


# ==========================================
# TAB 2: SHARE OF MODEL (GEO AUDITOR)
# ==========================================
with tab_som:
    st.header("Generative Engine Optimization (GEO) & Share-of-Model (SoM)")
    st.markdown(
        "Audits how often, in what rank, and with what sentiment your brand is cited by "
        "**ChatGPT, Perplexity, Claude, and Gemini**."
    )

    audit_req = GeoAuditRequest(
        tenant_id=tenant_id,
        target_brand=target_brand,
        category=category,
        competitors=["VortexAI", "OmniAgent", "HyperDev"],
        engines=["ChatGPT", "Perplexity", "Claude", "Gemini"]
    )
    geo_res = GeoAuditorService.run_deterministic_audit(audit_req)

    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Target SoM", f"{geo_res.target_brand_som_score:.3f}", help="Σ decay·sentiment / M")
    g2.metric("Normalized SoM", f"{geo_res.target_brand_normalized_som * 100:.1f}%", help="SoM / 1.5")
    g3.metric("Share of Voice", f"{geo_res.target_brand_share_of_voice * 100:.1f}%")
    g4.metric("Market HHI", f"{geo_res.market_concentration_hhi:,.0f}",
              help="< 1500 unconcentrated · 1500-2500 moderate · > 2500 concentrated")

    c1, c2 = st.columns([1, 1])

    with c1:
        st.subheader("Competitor Share of Model (SoM) Index")
        som_df = [
            {
                "Brand": m.brand_name,
                "SoM Score": m.share_of_model_score,
                "Citations": m.citation_count,
                "Citation Rate": m.citation_rate,
                "Wilson 95% CI": f"[{m.citation_rate_ci_low:.3f}, {m.citation_rate_ci_high:.3f}]",
                "Share of Voice": m.share_of_voice,
                "Avg Rank": m.average_rank,
                "Avg Sentiment": m.average_sentiment
            }
            for m in geo_res.competitor_metrics
        ]
        fig_bar = px.bar(
            som_df,
            x="Brand",
            y="SoM Score",
            color="Brand",
            text="SoM Score",
            title=f"SoM Score Breakdown ({category})"
        )
        fig_bar.update_layout(height=350, paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_bar, use_container_width=True)
        st.dataframe(som_df, use_container_width=True, hide_index=True)

    with c2:
        st.subheader("Engine Citation Share Distribution")
        engine_pie = px.pie(
            names=list(geo_res.engine_breakdown.keys()),
            values=list(geo_res.engine_breakdown.values()),
            hole=0.45,
            title="Citation Distribution by Generative Engine"
        )
        engine_pie.update_layout(height=350, paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(engine_pie, use_container_width=True)

    st.subheader("Audited Citation Evidence Snippets (Sample)")
    table_data = [
        {
            "Engine": s.engine_name,
            "Brand": s.brand_cited,
            "Rank": f"#{s.citation_rank}",
            "Sentiment": s.sentiment_label,
            "Provenance": s.source_provenance,
            "Snippet": s.snippet
        }
        for s in geo_res.citations_sample[:8]
    ]
    st.dataframe(table_data, use_container_width=True)


# ==========================================
# TAB 3: MULTI-TOUCH ATTRIBUTION (MTA)
# ==========================================
with tab_mta:
    st.header("Algorithmic Multi-Touch Attribution: Markov Chain vs Exact Shapley")
    st.markdown(
        "Demonstrates the mathematical flaw of **Last-Click** attribution and compares it against "
        "**Markov Removal Effects** and **Axiomatic Shapley Values**."
    )

    mta_req = MtaComputeRequest(
        tenant_id=tenant_id, total_budget_usd=total_budget, markov_order=markov_order,
        time_decay_half_life=half_life, bootstrap_samples=bootstrap_samples,
        response_elasticity=beta, reallocation_trust_region=trust_region, persist=False,
    )
    try:
        t0 = time.perf_counter()
        mta_res = MultiTouchAttributionService.execute_mta_pipeline(mta_req, db=db)
        compute_ms = (time.perf_counter() - t0) * 1000
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("P(conversion) from chain", f"{mta_res.baseline_conversion_rate * 100:.1f}%",
              delta=f"empirical {mta_res.empirical_conversion_rate * 100:.1f}%", delta_color="off")
    k2.metric("Expected path length", f"{mta_res.expected_path_length:.2f} touches")
    lc_div = next(d for d in mta_res.model_divergence_vs_markov if d.model == "Last-Click")
    k3.metric("Last-Click vs Markov (TV)", f"{lc_div.total_variation * 100:.1f}% of credit",
              delta=f"JSD {lc_div.jensen_shannon_bits:.3f} bits", delta_color="off")
    k4.metric("Compute time", f"{compute_ms:.0f} ms", delta=f"order k={mta_res.markov_order}", delta_color="off")

    # 5-Way Comparison Chart
    compare_data = []
    for ch in mta_res.channels:
        compare_data.append({"Channel": ch.channel_name, "Model": "Last-Click", "Share (%)": ch.last_click_share * 100})
        compare_data.append({"Channel": ch.channel_name, "Model": "First-Touch", "Share (%)": ch.first_touch_share * 100})
        compare_data.append({"Channel": ch.channel_name, "Model": "Linear", "Share (%)": ch.linear_share * 100})
        compare_data.append({"Channel": ch.channel_name, "Model": "Position-Based", "Share (%)": ch.position_based_share * 100})
        compare_data.append({"Channel": ch.channel_name, "Model": "Time-Decay", "Share (%)": ch.time_decay_share * 100})
        compare_data.append({"Channel": ch.channel_name, "Model": "Markov Chain", "Share (%)": ch.markov_share * 100})
        compare_data.append({"Channel": ch.channel_name, "Model": "Shapley Value", "Share (%)": ch.shapley_share * 100})

    fig_comp = px.bar(
        compare_data,
        x="Channel",
        y="Share (%)",
        color="Model",
        barmode="group",
        title="Attribution Share Comparison Across 7 Attribution Models"
    )
    fig_comp.update_layout(height=420, paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig_comp, use_container_width=True)

    st.info(f"💡 **Algorithmic Attribution Strategic Insight**: {mta_res.model_reallocation_insight}")

    # Detailed Table
    st.subheader("Channel Attribution Weights & Removal Effects")
    table_mta = [
        {
            "Marketing Channel": c.channel_name,
            "Last-Click Share": f"{c.last_click_share * 100:.1f}%",
            "First-Touch Share": f"{c.first_touch_share * 100:.1f}%",
            "Markov Share": f"{c.markov_share * 100:.1f}%",
            "Markov 95% CI": (f"[{c.markov_share_ci_low * 100:.1f}%, {c.markov_share_ci_high * 100:.1f}%]"
                              if c.markov_share_ci_low is not None else "-"),
            "Shapley Share": f"{c.shapley_share * 100:.1f}%",
            "Removal Effect": f"{c.markov_removal_effect:.4f}",
            "E[visits]/journey": f"{c.expected_visits_per_journey:.3f}",
            "Attributed Revenue ($)": f"${c.attributed_revenue_markov:,.2f}",
            "Marginal Status": c.marginal_roas_category
        }
        for c in mta_res.channels
    ]
    st.dataframe(table_mta, use_container_width=True)

    st.subheader("Model Disagreement vs Markov")
    st.dataframe([
        {"Model": d.model, "Jensen-Shannon (bits)": d.jensen_shannon_bits, "Total Variation": d.total_variation}
        for d in mta_res.model_divergence_vs_markov
    ], use_container_width=True, hide_index=True)


# ==========================================
# TAB 4: ROAS & BUDGET REALLOCATION
# ==========================================
with tab_roas:
    st.header("Budget Reallocation Optimizer & Marginal ROAS")
    st.markdown(
        "Reallocate marketing dollars away from over-credited bottom-funnel ads towards "
        "high-marginal-return AI Search and Virtual Influencer touchpoints."
    )

    r1, r2, r3 = st.columns(3)
    r1.metric("Projected revenue (current mix)", f"${mta_res.projected_revenue_current_usd:,.0f}")
    r2.metric("Projected revenue (optimized)", f"${mta_res.projected_revenue_optimized_usd:,.0f}",
              delta=f"{mta_res.projected_revenue_lift_pct:+.1f}%")
    r3.metric("Equilibrium marginal ROAS λ*", f"{mta_res.equilibrium_marginal_roas:.3f}")

    roas_df = [
        {
            "Channel": c.channel_name,
            "Current Spend ($)": c.current_spend_usd,
            "Recommended Spend ($)": c.recommended_budget_reallocation_usd,
            "Delta ($)": c.recommended_budget_reallocation_usd - c.current_spend_usd,
            "Last-Click ROAS": f"{c.roas_last_click:.2f}x",
            "Markov ROAS": f"{c.roas_markov:.2f}x",
            "Shapley ROAS": f"{c.roas_shapley:.2f}x",
            "mROAS now": f"{c.marginal_roas_current:.3f}",
            "mROAS optimized": f"{c.marginal_roas_optimized:.3f}",
            "CPA (Markov)": f"${c.cpa_markov:,.2f}" if c.cpa_markov else "-",
            "Status": c.marginal_roas_category
        }
        for c in mta_res.channels
    ]
    st.dataframe(roas_df, use_container_width=True)

    # Budget Shift Chart
    spend_compare = []
    for c in mta_res.channels:
        spend_compare.append({"Channel": c.channel_name, "Budget Type": "Current Spend", "Spend ($)": c.current_spend_usd})
        spend_compare.append({"Channel": c.channel_name, "Budget Type": "Optimized Allocation", "Spend ($)": c.recommended_budget_reallocation_usd})

    fig_spend = px.bar(
        spend_compare,
        x="Channel",
        y="Spend ($)",
        color="Budget Type",
        barmode="group",
        title="Current Spend vs Algorithmic ROAS-Optimized Budget"
    )
    fig_spend.update_layout(height=380, paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig_spend, use_container_width=True)


# ==========================================
# TAB 5: JSON-LD SCHEMA STUDIO
# ==========================================
with tab_jsonld:
    st.header("Schema.org JSON-LD Structured Data Studio for GEO")
    st.markdown(
        "Generate and validate Schema.org markup to maximize citation probabilities in "
        "ChatGPT Search, Perplexity, and Google AI Overviews."
    )

    def render_jsonld(build):
        try:
            res = JsonLdGeneratorService.process_request(req)
        except HTTPException as exc:
            st.error(f"🛑 Blocked (HTTP {exc.status_code}): {exc.detail}")
            return None
        except ValueError as exc:
            st.error(str(exc))
            return None
        st.code(res.script_tag, language="html")
        st.success(f"✅ Valid Schema.org {res.schema_type} markup: sanitized, SSRF-checked, safe for <script> embedding.")
        return res

    schema_type = st.radio("Select Schema Type", ["Product", "FAQPage", "Organization"], horizontal=True)

    if schema_type == "Product":
        p_name = st.text_input("Product Name", value="TesserPulse AI Attribution Engine")
        p_brand = st.text_input("Brand", value="TesserPulse")
        p_price = st.number_input("Monthly License Price ($)", value=499.0)
        p_sku = st.text_input("SKU", value="TP-ENT-2026")
        p_url = st.text_input("Product URL (HTTPS only)", value="https://example.com/products/tesserpulse")
        p_desc = st.text_area("Description", value="Enterprise multi-touch attribution and generative engine optimization platform.")

        if st.button("Generate Product JSON-LD"):
            build = lambda: JsonLdRequest(  # noqa: E731
                tenant_id=tenant_id,
                schema_type="Product",
                product_data=ProductSchemaInput(
                    name=p_name,
                    brand_name=p_brand,
                    description=p_desc,
                    sku=p_sku,
                    price=p_price,
                    url=p_url
                )
            )
            res = render_jsonld(build)
            if res:
                for tip in res.geo_optimization_tips:
                    st.markdown(f"- 💡 {tip}")

    elif schema_type == "FAQPage":
        faq_url = st.text_input("Page URL", value="https://example.com/faqs")
        q1 = st.text_input("Question 1", value="How does Markov removal effect calculate marketing channel value?")
        a1 = st.text_area("Answer 1", value="By simulating the removal of each channel from the graph and measuring the marginal reduction in overall conversion probability.")
        q2 = st.text_input("Question 2", value="Why is Last-Click attribution considered flawed?")
        a2 = st.text_area("Answer 2", value="Last-Click over-credits bottom-of-funnel retargeting while ignoring upper-funnel AI citations and virtual influencers.")

        if st.button("Generate FAQPage JSON-LD"):
            build = lambda: JsonLdRequest(  # noqa: E731
                tenant_id=tenant_id,
                schema_type="FAQPage",
                faq_data=FAQPageSchemaInput(
                    url=faq_url,
                    faqs=[
                        FAQItemInput(question=q1, answer=a1),
                        FAQItemInput(question=q2, answer=a2)
                    ]
                )
            )
            render_jsonld(build)

    elif schema_type == "Organization":
        org_name = st.text_input("Organization Name", value="TesserPulse Technologies Inc.")
        org_url = st.text_input("Website URL", value="https://example.com")
        org_desc = st.text_area("Description", value="Leading research lab for Generative Engine Optimization and algorithmic marketing attribution.")

        if st.button("Generate Organization JSON-LD"):
            build = lambda: JsonLdRequest(  # noqa: E731
                tenant_id=tenant_id,
                schema_type="Organization",
                org_data=OrganizationSchemaInput(
                    name=org_name,
                    url=org_url,
                    description=org_desc,
                    same_as_links=["https://github.com/samuelhtampubolon", "https://linkedin.com/company/tesserpulse"]
                )
            )
            render_jsonld(build)


# ==========================================
# TAB 6: SECURITY & OWASP AUDIT
# ==========================================
with tab_security:
    st.header("AppSec & Threat Mitigation Status (OWASP API Top 10)")

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        st.markdown("""
        ### Active Defenses & Mitigations
        - **SSRF Protection**: Explicit `https://` validation; private IPs (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`) and AWS/GCP metadata (`169.254.169.254`) blocked.
        - **Mass Assignment Mitigation**: Pydantic v2 `ConfigDict(extra='forbid')` enforced on all incoming schemas.
        - **BOLA Protection**: Tenant claims verified in JWT; cross-tenant operations rejected with HTTP 403.
        - **Rate Limiting**: SlowAPI token bucket limiter enforced across all API endpoints.
        - **XSS & Injection**: HTML escaping and `bleach` sanitization before JSON-LD generation.
        """)

    with col_s2:
        st.subheader("Interactive SSRF Guard Test")
        test_url = st.text_input("Test URL against SSRF Validator", value="http://169.254.169.254/latest/meta-data/")
        if st.button("Validate URL"):
            from src.security import validate_and_sanitize_url
            from fastapi import HTTPException
            try:
                safe_out = validate_and_sanitize_url(test_url)
                st.success(f"URL Allowed: {safe_out}")
            except HTTPException as e:
                st.error(f"🛑 Blocked by Security Guard (HTTP {e.status_code}): {e.detail}")

db.close()

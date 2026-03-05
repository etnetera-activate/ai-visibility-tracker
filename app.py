import streamlit as st
import requests
import json
import os
from datetime import datetime

st.set_page_config(
    page_title="AI Visibility Tracker",
    page_icon="🔍",
    layout="wide",
)

# ── Config ────────────────────────────────────────────────────────────────────
def _load_config():
    """Return config dict from st.secrets (Streamlit Cloud) or config.json (local)."""
    try:
        if "n8n_webhook_url" in st.secrets:
            bq_sec = st.secrets.get("bigquery", {})
            return {
                "n8n_webhook_url": st.secrets["n8n_webhook_url"],
                "gemini_api_key": st.secrets.get("gemini_api_key", ""),
                "bigquery": {
                    "project_id": bq_sec.get("project_id", ""),
                    "dataset_id": bq_sec.get("dataset_id", "ai_visibility"),
                    "credentials": dict(bq_sec.get("credentials", {})),
                },
                "auth": dict(st.secrets.get("auth", {})),
                "_from_secrets": True,
            }
    except Exception:
        pass
    try:
        with open("config.json", "r") as f:
            cfg = json.load(f)
        cfg["_from_secrets"] = False
        return cfg
    except FileNotFoundError:
        st.error("Please create a config.json file or configure Streamlit secrets.")
        st.stop()

config = _load_config()

N8N_WEBHOOK_URL = config.get("n8n_webhook_url")
API_KEY = config.get("gemini_api_key")

# ── BigQuery connection (optional) ────────────────────────────────────────────
bq = None
bq_cfg = config.get("bigquery", {})
if bq_cfg.get("project_id") and bq_cfg.get("project_id") != "YOUR_GCP_PROJECT_ID":
    try:
        from bigquery_backend import connect, connect_from_info
        if config.get("_from_secrets") and bq_cfg.get("credentials"):
            bq = connect_from_info(
                bq_cfg["project_id"],
                bq_cfg.get("dataset_id", "ai_visibility"),
                bq_cfg["credentials"],
            )
        elif bq_cfg.get("credentials_file"):
            bq = connect(bq_cfg["project_id"], bq_cfg.get("dataset_id", "ai_visibility"), bq_cfg["credentials_file"])
    except Exception as _e:
        st.warning(f"BigQuery not connected: {_e}")

# ── Sync auth config → .streamlit/secrets.toml (local dev only) ──────────────
auth_cfg = config.get("auth", {})
if not config.get("_from_secrets") and auth_cfg.get("client_id") and auth_cfg["client_id"] != "YOUR_GOOGLE_CLIENT_ID":
    secrets_dir = os.path.join(os.path.dirname(__file__), ".streamlit")
    os.makedirs(secrets_dir, exist_ok=True)
    secrets_path = os.path.join(secrets_dir, "secrets.toml")
    with open(secrets_path, "w") as f:
        f.write("[auth]\n")
        for key in ("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url"):
            f.write(f'{key} = "{auth_cfg[key]}"\n')

# ── Authentication ────────────────────────────────────────────────────────────
if auth_cfg.get("client_id") and auth_cfg["client_id"] != "YOUR_GOOGLE_CLIENT_ID":
    if not st.user.is_logged_in:
        st.header("Welcome to AI Visibility Tracker")
        st.subheader("Please log in to continue.")
        st.button("Log in with Google", on_click=st.login)
        st.stop()

# ── Session state ────────────────────────────────────────────────────────────
if "results" not in st.session_state:
    st.session_state.results = []
if "selected_index" not in st.session_state:
    st.session_state.selected_index = None
if "show_success_banner" not in st.session_state:
    st.session_state.show_success_banner = False
if "last_run_was_save" not in st.session_state:
    st.session_state.last_run_was_save = False

# ── Sidebar — Brand Setup ─────────────────────────────────────────────────────
with st.sidebar:
    # Show user info & logout if authenticated
    if auth_cfg.get("client_id") and auth_cfg["client_id"] != "YOUR_GOOGLE_CLIENT_ID" and st.user.is_logged_in:
        st.markdown(f"👤 **{st.user.name}**")
        st.button("Log out", on_click=st.logout)
        st.divider()

    st.header("🏢 Brand Setup")
    brand = st.text_input("Brand name", value="Tesla")
    brand_url = st.text_input("Website URL (optional)", placeholder="https://www.tesla.com/")
    brand_description = st.text_area(
        "Business description (optional)",
        placeholder="Short description of what your company does and who your customers are.",
        height=100,
    )
    st.divider()
    st.caption("These details are saved with each result for context.")
    if bq:
        st.success("📊 BigQuery connected")


# ── Helper: render a single result detail ────────────────────────────────────
def render_result_detail(r: dict):
    metrics = r.get("metrics", {})
    is_visible = metrics.get("is_visible", False)
    sentiment = metrics.get("sentiment", "UNKNOWN")
    context = metrics.get("context", "No context provided.")
    competitors = metrics.get("competitors", [])
    unbiased_response = r.get("unbiased_bot_response", "")

    if is_visible:
        st.success(f"✅ **{r['brand']}** is mentioned!")
    else:
        st.warning(f"❌ **{r['brand']}** is NOT mentioned.")

    st.markdown("#### Visibility Metrics")
    total_mentioned = len(competitors) + (1 if is_visible else 0)
    visibility_score = f"1 / {total_mentioned}" if is_visible and total_mentioned > 0 else "0"

    col1, col2, col3 = st.columns(3)
    with col1:
        sentiment_labels = {
            "POSITIVE": "🟢 Positive",
            "NEGATIVE": "🔴 Negative",
            "NEUTRAL": "⚪ Neutral",
            "NONE": "⬜ Not mentioned",
        }
        st.metric("Sentiment", sentiment_labels.get(sentiment, sentiment))
    with col2:
        st.metric("Competitors Mentioned", len(competitors))
    with col3:
        st.metric(
            "Visibility Score",
            visibility_score,
            help="1 / N means brand appeared once among N total brands mentioned.",
        )

    st.markdown("**How it fits into the answer:**")
    st.info(context)

    if competitors:
        st.markdown("**Competitors explicitly named:**")
        st.write(", ".join(competitors))

    st.markdown("---")
    st.markdown("#### What the AI actually said")
    st.markdown("> *Raw, unbiased LLM response — before analysis.*")
    st.markdown(unbiased_response if unbiased_response else "*(No response captured.)*")

    with st.expander("View Raw JSON from n8n"):
        st.json(r.get("raw_data", {}))


# ── Core query function (shared by New Query form and Force Rerun) ────────────
def run_query(brand: str, prompt: str, prompt_type: str, brand_url: str, brand_description: str, save_to_bq: bool = True):
    """Call n8n, append result to session state. Returns True on success."""
    payload = {"brand": brand, "prompt": prompt, "api_key": API_KEY, "brand_description": brand_description}
    try:
        response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            record = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "brand": brand,
                "brand_url": brand_url,
                "brand_description": brand_description,
                "prompt": prompt,
                "prompt_type": prompt_type,
                "metrics": data.get("metrics", {}),
                "unbiased_bot_response": data.get("unbiased_bot_response", ""),
                "raw_data": data,
                "test_only": not save_to_bq,
            }
            st.session_state.results.append(record)
            st.session_state.selected_index = len(st.session_state.results) - 1
            if save_to_bq and bq:
                try:
                    from bigquery_backend import save_run
                    save_run(bq, record)
                    st.toast("Saved to BigQuery ✅")
                except Exception as _e:
                    st.toast(f"BigQuery save failed: {_e}", icon="⚠️")
            elif not save_to_bq:
                st.toast("Test run — results not saved to BigQuery.", icon="🧪")
            return True
        else:
            st.error(f"Error {response.status_code} from n8n: {response.text}")
            return False
    except requests.exceptions.Timeout:
        st.error("⏱️ Request timed out (30s). Is your n8n instance running?")
        return False
    except Exception as e:
        st.error(f"Failed to connect to n8n: {e}")
        return False


# ── Main UI ───────────────────────────────────────────────────────────────────
st.title("AI Brand Visibility Tracker")
st.markdown("**Evaluating how LLMs represent your brand.**")

tab_new, tab_dashboard, tab_history = st.tabs(["➕ New Query", "📊 Dashboard", "📈 History"])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — New Query
# ═══════════════════════════════════════════════════════════════════════════════
with tab_new:
    if st.session_state.show_success_banner:
        if st.session_state.last_run_was_save:
            banner_msg = "✅ Analysis complete and saved to BigQuery! Head to the **📊 Dashboard** tab to see your results or **📈 History** tab for trends."
        else:
            banner_msg = "✅ Test run complete! Head to the **📊 Dashboard** tab to see your results. (Results were not saved to BigQuery.)"
        st.success(banner_msg)
        if st.button("✕ Dismiss", key="dismiss_banner"):
            st.session_state.show_success_banner = False
            st.rerun()
        st.markdown("")

    prompt_type = st.selectbox(
        "Prompt type",
        ["Informational", "Commercial", "Competitor", "Navigational"],
        help="Label to categorize the intent of this prompt.",
    )
    prompt = st.text_area(
        "Prompt to ask the LLM:",
        value="What are the best electric car brands?",
    )

    col_btn1, col_btn2 = st.columns(2)
    run_test = col_btn1.button("🧪 Test Run (no save)", use_container_width=True)
    run_save = col_btn2.button("💾 Run & Save to BigQuery", type="primary", use_container_width=True)

    if run_test or run_save:
        if not brand.strip():
            st.warning("Please enter a brand name in the sidebar.")
            st.stop()
        if not prompt.strip():
            st.warning("Please enter a prompt.")
            st.stop()
        if not API_KEY or API_KEY == "YOUR_GEMINI_API_KEY_HERE":
            st.error("Please add a valid Gemini API Key to your config.json.")
            st.stop()

        st.session_state.show_success_banner = False  # reset before new run
        with st.spinner("Analyzing LLM response…"):
            ok = run_query(brand, prompt, prompt_type, brand_url, brand_description, save_to_bq=run_save)
            if ok:
                st.session_state.show_success_banner = True
                st.session_state.last_run_was_save = run_save
                st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Dashboard
# ═══════════════════════════════════════════════════════════════════════════════
with tab_dashboard:
    if not st.session_state.results:
        st.info("No results yet. Run your first query in the **New Query** tab.")
    else:
        results = st.session_state.results

        # ── Summary metrics ───────────────────────────────────────────────────
        total_runs = len(results)
        visible_count = sum(1 for r in results if r.get("metrics", {}).get("is_visible"))
        visibility_rate = f"{visible_count / total_runs * 100:.0f}%"

        sentiments = [r.get("metrics", {}).get("sentiment", "") for r in results if r.get("metrics", {}).get("is_visible")]
        dominant_sentiment = max(set(sentiments), key=sentiments.count) if sentiments else "N/A"
        sentiment_labels = {"POSITIVE": "🟢 Positive", "NEGATIVE": "🔴 Negative", "NEUTRAL": "⚪ Neutral", "NONE": "⬜ None"}

        all_competitors = set()
        for r in results:
            all_competitors.update(r.get("metrics", {}).get("competitors", []))

        sm1, sm2, sm3, sm4 = st.columns(4)
        sm1.metric("Total Runs", total_runs)
        sm2.metric("Visibility Rate", visibility_rate, help="Share of prompts where the brand was mentioned")
        sm3.metric("Dominant Sentiment", sentiment_labels.get(dominant_sentiment, dominant_sentiment))
        sm4.metric("Unique Competitors Seen", len(all_competitors))

        st.markdown("---")

        # ── Results table ─────────────────────────────────────────────────────
        table_rows = []
        for r in reversed(results):
            metrics = r.get("metrics", {})
            table_rows.append({
                "Time": r.get("timestamp", ""),
                "Brand": r.get("brand", ""),
                "Prompt": r.get("prompt", "")[:60] + ("…" if len(r.get("prompt", "")) > 60 else ""),
                "Type": r.get("prompt_type", ""),
                "Visible": "✅" if metrics.get("is_visible") else "❌",
                "Sentiment": metrics.get("sentiment", ""),
                "Competitors #": len(metrics.get("competitors", [])),
            })

        st.dataframe(table_rows, use_container_width=True, hide_index=True)

        col_select = st.columns(1)[0]
        with col_select:
            labels = [
                f"{r['timestamp']}  |  {r['prompt'][:55]}…"
                if len(r["prompt"]) > 55
                else f"{r['timestamp']}  |  {r['prompt']}"
                for r in reversed(results)
            ]
            chosen_label = st.selectbox("View detail for a result:", labels)
            chosen_index = len(results) - 1 - labels.index(chosen_label)
            st.session_state.selected_index = chosen_index

        # ── Result detail ─────────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("### 🔍 Result Detail")
        r = results[st.session_state.selected_index]
        render_result_detail(r)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — History
# ═══════════════════════════════════════════════════════════════════════════════
with tab_history:
    if not bq:
        st.info("BigQuery is not connected. Historical data requires a BigQuery connection.")
    else:
        from bigquery_backend import get_brands, get_visibility_history

        @st.cache_data(ttl=300)
        def _cached_brands():
            return get_brands(bq)

        @st.cache_data(ttl=300)
        def _cached_history(brand_name: str, granularity: str):
            return get_visibility_history(bq, brand_name, granularity)

        brands_list = _cached_brands()

        if not brands_list:
            st.info("No brands found in BigQuery yet. Run and save a query first.")
        else:
            col_brand, col_gran = st.columns([2, 1])
            with col_brand:
                selected_brand = st.selectbox("Select brand", brands_list)
            with col_gran:
                granularity_label = st.radio(
                    "Granularity",
                    ["Daily", "Weekly", "Monthly"],
                    horizontal=True,
                )
            granularity_map = {"Daily": "DAY", "Weekly": "WEEK", "Monthly": "MONTH"}
            granularity = granularity_map[granularity_label]

            df = _cached_history(selected_brand, granularity)

            if df.empty:
                st.info(f"No saved runs found for **{selected_brand}**.")
            else:
                df["date"] = df["date"].astype(str)

                st.markdown(f"#### Visibility Rate over time — *{selected_brand}*")
                st.line_chart(df.set_index("date")[["visibility_rate"]], y_label="Visibility Rate (%)")

                st.markdown("#### Sentiment breakdown")
                st.bar_chart(df.set_index("date")[["positive", "negative", "neutral"]])

                st.markdown("#### Raw data")
                st.dataframe(
                    df.rename(columns={
                        "date": "Date",
                        "total_runs": "Total Runs",
                        "visible_runs": "Visible",
                        "visibility_rate": "Visibility Rate (%)",
                        "positive": "Positive",
                        "negative": "Negative",
                        "neutral": "Neutral",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

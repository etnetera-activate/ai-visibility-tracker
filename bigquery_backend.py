from google.cloud import bigquery
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone


def connect(project_id: str, dataset_id: str, credentials_file: str) -> dict:
    creds = Credentials.from_service_account_file(
        credentials_file,
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    client = bigquery.Client(project=project_id, credentials=creds)
    return {"client": client, "project": project_id, "dataset": dataset_id}


def connect_from_info(project_id: str, dataset_id: str, credentials_info: dict) -> dict:
    creds = Credentials.from_service_account_info(
        credentials_info,
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    client = bigquery.Client(project=project_id, credentials=creds)
    return {"client": client, "project": project_id, "dataset": dataset_id}


def save_run(bq: dict, result: dict):
    brand_id  = _upsert_brand(bq, result)
    prompt_id = _upsert_prompt(bq, result)
    _insert_run(bq, brand_id, prompt_id, result)


# ── helpers ───────────────────────────────────────────────────────────────────

def _table(bq: dict, name: str) -> str:
    return f"`{bq['project']}.{bq['dataset']}.{name}`"


def _upsert_brand(bq: dict, result: dict) -> int:
    client = bq["client"]
    t      = _table(bq, "brands")
    name   = (result.get("brand") or "").strip()

    # Check if already exists
    rows = list(client.query(
        f"SELECT id FROM {t} WHERE LOWER(TRIM(name)) = LOWER(TRIM(@name)) LIMIT 1",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("name", "STRING", name),
        ]),
    ).result())
    if rows:
        return rows[0].id

    # Insert new brand; MAX(id)+1 is safe for single-user app
    client.query(
        f"""
        INSERT INTO {t} (id, name, url, description, created_at)
        SELECT COALESCE(MAX(id), 0) + 1, @name, @url, @description, CURRENT_TIMESTAMP()
        FROM {t}
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("name",        "STRING", name),
            bigquery.ScalarQueryParameter("url",         "STRING", result.get("brand_url")         or ""),
            bigquery.ScalarQueryParameter("description", "STRING", result.get("brand_description") or ""),
        ]),
    ).result()

    # Re-read to return the new id
    rows = list(client.query(
        f"SELECT id FROM {t} WHERE LOWER(TRIM(name)) = LOWER(TRIM(@name)) LIMIT 1",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("name", "STRING", name),
        ]),
    ).result())
    return rows[0].id


def _upsert_prompt(bq: dict, result: dict) -> int:
    client = bq["client"]
    t      = _table(bq, "prompts")
    text   = (result.get("prompt") or "").strip()

    rows = list(client.query(
        f"SELECT id FROM {t} WHERE LOWER(TRIM(text)) = LOWER(TRIM(@text)) LIMIT 1",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("text", "STRING", text),
        ]),
    ).result())
    if rows:
        return rows[0].id

    client.query(
        f"""
        INSERT INTO {t} (id, text, type, created_at)
        SELECT COALESCE(MAX(id), 0) + 1, @text, @type, CURRENT_TIMESTAMP()
        FROM {t}
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("text", "STRING", text),
            bigquery.ScalarQueryParameter("type", "STRING", result.get("prompt_type") or ""),
        ]),
    ).result()

    rows = list(client.query(
        f"SELECT id FROM {t} WHERE LOWER(TRIM(text)) = LOWER(TRIM(@text)) LIMIT 1",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("text", "STRING", text),
        ]),
    ).result())
    return rows[0].id


def get_brands(bq: dict) -> list:
    """Return a sorted list of all brand names stored in BigQuery."""
    rows = list(bq["client"].query(
        f"SELECT DISTINCT name FROM {_table(bq, 'brands')} ORDER BY name"
    ).result())
    return [row.name for row in rows]


def get_visibility_history(bq: dict, brand_name: str, granularity: str = "DAY"):
    """Return a DataFrame with visibility rate aggregated by time bucket.

    granularity: 'DAY' | 'WEEK' | 'MONTH'
    """
    import pandas as pd

    # granularity comes from a fixed UI radio — not free-form user input
    allowed = {"DAY", "WEEK", "MONTH"}
    if granularity not in allowed:
        granularity = "DAY"

    query = f"""
        SELECT
          DATE(DATE_TRUNC(r.timestamp, {granularity})) AS date,
          COUNT(*) AS total_runs,
          COUNTIF(r.is_visible = TRUE) AS visible_runs,
          ROUND(COUNTIF(r.is_visible = TRUE) / COUNT(*) * 100, 1) AS visibility_rate,
          COUNTIF(r.sentiment = 'POSITIVE') AS positive,
          COUNTIF(r.sentiment = 'NEGATIVE') AS negative,
          COUNTIF(r.sentiment = 'NEUTRAL') AS neutral
        FROM {_table(bq, 'runs')} r
        JOIN {_table(bq, 'brands')} b ON r.brand_id = b.id
        WHERE LOWER(TRIM(b.name)) = LOWER(TRIM(@brand_name))
        GROUP BY date
        ORDER BY date
    """
    rows = list(bq["client"].query(
        query,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("brand_name", "STRING", brand_name),
        ]),
    ).result())

    if not rows:
        return pd.DataFrame(columns=["date", "total_runs", "visible_runs", "visibility_rate", "positive", "negative", "neutral"])

    return pd.DataFrame([dict(row) for row in rows])


def _insert_run(bq: dict, brand_id: int, prompt_id: int, result: dict):
    client = bq["client"]
    m = result.get("metrics", {})

    competitors = m.get("competitors", [])
    if isinstance(competitors, str):
        competitors = [c.strip() for c in competitors.split(",") if c.strip()]

    # Get next sequential id
    next_id = list(client.query(
        f"SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM {_table(bq, 'runs')}"
    ).result())[0].next_id

    errors = client.insert_rows_json(
        f"{bq['project']}.{bq['dataset']}.runs",
        [{
            "id":                 next_id,
            "brand_id":           brand_id,
            "prompt_id":          prompt_id,
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "is_visible":         bool(m.get("is_visible")),
            "sentiment":          m.get("sentiment")              or "",
            "context":            m.get("context")               or "",
            "competitors":        competitors,
            "unbiased_response":  result.get("unbiased_bot_response") or "",
        }],
    )
    if errors:
        raise RuntimeError(f"BigQuery insert failed: {errors}")

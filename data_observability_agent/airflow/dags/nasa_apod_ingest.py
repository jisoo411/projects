from datetime import timedelta

import requests
from airflow.models import Variable
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, task
from pendulum import datetime

NEON_CONN_ID = "neon_source_data"


@dag(
    start_date=datetime(2026, 2, 10),
    schedule="@daily",
    default_args={"owner": "pipeline-watch", "retries": 2},
    tags=["nasa", "ingestion"],
    catchup=True,
)
def nasa_apod_ingest():
    @task()
    def fetch_and_load(**context) -> int:
        end_date = context["ds"]
        start_date = (context["logical_date"] - timedelta(days=1)).strftime("%Y-%m-%d")

        api_key = Variable.get("nasa_api_key", default_var="DEMO_KEY")
        url = (
            "https://api.nasa.gov/planetary/apod"
            f"?start_date={start_date}&end_date={end_date}&api_key={api_key}"
        )

        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        entries = resp.json()
        # API returns a list when a date range is given, a single dict otherwise
        if isinstance(entries, dict):
            entries = [entries]

        rows = [
            (
                e.get("date"),
                e.get("url"),
                e.get("title"),
                e.get("explanation"),
                e.get("hdurl"),
                e.get("media_type"),
                e.get("copyright"),
                e.get("service_version"),
            )
            for e in entries
            if e.get("date") and e.get("url")
        ]

        if not rows:
            print(f"No APOD data returned for {start_date} to {end_date}.")
            return 0

        hook = PostgresHook(postgres_conn_id=NEON_CONN_ID)
        conn = hook.get_conn()
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO nasa_apod
                (date, url, title, explanation, hdurl, media_type, copyright, service_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date, url) DO NOTHING
            """,
            rows,
        )
        conn.commit()
        cur.close()
        print(f"Loaded {len(rows)} APOD records for {start_date} to {end_date}.")
        return len(rows)

    @task()
    def log_run(record_count: int, **context):
        dag_id = context["dag"].dag_id
        dag_run_id = context["run_id"]
        logical_date = context["logical_date"]
        end_date = context["ds"]
        start_date = (logical_date - timedelta(days=1)).strftime("%Y-%m-%d")

        log_text = (
            f"DAG {dag_id} completed successfully.\n"
            f"Date range: {start_date} to {end_date}.\n"
            f"Loaded {record_count} Astronomy Picture of the Day records into nasa_apod."
        )

        hook = PostgresHook(postgres_conn_id=NEON_CONN_ID)
        conn = hook.get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO airflow_task_logs
                (dag_id, dag_run_id, task_id, try_number, log_text, logical_date, state,
                 destination_table)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (dag_id, dag_run_id, "fetch_and_load", 1, log_text, logical_date, "success",
             "nasa_apod"),
        )
        conn.commit()
        cur.close()

    record_count = fetch_and_load()
    log_run(record_count)


nasa_apod_ingest()

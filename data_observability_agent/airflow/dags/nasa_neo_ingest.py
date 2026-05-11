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
def nasa_neo_ingest():
    @task()
    def fetch_and_load(**context):
        # end_date = execution date; start_date = one day prior
        end_date = context["ds"]  # YYYY-MM-DD string
        start_date = (context["logical_date"] - timedelta(days=1)).strftime("%Y-%m-%d")

        api_key = Variable.get("nasa_api_key", default_var="DEMO_KEY")
        url = (
            "https://api.nasa.gov/neo/rest/v1/feed"
            f"?start_date={start_date}&end_date={end_date}&api_key={api_key}"
        )

        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        neo_by_date = resp.json().get("near_earth_objects", {})

        rows = []
        for feed_date, asteroids in neo_by_date.items():
            for a in asteroids:
                ca = a["close_approach_data"][0] if a.get("close_approach_data") else {}
                rv = ca.get("relative_velocity", {})
                md = ca.get("miss_distance", {})
                diam_km = a.get("estimated_diameter", {}).get("kilometers", {})
                rows.append((
                    a["id"],
                    ca.get("close_approach_date"),
                    a.get("neo_reference_id"),
                    a.get("name"),
                    a.get("nasa_jpl_url"),
                    a.get("absolute_magnitude_h"),
                    diam_km.get("estimated_diameter_min"),
                    diam_km.get("estimated_diameter_max"),
                    a.get("is_potentially_hazardous_asteroid"),
                    a.get("is_sentry_object"),
                    ca.get("close_approach_date_full"),
                    float(rv["kilometers_per_second"]) if rv.get("kilometers_per_second") else None,
                    float(md["kilometers"]) if md.get("kilometers") else None,
                    ca.get("orbiting_body"),
                    feed_date,
                ))

        if not rows:
            print(f"No NEO data returned for {start_date} to {end_date}.")
            return 0

        hook = PostgresHook(postgres_conn_id=NEON_CONN_ID)
        conn = hook.get_conn()
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO nasa_near_earth_objects (
                id, close_approach_date, neo_reference_id, name, nasa_jpl_url,
                absolute_magnitude_h, estimated_diameter_km_min, estimated_diameter_km_max,
                is_potentially_hazardous_asteroid, is_sentry_object,
                close_approach_date_full, relative_velocity_km_per_s,
                miss_distance_km, orbiting_body, feed_date
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s
            )
            ON CONFLICT (id, close_approach_date) DO NOTHING
            """,
            rows,
        )
        conn.commit()
        cur.close()
        print(f"Loaded {len(rows)} NEO records for {start_date} to {end_date}.")
        return len(rows)

    @task()
    def log_run(record_count: int, **context):
        """Write a run summary to airflow_task_logs so the backend can embed it."""
        dag_id = context["dag"].dag_id
        dag_run_id = context["run_id"]
        logical_date = context["logical_date"]
        end_date = context["ds"]
        start_date = (logical_date - timedelta(days=1)).strftime("%Y-%m-%d")

        log_text = (
            f"DAG {dag_id} completed successfully.\n"
            f"Date range: {start_date} to {end_date}.\n"
            f"Loaded {record_count} near-Earth object records into nasa_near_earth_objects."
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
             "nasa_near_earth_objects"),
        )
        conn.commit()
        cur.close()

    record_count = fetch_and_load()
    log_run(record_count)


nasa_neo_ingest()

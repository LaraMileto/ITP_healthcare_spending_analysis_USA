# Script to retrieve data and build the database.
import os
import time
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import load_dotenv
from fredapi import Fred

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / "sandbox.env"

load_dotenv(ENV_PATH)
fred = Fred(api_key=os.getenv("API_KEY"))

DB_PATH = Path(__file__).resolve().parent / "fred.db"


def get_data(ticker: str | list[str]) -> pd.DataFrame:
    fred = Fred(api_key=os.getenv("API_KEY"))
    df = pd.DataFrame()

    if type(ticker) is str:
        ticker = [ticker]

    if type(ticker) is list:
        for i in ticker:
            data = fred.get_series(i).reset_index()
            data.columns = ["date", "value"]
            df = data if df.empty else pd.merge(df, data, on=df.columns[0], how="left")
            df = df.bfill()
    return df


INDICATORS = [
    (1, "CXUHEALTHLB0102M", "Healthcare Spending", "USD", "spending"),
    (2, "CXUHEALTHLB0103M", "Healthcare Spending", "USD", "spending"),
    (3, "CXUHEALTHLB0104M", "Healthcare Spending", "USD", "spending"),
    (4, "CXUHEALTHLB0105M", "Healthcare Spending", "USD", "spending"),
    (5, "CXUHEALTHLB0106M", "Healthcare Spending", "USD", "spending"),
    (6, "CXUINCAFTTXLB0102M", "Income After Tax", "USD", "income"),
    (7, "CXUINCAFTTXLB0103M", "Income After Tax", "USD", "income"),
    (8, "CXUINCAFTTXLB0104M", "Income After Tax", "USD", "income"),
    (9, "CXUINCAFTTXLB0105M", "Income After Tax", "USD", "income"),
    (10, "CXUINCAFTTXLB0106M", "Income After Tax", "USD", "income"),
    (11, "CXUTOTALEXPLB0102M", "Total Expenditure", "USD", "spending"),
    (12, "CXUTOTALEXPLB0103M", "Total Expenditure", "USD", "spending"),
    (13, "CXUTOTALEXPLB0104M", "Total Expenditure", "USD", "spending"),
    (14, "CXUTOTALEXPLB0105M", "Total Expenditure", "USD", "spending"),
    (15, "CXUTOTALEXPLB0106M", "Total Expenditure", "USD", "spending"),
    (16, "CPIAUCSL", "CPI Total", "index", "price_index"),
    (17, "CPIMEDSL", "CPI Medical", "index", "price_index"),
    (18, "MEPAINUSA646N", "Median Personal Income", "USD", "income"),
]

QUINTILES = [
    (0, "ALL", "Economy-wide (no quintile breakdown)"),
    (1, "Q1", "Lowest 20% of income"),
    (2, "Q2", "Second 20% of income"),
    (3, "Q3", "Middle 20% of income"),
    (4, "Q4", "Fourth 20% of income"),
    (5, "Q5", "Highest 20% of income"),
]

CODE_TO_QUINTILE = {
    "CXUHEALTHLB0102M": "Q1",
    "CXUHEALTHLB0103M": "Q2",
    "CXUHEALTHLB0104M": "Q3",
    "CXUHEALTHLB0105M": "Q4",
    "CXUHEALTHLB0106M": "Q5",
    "CXUINCAFTTXLB0102M": "Q1",
    "CXUINCAFTTXLB0103M": "Q2",
    "CXUINCAFTTXLB0104M": "Q3",
    "CXUINCAFTTXLB0105M": "Q4",
    "CXUINCAFTTXLB0106M": "Q5",
    "CXUTOTALEXPLB0102M": "Q1",
    "CXUTOTALEXPLB0103M": "Q2",
    "CXUTOTALEXPLB0104M": "Q3",
    "CXUTOTALEXPLB0105M": "Q4",
    "CXUTOTALEXPLB0106M": "Q5",
    "CPIAUCSL": "ALL",
    "CPIMEDSL": "ALL",
    "MEPAINUSA646N": "ALL",
}


def build_dimension_tables(
    indicators: list[tuple[int, str, str, str, str]] = INDICATORS,
    quintiles: list[
        tuple[
            int,
            str,
            str,
        ]
    ] = QUINTILES,
) -> tuple[pd.DataFrame, pd.DataFrame]:

    df_indicators = pd.DataFrame(
        indicators, columns=["indicator_id", "fred_code", "title", "unit", "category"]
    )
    df_quintiles = pd.DataFrame(
        quintiles, columns=["quintile_id", "label", "description"]
    )
    return df_indicators, df_quintiles


def fetch_until_success(code: str, wait_seconds: int = 5) -> pd.DataFrame:
    """
    Keep trying until the data is fetched successfully.
    Warning: this can run forever.
    """
    attempt = 1

    while True:
        try:
            df_chunk = get_data(code)

        except Exception as e:
            print(f"WARNING: {code} fetch failed on attempt {attempt}: {e}")
            time.sleep(wait_seconds)
            attempt += 1

        else:
            print(f"{code} fetch successful on attempt {attempt}")
            return df_chunk


def build_observations(
    df_indicators: pd.DataFrame, df_quintiles: pd.DataFrame
) -> pd.DataFrame:
    code_to_ind_id = df_indicators.set_index("fred_code")["indicator_id"].to_dict()
    label_to_qnt_id = df_quintiles.set_index("label")["quintile_id"].to_dict()

    obs_frames = []

    successful = []
    failed = []
    empty = []

    for _, row in df_indicators.iterrows():
        code = row["fred_code"]

        try:
            df_chunk = get_data(code)

        except Exception as e:
            print(f"WARNING: {code} fetch failed ({e}), retrying")
            df_chunk = fetch_until_success(code, wait_seconds=5)

        if df_chunk is None or df_chunk.empty:
            print(f"WARNING: {code} has no data, skipping")
            empty.append(code)
            continue

        df_chunk["indicator_id"] = code_to_ind_id[code]
        df_chunk["quintile_id"] = label_to_qnt_id[CODE_TO_QUINTILE[code]]
        df_chunk["date"] = pd.to_datetime(df_chunk["date"])

        obs_frames.append(df_chunk[["indicator_id", "quintile_id", "date", "value"]])

        successful.append(code)

        time.sleep(2)

    if not obs_frames:
        raise ValueError("No observations were successfully fetched.")

    df_observations = pd.concat(obs_frames, ignore_index=True)

    print("\nBuild observations summary")
    print(f"Successful: {len(successful)}")
    print(f"Empty: {len(empty)}")
    print(f"Failed: {len(failed)}")

    if failed:
        print(f"Failed series: {failed}")

    if empty:
        print(f"Empty series: {empty}")

    if not failed and not empty:
        print("All FRED series were fetched successfully.")

    return df_observations


def build_database(
    df_indicators: pd.DataFrame,
    df_quintiles: pd.DataFrame,
    df_observations: pd.DataFrame,
) -> None:

    if DB_PATH.exists():
        print(f"{DB_PATH} already exists. Removing it")
        DB_PATH.unlink()
    conn = duckdb.connect(str(DB_PATH))

    conn.execute("""
        CREATE TABLE indicators (
            indicator_id INTEGER PRIMARY KEY,
            fred_code    VARCHAR NOT NULL UNIQUE,
            title        VARCHAR NOT NULL,
            unit         VARCHAR NOT NULL,
            category     VARCHAR NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE quintiles (
            quintile_id INTEGER PRIMARY KEY,
            label       VARCHAR NOT NULL UNIQUE,
            description VARCHAR
        )
    """)

    conn.execute("""
        CREATE TABLE observations (
            indicator_id INTEGER NOT NULL REFERENCES indicators(indicator_id),
            quintile_id  INTEGER NOT NULL REFERENCES quintiles(quintile_id),
            date         DATE    NOT NULL,
            value        DOUBLE  NOT NULL,
            PRIMARY KEY (indicator_id, quintile_id, date)
        )
    """)
    conn.execute("INSERT INTO indicators   SELECT * FROM df_indicators")
    conn.execute("INSERT INTO quintiles    SELECT * FROM df_quintiles")
    conn.execute("INSERT INTO observations SELECT * FROM df_observations")
    conn.close()
    print(f"Database written to {DB_PATH}")


def main():
    print("Building dimension tables...")
    df_indicators, df_quintiles = build_dimension_tables()
    print(f"  {len(df_indicators)} indicators, {len(df_quintiles)} quintiles")

    print("Fetching observations from FRED...")
    df_observations = build_observations(df_indicators, df_quintiles)

    print("Loading into DuckDB...")
    build_database(df_indicators, df_quintiles, df_observations)
    print("Done.")


if __name__ == "__main__":
    main()
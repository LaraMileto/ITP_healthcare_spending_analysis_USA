import datetime as dt
from pathlib import Path
from typing import Any

import duckdb
import matplotlib.pyplot as plt
import pandas as pd


class HealthcareAffordabilityAnalyzer:
    """
    Analyzer for urban healthcare affordability using data stored in DuckDB.

    Research question:
    How have urban healthcare expenditures evolved relative to personal income,
    and has healthcare become less affordable over time?
    """

    def __init__(self, db_path: str | Path):
        """
        Open a read-only connection to the DuckDB database.

        Parameters
        ----------
        db_path : str or Path
            Path to the DuckDB database file.
        """
        # Reject obviously-wrong inputs up front
        if not isinstance(db_path, (str, Path)):
            raise TypeError(
                f"db_path must be a str or Path, got {type(db_path).__name__}."
            )
        if isinstance(db_path, str) and db_path.strip() == "":
            raise ValueError("db_path must not be empty.")
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database file not found: {self.db_path}")
        self.con = duckdb.connect(
            str(self.db_path), read_only=True
        )  # we do not want to write in the duckdb
        # Confirm the schema we expect is present
        expected_tables = {"indicators", "quintiles", "observations"}
        existing = {
            row[0]
            for row in self.con.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        missing = expected_tables - existing
        if missing:
            raise ValueError(
                f"Database at {self.db_path} is missing expected tables: "
                f"{sorted(missing)}. Did you run data/build_fred.py?"
            )

    def _validate_date_range(
        self,
        start_date: str | dt.date | None,
        end_date: str | dt.date | None,
    ) -> None:
        """
        Parameters
        ----------
        start_date, end_date : str, datetime.date, or None
            Strings must be 'YYYY-MM-DD'. None means no bound on that side.

        Returns
        -------
        None
            Raises ValueError if a string is malformed or end_date < start_date.
        """
        start = self._parse_date(start_date, "start_date")
        end = self._parse_date(end_date, "end_date")

        if start is None or end is None:
            return

        if end < start:
            raise ValueError("end_date must be greater than or equal to start_date.")

    def _check_not_empty(self, df: pd.DataFrame, context: str) -> None:
        """
        Check that a DataFrame is not empty.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame to check.
        context : str
            Method name used in the error message.

        Returns
        -------
        None
            Raises ValueError if `df` is empty.
        """
        if df.empty:
            raise ValueError(
                f"No observations found in {context}. "
                f"Check the date range and filters you passed."
            )

    def _valid_categories(self) -> list[str]:
        """
        List the indicator categories present in the database.

        Returns
        -------
        list[str]
            Distinct values of `indicators.category`, sorted.
        """
        query = """
            SELECT DISTINCT category
            FROM indicators
            ORDER BY category
        """
        return [row[0] for row in self.con.execute(query).fetchall()]

    def _valid_quintiles(self) -> tuple[list[int], list[str]]:
        """
        List the quintile IDs and labels present in the database.

        Returns
        -------
        tuple[list[int], list[str]]
            Parallel lists (ids, labels) ordered by quintile_id.
        """
        query = """
            SELECT quintile_id, label
            FROM quintiles
            ORDER BY quintile_id
        """
        rows = self.con.execute(query).fetchall()

        valid_ids = [row[0] for row in rows]
        valid_labels = [row[1] for row in rows]

        return valid_ids, valid_labels

    def _validate_category(self, category: str | None) -> None:
        """
        Check that `category` exists in the indicators table.

        Parameters
        ----------
        category : str or None
            Indicator category. None passes silently.

        Returns
        -------
        None
            Raises ValueError if `category` is not None and not in the database.
        """
        if category is None:
            return

        valid_categories = self._valid_categories()

        if category not in valid_categories:
            raise ValueError(
                f"{category} is not a valid category. "
                f"Valid categories are: {valid_categories}."
            )

    def _validate_quintile(self, quintile: str | int | None) -> None:
        """
        Check that `quintile` matches a known quintile ID or label.

        Parameters
        ----------
        quintile : str, int, or None
            Quintile_id (int) or label (str). None passes silently.

        Returns
        -------
        None
            Raises ValueError if `quintile` is not None and matches neither.
        """
        if quintile is None:
            return

        valid_ids, valid_labels = self._valid_quintiles()

        if quintile not in valid_ids and quintile not in valid_labels:
            raise ValueError(
                f"{quintile} is not a valid quintile. "
                f"Valid IDs are: {valid_ids}. "
                f"Valid labels are: {valid_labels}."
            )

    def _classify_variable(self, fred_code: str) -> str | None:
        """
        Map a FRED code to its variable name.

        Parameters
        ----------
        fred_code : str
            FRED series code.

        Returns
        -------
        str or None
            "healthcare_spending", "income_after_tax", "total_expenditure",
            or None if the code doesn't match any of these three families.
        """
        if fred_code.startswith("CXUHEALTH"):
            return "healthcare_spending"

        if fred_code.startswith("CXUINCAFTTX"):
            return "income_after_tax"

        if fred_code.startswith("CXUTOTALEXP"):
            return "total_expenditure"

        return None

    def _parse_date(self, value, name: str) -> dt.date | None:
        """
        Convert `value` to a `datetime.date`.

        Parameters
        ----------
        value : str, datetime.date, or None
            Strings must be 'YYYY-MM-DD'.
        name : str
            Argument name used in error messages.

        Returns
        -------
        datetime.date or None
            None if `value` is None. Raises ValueError on malformed strings.
        """
        if value is None:
            return None

        if isinstance(value, dt.date):
            return value

        try:
            return dt.datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"{name} must be in format 'YYYY-MM-DD'.") from None

    def _classify_price_index_variable(self, fred_code: str) -> str | None:
        """
        Map a CPI FRED code to its variable name.

        Parameters
        ----------
        fred_code : str
            FRED series code.

        Returns
        -------
        str or None
            "cpi_total" for CPIAUCSL, "cpi_medical" for CPIMEDSL, else None.
        """
        if fred_code == "CPIAUCSL":
            return "cpi_total"

        if fred_code == "CPIMEDSL":
            return "cpi_medical"

        return None

    def list_indicators(self) -> pd.DataFrame:
        """
        Return the full indicators dimension table.

        No Parameters

        Returns
        -------
        pd.DataFrame
            Columns: indicator_id, fred_code, title, unit, category.
            Ordered by indicator_id.
        """
        query = """SELECT * FROM indicators ORDER BY indicator_id"""
        return self.con.execute(query).fetchdf()

    def list_quintiles(self) -> pd.DataFrame:
        """
        Return the full quintiles dimension table.

        No Parameters

        Returns
        -------
        pd.DataFrame
            Columns: quintile_id, label, description. Ordered by quintile_id.
        """
        query = """SELECT * FROM quintiles ORDER BY quintile_id"""
        return self.con.execute(query).fetchdf()

    def get_date_range(
        self,
        category: str | None = None,
    ) -> tuple[pd.Timestamp, pd.Timestamp]:
        """
        Return the earliest and latest observation dates in the database.

        Parameters
        ----------
        category : str or None, default None
            Restrict to one indicator category; None considers all observations.

        Returns
        -------
        tuple[pd.Timestamp, pd.Timestamp]
            (earliest_date, latest_date).
        """
        self._validate_category(category)

        if category is None:
            query = "SELECT MIN(date), MAX(date) FROM observations"
            params: list = []
        else:
            query = """
                    SELECT MIN(o.date), MAX(o.date)
                    FROM observations AS o
                             JOIN indicators AS i
                                  ON o.indicator_id = i.indicator_id
                    WHERE i.category = ? \
                    """
            params = [category]

        row = self.con.execute(query, params).fetchone()
        return pd.Timestamp(row[0]), pd.Timestamp(row[1])

    def load_observations(
        self,
        category: str | None = None,
        quintile: str | int | None = None,
        start_date: str | dt.date | None = None,
        end_date: str | dt.date | None = None,
    ) -> pd.DataFrame:
        """
        Load observations from the database with optional filters.

        Parameters
        ----------
        category : str or None
            Filter to one indicator category.
        quintile : str, int, or None
            Filter to one quintile (ID or label).
        start_date, end_date : str, datetime.date, or None
            Inclusive date bounds. Strings must be 'YYYY-MM-DD'.

        Returns
        -------
        pd.DataFrame
            Columns: date, value, indicator_id, fred_code, category,
            quintile_id, quintile. Ordered by date.
        """

        self._validate_category(category)
        self._validate_quintile(quintile)
        self._validate_date_range(start_date, end_date)

        start = self._parse_date(start_date, "start_date")
        end = self._parse_date(end_date, "end_date")

        query = """
            SELECT
                o.date,
                o.value,
                i.indicator_id,
                i.fred_code,
                i.category,
                q.quintile_id,
                q.label AS quintile
            FROM observations AS o
            JOIN indicators AS i
                ON o.indicator_id = i.indicator_id
            JOIN quintiles AS q
                ON o.quintile_id = q.quintile_id
        """

        conditions = []
        params = []

        if category is not None:
            conditions.append("i.category = ?")
            params.append(category)

        if quintile is not None:
            if isinstance(quintile, int):
                conditions.append("q.quintile_id = ?")
                params.append(quintile)
            else:
                conditions.append("q.label = ?")
                params.append(quintile)

        if start is not None:
            conditions.append("o.date >= ?")
            params.append(start)

        if end is not None:
            conditions.append("o.date <= ?")
            params.append(end)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY o.date"

        df = self.con.execute(query, params).fetchdf()
        df["date"] = pd.to_datetime(df["date"])

        return df

    def prepare_quintile_panel(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        drop_NaN: bool = False,
    ) -> pd.DataFrame:
        """
        Build a wide-format panel by date and quintile.

        Parameters
        ----------
        start_date, end_date : str or None
            Inclusive date bounds in 'YYYY-MM-DD'.
        drop_NaN : bool, optional
            If True, drop rows where any column is NaN. Default False.

        Returns
        -------
        pd.DataFrame
            Columns: date, quintile, healthcare_spending, income_after_tax,
            total_expenditure. Excludes "ALL" rows.
        """
        self._validate_date_range(start_date, end_date)

        df = self.load_observations(
            start_date=start_date,
            end_date=end_date,
        )
        df = df[df["quintile"] != "ALL"]

        df["variable"] = df["fred_code"].apply(self._classify_variable)

        df = df[df["variable"].notna()].copy()

        panel = df.pivot_table(
            index=["date", "quintile"],
            columns="variable",
            values="value",
            aggfunc="first",
        ).reset_index()

        expected_columns = [
            "date",
            "quintile",
            "healthcare_spending",
            "income_after_tax",
            "total_expenditure",
        ]

        missing = [col for col in expected_columns if col not in panel.columns]
        if missing:
            raise ValueError(
                f"Quintile panel is missing expected variables: {missing}. "
                f"Check that the database contains healthcare_spending, "
                f"income_after_tax, and total_expenditure series for the "
                f"requested date range "
                f"(start_date={start_date}, end_date={end_date})."
            )
        if drop_NaN:
            return panel[expected_columns].dropna()
        return panel[expected_columns]

    def prepare_price_index_panel(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        drop_NaN: bool = False,
    ) -> pd.DataFrame:
        """
        Build a wide-format CPI panel by date.

        Parameters
        ----------
        start_date, end_date : str or None
            Inclusive date bounds in 'YYYY-MM-DD'.
        drop_NaN : bool, optional
            If True, drop rows where any column is NaN. Default False.

        Returns
        -------
        pd.DataFrame
            Columns: date, cpi_total, cpi_medical.
        """
        df = self.load_observations(
            category="price_index",
            start_date=start_date,
            end_date=end_date,
        )

        df["variable"] = df["fred_code"].apply(self._classify_price_index_variable)

        df = df[df["variable"].notna()]

        panel = df.pivot_table(
            index="date",
            columns="variable",
            values="value",
            aggfunc="first",
        ).reset_index()

        expected_columns = [
            "date",
            "cpi_total",
            "cpi_medical",
        ]

        if drop_NaN:
            return panel[expected_columns].dropna()
        return panel[expected_columns]

    def prepare_burden_panel(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """
        Build a panel with both affordability metrics side by side.

        Returns
        -------
        pd.DataFrame
            Columns: date, quintile, healthcare_spending, income_after_tax,
            total_expenditure, affordability_ratio, health_budget_share.
        """
        df = self.prepare_quintile_panel(
            start_date=start_date,
            end_date=end_date,
        )

        if (df["income_after_tax"] <= 0).any():
            raise ValueError("income_after_tax contains zero or negative values.")
        if (df["total_expenditure"] <= 0).any():
            raise ValueError("total_expenditure contains zero or negative values.")

        df["affordability_ratio"] = (
            df["healthcare_spending"] / df["income_after_tax"] * 100
        )
        df["health_budget_share"] = (
            df["healthcare_spending"] / df["total_expenditure"] * 100
        )

        return df

    def compute_affordability_ratio(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        quintile: str | None = None,
    ) -> pd.DataFrame:
        """
        Compute healthcare spending as a percentage of income after tax, by quintile.

        Parameters
        ----------
        quintile : str or None, default None
            Restrict the result to one quintile label (e.g. "Q1"). None keeps
            all quintiles.
        start_date, end_date : str or None
            Inclusive date bounds in 'YYYY-MM-DD'.

        Returns
        -------
        pd.DataFrame
            Columns: date, quintile, healthcare_spending, income_after_tax,
            affordability_ratio (percent).
        """
        self._validate_date_range(start_date, end_date)
        self._validate_quintile(quintile)
        df = self.prepare_quintile_panel(
            start_date=start_date,
            end_date=end_date,
        )
        # Optional single-quintile filter (e.g. "Q1" to only get the lowest quintile)
        if quintile is not None:
            df = df[df["quintile"] == quintile]

        self._check_not_empty(df, "compute_affordability_ratio")

        if (df["income_after_tax"] <= 0).any():
            raise ValueError(
                "Income after tax contains zero or negative values. "
                "Cannot compute affordability ratio safely."
            )

        df["affordability_ratio"] = (
            df["healthcare_spending"] / df["income_after_tax"] * 100
        )

        output_columns = [
            "date",
            "quintile",
            "healthcare_spending",
            "income_after_tax",
            "affordability_ratio",
        ]

        return df[output_columns]

    def compute_health_budget_share(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        quintile: str | None = None,
    ) -> pd.DataFrame:
        """
        Compute healthcare spending as a percentage of total expenditure, by quintile.

        Parameters
        ----------
        quintile : str or None, default None
            Restrict the result to one quintile label (e.g. "Q1"). None keeps
            all quintiles.
        start_date, end_date : str or None
            Inclusive date bounds in 'YYYY-MM-DD'.

        Returns
        -------
        pd.DataFrame
            Columns: date, quintile, healthcare_spending, total_expenditure,
            health_budget_share (percent).
        """

        self._validate_date_range(start_date, end_date)
        self._validate_quintile(quintile)
        df = self.prepare_quintile_panel(
            start_date=start_date,
            end_date=end_date,
        )
        # Optional single-quintile filter.
        if quintile is not None:
            df = df[df["quintile"] == quintile]

        self._check_not_empty(df, "compute_health_budget_share")


        if (df["total_expenditure"] <= 0).any():
            raise ValueError(
                "total_expenditure contains zero or negative values. "
                "Cannot compute budget share safely."
            )

        df["health_budget_share"] = (
            df["healthcare_spending"] / df["total_expenditure"] * 100
        )

        return (
            df[
                [
                    "date",
                    "quintile",
                    "healthcare_spending",
                    "total_expenditure",
                    "health_budget_share",
                ]
            ]
            .sort_values(["quintile", "date"])
            .reset_index(drop=True)
        )

    def compute_affordability_gap(
        self,
        low_quintile: str = "Q1",
        high_quintile: str = "Q5",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """
        Compute the affordability-ratio gap between two quintiles over time.

        Parameters
        ----------
        low_quintile, high_quintile : str
            Quintile labels (e.g. "Q1", "Q5"). Must differ.
        start_date, end_date : str or None
            Inclusive date bounds in 'YYYY-MM-DD'.

        Returns
        -------
        pd.DataFrame
            Columns: date, low_quintile_ratio, high_quintile_ratio,
            affordability_gap (percentage points).
        """
        self._validate_date_range(start_date, end_date)
        self._validate_quintile(low_quintile)
        self._validate_quintile(high_quintile)

        if low_quintile == high_quintile:
            raise ValueError("low_quintile and high_quintile must be different.")

        df = self.prepare_quintile_panel(
            start_date=start_date,
            end_date=end_date,
        )

        df = df[df["quintile"].isin([low_quintile, high_quintile])]  # Filter

        self._check_not_empty(df, "compute_affordability_gap")

        if (df["income_after_tax"] <= 0).any():
            raise ValueError(
                "income_after_tax contains zero or negative values. "
                "Cannot compute affordability ratio."
            )

        df["affordability_ratio"] = (
            df["healthcare_spending"] / df["income_after_tax"] * 100
        )

        wide = df.pivot(
            index="date",
            columns="quintile",
            values="affordability_ratio",
        ).reset_index()

        wide = wide.rename(
            columns={
                low_quintile: "low_quintile_ratio",
                high_quintile: "high_quintile_ratio",
            }
        )

        wide["affordability_gap"] = (
            wide["low_quintile_ratio"] - wide["high_quintile_ratio"]
        )

        return (
            wide[
                [
                    "date",
                    "low_quintile_ratio",
                    "high_quintile_ratio",
                    "affordability_gap",
                ]
            ]
            .sort_values("date")
            .reset_index(drop=True)
        )

    def compute_real_values(
        self,
        variable: str,
        cpi_type: str = "cpi_total",
        start_date: str | None = None,
        end_date: str | None = None,
        quintile: str | None = None,
        base_date: str | None = None,
    ) -> pd.DataFrame:
        """
        Deflate a nominal variable using a CPI series.

        Parameters
        ----------
        variable : str
            One of "healthcare_spending", "income_after_tax", "total_expenditure".
        cpi_type : str, default "cpi_total"
            Either "cpi_total" or "cpi_medical".
        start_date, end_date : str or None
            Inclusive date bounds in 'YYYY-MM-DD'.
        base_date : str or None, default None
            If given, rescale CPI so that CPI[base_date] = 100 before deflating.
            Real values are then in "dollars at base_date" terms. Format 'YYYY-MM-DD'.
            The base date must be present in the data.

        Returns
        -------
        pd.DataFrame
            Columns: date, quintile, nominal_value, cpi, real_value.
        """

        self._validate_date_range(start_date, end_date)

        valid_variables = {
            "healthcare_spending",
            "income_after_tax",
            "total_expenditure",
        }

        valid_cpi_types = {
            "cpi_total",
            "cpi_medical",
        }

        if variable not in valid_variables:
            raise ValueError(f"variable must be one of {sorted(valid_variables)}.")

        if cpi_type not in valid_cpi_types:
            raise ValueError(f"cpi_type must be one of {sorted(valid_cpi_types)}.")

        self._validate_quintile(quintile)
        quintile_df = self.prepare_quintile_panel(
            start_date=start_date,
            end_date=end_date,
        )

        cpi_df = self.prepare_price_index_panel(
            start_date=start_date,
            end_date=end_date,
        )

        df = quintile_df[["date", "quintile", variable]].merge(
            cpi_df[["date", cpi_type]],
            on="date",
            how="inner",
        )

        self._check_not_empty(df, "compute_real_values")

        # Optional single-quintile filter
        if quintile is not None:
            df = df[df["quintile"] == quintile]
            self._check_not_empty(df, "compute_real_values")

        # Optional CPI rebasing: divide every CPI value by CPI at base_date and
        # multiply by 100, so CPI[base_date] becomes 100
        if base_date is not None:
            base = self._parse_date(base_date, "base_date")
            base_rows = df[df["date"] == pd.Timestamp(base)]
            if base_rows.empty:
                raise ValueError(
                    f"base_date {base_date} is not present in the deflated panel. "
                    f"Use get_date_range() to see what is available."
                )
            base_cpi = base_rows[cpi_type].iloc[0]
            df[cpi_type] = df[cpi_type] / base_cpi * 100

        if (df[cpi_type] <= 0).any():
            raise ValueError(
                f"{cpi_type} contains zero or negative values. "
                "Cannot compute real values."
            )

        df = df.rename(
            columns={
                variable: "nominal_value",
                cpi_type: "cpi",
            }
        )

        df["real_value"] = df["nominal_value"] / df["cpi"] * 100

        return (
            df[
                [
                    "date",
                    "quintile",
                    "nominal_value",
                    "cpi",
                    "real_value",
                ]
            ]
            .sort_values(["quintile", "date"])
            .reset_index(drop=True)
        )

    ## Plots
    def quintile_comparison(
        self,
        date: str,
        metric: str = "affordability_ratio",
    ) -> pd.DataFrame:
        """
        Compare a metric across quintiles at a single observation date.

        Returns a snapshot showing how the metric varies across the five
        income quintiles on one date. The date must be present in the data
        for the chosen metric.

        Parameters
        ----------
        date : str
            Date in 'YYYY-MM-DD' format.
        metric : str, default "affordability_ratio"
            Which metric to compare. One of "affordability_ratio" or
            "health_budget_share".

        Returns
        -------
        pd.DataFrame
            Columns: quintile, <metric>. One row per quintile, ordered Q1..Q5.
        """
        parsed_date = self._parse_date(date, "date")

        # Dispatch to the right metric
        if metric == "affordability_ratio":
            df = self.compute_affordability_ratio()
            value_col = "affordability_ratio"
        elif metric == "health_budget_share":
            df = self.compute_health_budget_share()
            value_col = "health_budget_share"
        else:
            raise ValueError(
                f"{metric} is not a valid metric. "
                f"Valid options: 'affordability_ratio', 'health_budget_share'."
            )

        # Filter to the requested date (snapshot not time series)
        snapshot = df[df["date"] == pd.Timestamp(parsed_date)]

        if snapshot.empty:
            # Tell the user what dates ARE available when chosen is unavailabl
            available = sorted(df["date"].dt.date.unique())
            raise ValueError(
                f"date {date} is not in the {metric} data. "
                f"Earliest available: {available[0]}, "
                f"latest available: {available[-1]}. "
                f"Total available dates: {len(available)}."
            )

        return (
            snapshot[["quintile", value_col]]
            .sort_values("quintile")
            .reset_index(drop=True)
        )

    def plot_healthcare_spending_by_quintile(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        quintiles: list[str] | None = None,
    ) -> Any:
        """
        Plot annual healthcare spending over time, with one line per income quintile.

        Parameters
        ----------
        start_date : str or None, default None
            Start of the date range in 'YYYY-MM-DD'. None means no lower bound.
        end_date : str or None, default None
            End of the date range in 'YYYY-MM-DD'. None means no upper bound.
        quintiles : list[str] or None, default None
            Quintile labels to plot, e.g. ["Q1", "Q3"]. None plots all five.

        Returns
        -------
        matplotlib.axes.Axes
            The axis object of the rendered plot.
        """

        df = self.prepare_quintile_panel(
            start_date=start_date,
            end_date=end_date,
        )

        if quintiles is not None:
            for q in quintiles:
                self._validate_quintile(q)
            df = df[df["quintile"].isin(quintiles)]

        # Create the plot
        plt.figure(figsize=(8, 4))

        for quintile in sorted(df["quintile"].unique()):
            qtl = df[df["quintile"] == quintile]
            plt.plot(
                qtl["date"], qtl["healthcare_spending"], linewidth=1.5, label=quintile
            )

        # Format
        if quintiles is not None and len(quintiles) == 1:
            title = f"Healthcare Spending — {quintiles[0]}"
        else:
            title = "Healthcare Spending by Quintile"
        plt.title(title, fontsize=16)
        plt.ylabel("USD", fontsize=12)
        plt.grid(axis="y", alpha=0.3, linestyle="--")
        plt.legend(title="Quintile")

        ax = plt.gca()
        plt.show()

        return ax

    def plot_income_by_quintile(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        quintiles: list[str] | None = None,
    ) -> Any:
        """
        Plot annual income after tax over time, with one line per income quintile.

        Parameters
        ----------
        start_date : str or None, default None
            Start of the date range in 'YYYY-MM-DD'. None means no lower bound.
        end_date : str or None, default None
            End of the date range in 'YYYY-MM-DD'. None means no upper bound.
        quintiles : list[str] or None, default None
            Quintile labels to plot, e.g. ["Q1", "Q3"]. None plots all five.

        Returns
        -------
        matplotlib.axes.Axes
            The axis object of the rendered plot.
        """

        df = self.prepare_quintile_panel(
            start_date=start_date,
            end_date=end_date,
        )

        if quintiles is not None:
            for q in quintiles:
                self._validate_quintile(q)
            df = df[df["quintile"].isin(quintiles)]

        # Create the plot
        plt.figure(figsize=(8, 4))

        for quintile in sorted(df["quintile"].unique()):
            qtl = df[df["quintile"] == quintile]
            plt.plot(
                qtl["date"], qtl["income_after_tax"], linewidth=1.5, label=quintile
            )

        # Format
        if quintiles is not None and len(quintiles) == 1:
            title = f"Income After Tax — {quintiles[0]}"
        else:
            title = "Income After Tax by Quintile"
        plt.title(title, fontsize=16)
        plt.ylabel("USD", fontsize=12)
        plt.grid(axis="y", alpha=0.3, linestyle="--")
        plt.legend(title="Quintile")

        ax = plt.gca()
        plt.show()

        return ax

    def plot_affordability_ratio(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        quintiles: list[str] | None = None,
    ) -> Any:
        """
        Plot healthcare spending as a percentage of income after tax, 
        with one line per income quintile.

        Parameters
        ----------
        start_date : str or None, default None
            Start of the date range in 'YYYY-MM-DD'. None means no lower bound.
        end_date : str or None, default None
            End of the date range in 'YYYY-MM-DD'. None means no upper bound.
        quintiles : list[str] or None, default None
            Quintile labels to plot, e.g. ["Q1", "Q3"]. None plots all five.

        Returns
        -------
        matplotlib.axes.Axes
            The axis object of the rendered plot.
        """

        df = self.compute_affordability_ratio(
            start_date=start_date,
            end_date=end_date,
        )

        if quintiles is not None:
            for q in quintiles:
                self._validate_quintile(q)
            df = df[df["quintile"].isin(quintiles)]

        # Create the plot
        plt.figure(figsize=(8, 4))

        for quintile in sorted(df["quintile"].unique()):
            qtl = df[df["quintile"] == quintile]
            plt.plot(
                qtl["date"], qtl["affordability_ratio"], linewidth=1.5, label=quintile
            )

        # Format
        if quintiles is not None and len(quintiles) == 1:
            title = f"Healthcare Spending as % of Income After Tax — {quintiles[0]}"
        else:
            title = "Healthcare Spending as % of Income After Tax"
        plt.title(title, fontsize=16)
        plt.ylabel("Percent", fontsize=12)
        plt.grid(axis="y", alpha=0.3, linestyle="--")
        plt.legend(title="Quintile", loc="upper left")

        ax = plt.gca()
        plt.show()

        return ax

    def plot_health_budget_share(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        quintiles: list[str] | None = None,
    ) -> Any:
        """
        Plot healthcare spending as a percentage of total expenditure, 
        with one line per income quintile.

        Parameters
        ----------
        start_date : str or None, default None
            Start of the date range in 'YYYY-MM-DD'. None means no lower bound.
        end_date : str or None, default None
            End of the date range in 'YYYY-MM-DD'. None means no upper bound.
        quintiles : list[str] or None, default None
            Quintile labels to plot, e.g. ["Q1", "Q3"]. None plots all five.

        Returns
        -------
        matplotlib.axes.Axes
            The axis object of the rendered plot.
        """

        df = self.compute_health_budget_share(
            start_date=start_date,
            end_date=end_date,
        )

        if quintiles is not None:
            for q in quintiles:
                self._validate_quintile(q)
            df = df[df["quintile"].isin(quintiles)]

        # Create the plot
        plt.figure(figsize=(8, 4))

        for quintile in sorted(df["quintile"].unique()):
            qtl = df[df["quintile"] == quintile]
            plt.plot(
                qtl["date"], qtl["health_budget_share"], linewidth=1.5, label=quintile
            )

        # Format
        if quintiles is not None and len(quintiles) == 1:
            title = f"Healthcare Spending as % of Total Expenditure — {quintiles[0]}"
        else:
            title = "Healthcare Spending as % of Total Expenditure"
        plt.title(title, fontsize=16)
        plt.ylabel("Percent", fontsize=12)
        plt.grid(axis="y", alpha=0.3, linestyle="--")
        plt.legend(title="Quintile", loc="upper left")

        ax = plt.gca()
        plt.show()

        return ax

    def plot_medical_vs_total_cpi(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        series: list[str] | None = None,
    ) -> Any:
        """
        Plot medical CPI against total CPI over time.

        Parameters
        ----------
        start_date : str or None, default None
            Start of the date range in 'YYYY-MM-DD'. None means no lower bound.
        end_date : str or None, default None
            End of the date range in 'YYYY-MM-DD'. None means no upper bound.
        series : list[str] or None, default None
            CPI series to plot. Accepted values: "cpi_total", "cpi_medical".
            None plots both.

        Returns
        -------
        matplotlib.axes.Axes
            The axis object of the rendered plot.
        """

        df = self.prepare_price_index_panel(
            start_date=start_date,
            end_date=end_date,
        )

        valid_series = {"cpi_total", "cpi_medical"}
        if series is not None:
            invalid = set(series) - valid_series
            if invalid:
                raise ValueError(
                    f"{invalid} are not valid series. "
                    f"Valid options: {sorted(valid_series)}."
                )
        else:
            series = ["cpi_total", "cpi_medical"]

        # Create the plot
        plt.figure(figsize=(8, 4))

        labels = {"cpi_total": "Total CPI", "cpi_medical": "Medical CPI"}
        for s in series:
            plt.plot(df["date"], df[s], linewidth=1.5, label=labels[s])

        # Format
        title = labels[series[0]] if len(series) == 1 else "Medical CPI vs Total CPI"
        plt.title(title, fontsize=16)
        plt.ylabel("Index", fontsize=12)
        plt.grid(axis="y", alpha=0.3, linestyle="--")
        plt.legend(loc="upper left")

        ax = plt.gca()
        plt.show()

        return ax

    def plot_affordability_gap(
        self,
        low_quintile: str = "Q1",
        high_quintile: str = "Q5",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Any:
        """
        Plot the affordability ratio gap between two income quintiles over time.

        Parameters
        ----------
        low_quintile : str, default "Q1"
            Quintile label for the lower income group.
        high_quintile : str, default "Q5"
            Quintile label for the higher income group.
        start_date : str or None, default None
            Start of the date range in 'YYYY-MM-DD'. None means no lower bound.
        end_date : str or None, default None
            End of the date range in 'YYYY-MM-DD'. None means no upper bound.

        Returns
        -------
        matplotlib.axes.Axes
            The axis object of the rendered plot.
        """

        df = self.compute_affordability_gap(
            low_quintile=low_quintile,
            high_quintile=high_quintile,
            start_date=start_date,
            end_date=end_date,
        )

        # Create the plot
        plt.figure(figsize=(8, 4))

        # Single line: the gap between the two quintiles
        plt.plot(df["date"], df["affordability_gap"], color="navy", linewidth=1.5)

        # Format
        plt.title(
            f"Affordability Gap: {low_quintile} - {high_quintile}",
            fontsize=16,
        )
        plt.ylabel("Percentage points", fontsize=12)
        plt.grid(axis="y", alpha=0.3, linestyle="--")

        ax = plt.gca()
        plt.show()

        return ax

    def plot_real_vs_nominal(
        self,
        variable: str = "healthcare_spending",
        quintile: str = "Q1",
        cpi_type: str = "cpi_total",
        start_date: str | None = None,
        end_date: str | None = None,
        series: list[str] | None = None,
    ) -> Any:
        """
        Plot nominal vs real values for one variable and one quintile over time.

        Parameters
        ----------
        variable : str, default "healthcare_spending"
            Variable to deflate. One of "healthcare_spending", "income_after_tax",
            or "total_expenditure".
        quintile : str, default "Q1"
            Quintile label to filter on.
        cpi_type : str, default "cpi_total"
            Price index used for deflation. One of "cpi_total" or "cpi_medical".
        start_date : str or None, default None
            Start of the date range in 'YYYY-MM-DD'. None means no lower bound.
        end_date : str or None, default None
            End of the date range in 'YYYY-MM-DD'. None means no upper bound.
        series : list[str] or None, default None
            Series to plot. Accepted values: "nominal", "real". None plots both.

        Returns
        -------
        matplotlib.axes.Axes
            The axis object of the rendered plot.
        """

        self._validate_quintile(quintile)

        valid_series = {"nominal", "real"}
        if series is not None:
            invalid = set(series) - valid_series
            if invalid:
                raise ValueError(
                    f"{invalid} are not valid series. "
                    f"Valid options: {sorted(valid_series)}."
                )
        else:
            series = ["nominal", "real"]

        df = self.compute_real_values(
            variable=variable,
            cpi_type=cpi_type,
            start_date=start_date,
            end_date=end_date,
        )

        df = df[df["quintile"] == quintile]

        if df.empty:
            raise ValueError(
                f"No data found for quintile {quintile} with variable {variable}."
            )

        pretty_var = {
            "healthcare_spending": "Healthcare Spending",
            "income_after_tax": "Income After Tax",
            "total_expenditure": "Total Expenditure",
        }
        var_label = pretty_var.get(variable, variable)

        plt.figure(figsize=(8, 4))

        cols = {"nominal": ("nominal_value", "Nominal"), "real": ("real_value", "Real")}
        for s in series:
            col, label = cols[s]
            plt.plot(df["date"], df[col], linewidth=1.5, label=label)

        if series == ["nominal"]:
            title = f"Nominal {var_label} ({quintile})"
        elif series == ["real"]:
            title = f"Real {var_label} ({quintile}, deflated by {cpi_type})"
        else:
            title = f"Nominal vs Real {var_label} ({quintile}, deflated by {cpi_type})"
        plt.title(title, fontsize=14)
        plt.ylabel("USD", fontsize=12)
        plt.grid(axis="y", alpha=0.3, linestyle="--")
        plt.legend(loc="upper left")

        ax = plt.gca()
        plt.show()

        return ax

    def affordability_summary_table(
        self,
        metric: str = "affordability_ratio",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """
        Summarize a per-quintile metric across a time window.

        For each quintile, reports the value at the earliest and latest
        observation in the window, the change between them (in (absolute) percentage 
        points and in (relative) %),
        and summary statistics across the whole window (mean, std).

        Parameters
        ----------
        metric : str, default "affordability_ratio"
            Which metric to summarize. One of "affordability_ratio" or
            "health_budget_share".
        start_date, end_date : str or None
            Inclusive date bounds in 'YYYY-MM-DD'.

        Returns
        -------
        pd.DataFrame
            Columns: quintile, start_date, end_date, start_value, end_value,
            change_pp, percent_change, mean, std. One row per quintile,
            sorted by change_pp descending. Numeric columns rounded to 2 dp.
        """
        if metric == "affordability_ratio":
            df = self.compute_affordability_ratio(
                start_date=start_date,
                end_date=end_date,
            )
            value_col = "affordability_ratio"
        elif metric == "health_budget_share":
            df = self.compute_health_budget_share(
                start_date=start_date,
                end_date=end_date,
            )
            value_col = "health_budget_share"
        else:
            raise ValueError(
                f"{metric} is not a valid metric. "
                f"Valid options: 'affordability_ratio', 'health_budget_share'."
            )

        self._check_not_empty(df, "affordability_summary_table")

        rows = []
        for quintile, group in df.sort_values("date").groupby("quintile"):
            first = group.iloc[0]
            last = group.iloc[-1]

            change_pp = last[value_col] - first[value_col]

            # Guard against a zero starting value: percent_change is undefined
            if first[value_col] > 0:
                percent_change = (last[value_col] / first[value_col] - 1) * 100
            else:
                percent_change = float("nan")

            rows.append(
                {
                    "quintile": quintile,
                    "start_date": first["date"],
                    "end_date": last["date"],
                    "start_value": first[value_col],
                    "end_value": last[value_col],
                    "change_pp": change_pp,
                    "percent_change": percent_change,
                    "mean": group[value_col].mean(),
                    "std": group[value_col].std(),
                }
            )

        result = pd.DataFrame(rows).sort_values("change_pp", ascending=False)

        numeric_cols = [
            "start_value",
            "end_value",
            "change_pp",
            "percent_change",
            "mean",
            "std",
        ]
        result[numeric_cols] = result[numeric_cols].round(2)

        return result.reset_index(drop=True)

    def close(self) -> None:
        """
        Close the database connection. Safe to call multiple times.
        """
        self.con.close()

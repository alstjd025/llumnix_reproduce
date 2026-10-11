#!/usr/bin/env python3
"""Parse application-side experiment metrics into plotting-friendly CSV files."""

import argparse
import csv
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd


NUMERIC_COLUMNS = [
    "start_time",
    "end_time",
    "latency",
    "input_tokens",
    "output_tokens",
    "first_token_latency",
    "tbt_p90_ms",
    "tbt_p95_ms",
    "call_index",
    "total_calls_expected",
    "job_submit_time",
    "job_end_time",
    "job_timeout_sec",
    "transition_time",
]

CANONICAL_COLUMNS = [
    "task_id",
    "iteration",
    "agent",
    "call_index",
    "total_calls_expected",
    "start_time",
    "end_time",
    "latency",
    "input_tokens",
    "output_tokens",
    "first_token_latency",
    "decode_speed_tps",
    "gpu_memory_mb",
    "kv_cache_usage_pct",
    "transition_time",
    "tokenizer_mode",
    "stream_fallback_used",
    "tbt_available",
    "stream_chunks",
    "streamed_output_tokens_est",
    "first_chunk_tokens_est",
    "tbt_mean_ms",
    "tbt_p50_ms",
    "tbt_p75_ms",
    "tbt_p80_ms",
    "tbt_p85_ms",
    "tbt_p90_ms",
    "tbt_p95_ms",
    "tbt_max_ms",
    "tbt_sample_count",
    "is_timeout",
    "is_error",
    "is_rejected",
    "rejection_reason",
    "is_job_timeout",
    "job_timeout_sec",
    "is_server_terminated",
    "job_submit_time",
    "job_end_time",
    "job_completed",
    "concurrency_level",
    "success",
    "error_msg",
    "timestamp",
]

LEGACY_COLUMNS = [
    col
    for col in CANONICAL_COLUMNS
    if col not in {
        "is_rejected",
        "rejection_reason",
        "is_job_timeout",
        "job_timeout_sec",
        "is_server_terminated",
    }
]

PRE_REJECTION_COLUMNS = [
    col for col in CANONICAL_COLUMNS if col not in {"is_rejected", "rejection_reason"}
]


def _as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def _read_metrics_csv(csv_path: Path) -> pd.DataFrame:
    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        for raw in reader:
            if not raw or raw[0] == "task_id":
                continue
            if len(raw) == len(CANONICAL_COLUMNS):
                row = dict(zip(CANONICAL_COLUMNS, raw))
            elif len(raw) == len(PRE_REJECTION_COLUMNS):
                row = {col: "" for col in CANONICAL_COLUMNS}
                row.update(dict(zip(PRE_REJECTION_COLUMNS, raw)))
            elif len(raw) == len(LEGACY_COLUMNS):
                row = {col: "" for col in CANONICAL_COLUMNS}
                row.update(dict(zip(LEGACY_COLUMNS, raw)))
            else:
                raise ValueError(
                    f"Unexpected metrics.csv row width in {csv_path}: "
                    f"{len(raw)} fields"
                )
            rows.append(row)
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)


def load_metrics(result_dir: str) -> pd.DataFrame:
    csv_path = Path(result_dir) / "metrics.csv"
    if not csv_path.is_file():
        raise FileNotFoundError(f"metrics.csv not found: {csv_path}")
    df = _read_metrics_csv(csv_path)
    df.columns = df.columns.str.strip()
    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "task_id" in df.columns:
        df["base_task_id"] = (
            df["task_id"].astype(str).str.replace(r"__replay\d+$", "", regex=True)
        )
    return df


def split_metrics(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    agent_col = df["agent"].astype(str)
    calls = df[agent_col.str.startswith("chain_call")].copy()
    jobs = df[agent_col == "job_summary"].copy()
    if "success" in calls.columns:
        calls["success_bool"] = _as_bool(calls["success"])
    if "is_rejected" in calls.columns:
        calls["is_rejected_bool"] = _as_bool(calls["is_rejected"])
    if "job_completed" in jobs.columns:
        jobs["job_completed_bool"] = _as_bool(jobs["job_completed"])
    if "is_rejected" in jobs.columns:
        jobs["is_rejected_bool"] = _as_bool(jobs["is_rejected"])
    return calls, jobs


def load_run_config(result_dir: str) -> dict:
    path = Path(result_dir) / "run_config.json"
    if path.is_file():
        with open(path) as f:
            return json.load(f)
    return {}


def resolve_baseline_dir(result_dir: str, baseline_dir: str | None) -> str | None:
    if not baseline_dir:
        return None
    path = Path(baseline_dir)
    if path.is_dir():
        return str(path)
    if not path.is_absolute():
        result_path = Path(result_dir)
        candidate = result_path.parent.parent / path
        if candidate.is_dir():
            return str(candidate)
    return baseline_dir


def build_call_baseline(baseline_calls: pd.DataFrame) -> pd.DataFrame:
    cols = ["base_task_id", "call_index", "latency"]
    baseline = (
        baseline_calls[cols]
        .dropna(subset=cols)
        .groupby(["base_task_id", "call_index"], as_index=False)
        .median(numeric_only=True)
        .rename(columns={"latency": "baseline_call_latency"})
    )
    return baseline


def build_job_baseline(baseline_jobs: pd.DataFrame) -> pd.DataFrame:
    jobs = baseline_jobs.copy()
    if "job_completed" in jobs.columns:
        jobs = jobs[_as_bool(jobs["job_completed"])]
    return (
        jobs[["base_task_id", "latency"]]
        .dropna(subset=["base_task_id", "latency"])
        .groupby("base_task_id", as_index=False)
        .median(numeric_only=True)
        .rename(columns={"latency": "baseline_job_latency"})
    )


def add_tau_goodput(
    calls: pd.DataFrame,
    jobs: pd.DataFrame,
    baseline_dir: str | None,
    tau: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    calls = calls.copy()
    jobs = jobs.copy()
    calls["call_goodput_bool"] = None
    calls["call_slowdown"] = np.nan
    calls["baseline_call_latency"] = np.nan
    calls["call_latency_threshold"] = np.nan
    jobs["job_goodput_bool"] = None
    jobs["job_slowdown"] = np.nan
    jobs["baseline_job_latency"] = np.nan
    jobs["job_latency_threshold"] = np.nan

    if not baseline_dir:
        return calls, jobs

    baseline_df = load_metrics(baseline_dir)
    baseline_calls, baseline_jobs = split_metrics(baseline_df)

    call_baseline = build_call_baseline(baseline_calls)
    if not call_baseline.empty and not calls.empty:
        calls = calls.merge(
            call_baseline,
            on=["base_task_id", "call_index"],
            how="left",
            suffixes=("", "_from_baseline"),
            validate="many_to_one",
        )
        calls["baseline_call_latency"] = calls[
            "baseline_call_latency_from_baseline"
        ].combine_first(calls["baseline_call_latency"])
        calls = calls.drop(columns=["baseline_call_latency_from_baseline"])
        calls["call_latency_threshold"] = calls["baseline_call_latency"] * tau
        calls["call_slowdown"] = calls["latency"] / calls["baseline_call_latency"]
        classifiable = calls["baseline_call_latency"].notna() & (calls["baseline_call_latency"] > 0)
        ok = (
            calls["success_bool"]
            & classifiable
            & (calls["latency"] < calls["call_latency_threshold"])
        )
        calls.loc[classifiable, "call_goodput_bool"] = ok.loc[classifiable]

    job_baseline = build_job_baseline(baseline_jobs)
    if not job_baseline.empty and not jobs.empty:
        jobs = jobs.merge(
            job_baseline,
            on="base_task_id",
            how="left",
            suffixes=("", "_from_baseline"),
            validate="many_to_one",
        )
        jobs["baseline_job_latency"] = jobs[
            "baseline_job_latency_from_baseline"
        ].combine_first(jobs["baseline_job_latency"])
        jobs = jobs.drop(columns=["baseline_job_latency_from_baseline"])
        jobs["job_latency_threshold"] = jobs["baseline_job_latency"] * tau
        jobs["job_slowdown"] = jobs["latency"] / jobs["baseline_job_latency"]
        classifiable = jobs["baseline_job_latency"].notna() & (jobs["baseline_job_latency"] > 0)
        # Run-boundary cutoffs are not SLO violations: a job that the runner
        # terminated when the run ended (is_server_terminated=True) and was
        # neither rejected at admission nor timed out by tau has an unknown
        # outcome. Treat such jobs as unclassified so they drop out of the
        # goodput-rate denominator instead of being counted as SLO misses.
        server_term = (
            _as_bool(jobs["is_server_terminated"])
            if "is_server_terminated" in jobs.columns
            else pd.Series(False, index=jobs.index)
        )
        job_timeout = (
            _as_bool(jobs["is_job_timeout"])
            if "is_job_timeout" in jobs.columns
            else pd.Series(False, index=jobs.index)
        )
        rejected = (
            jobs["is_rejected_bool"].astype(bool)
            if "is_rejected_bool" in jobs.columns
            else pd.Series(False, index=jobs.index)
        )
        boundary_cutoff = server_term & ~rejected & ~job_timeout

        # Rejected-at-start exclusion: a job whose very first call (lowest
        # call_index in the calls table) was rejected by admission control
        # never actually ran — the server declined before producing any
        # output. Counting it as an SLO miss conflates "we attempted and
        # failed" with "we never tried", so exclude these from the
        # goodput-rate denominator too. Mid-chain rejected jobs (some
        # calls succeeded before reject) stay in the denominator because
        # the system did expend effort on them.
        rejected_at_start_ids: set = set()
        if (
            not calls.empty
            and "task_id" in calls.columns
            and "call_index" in calls.columns
            and "is_rejected_bool" in calls.columns
        ):
            first_calls = (
                calls.sort_values("call_index")
                .drop_duplicates(subset="task_id", keep="first")
            )
            rej_first = first_calls["is_rejected_bool"].fillna(False).astype(bool)
            rejected_at_start_ids = set(first_calls.loc[rej_first, "task_id"])
        if "task_id" in jobs.columns and rejected_at_start_ids:
            rejected_at_start = jobs["task_id"].isin(rejected_at_start_ids)
        else:
            rejected_at_start = pd.Series(False, index=jobs.index)

        classifiable = classifiable & ~boundary_cutoff & ~rejected_at_start
        ok = (
            jobs["job_completed_bool"]
            & classifiable
            & (jobs["latency"] < jobs["job_latency_threshold"])
        )
        jobs.loc[classifiable, "job_goodput_bool"] = ok.loc[classifiable]

    return calls, jobs


def build_summary(calls: pd.DataFrame, jobs: pd.DataFrame) -> pd.DataFrame:
    total_calls = len(calls)
    total_jobs = len(jobs)
    completed_jobs = int(jobs["job_completed_bool"].sum()) if total_jobs else 0
    successful_calls = int(calls["success_bool"].sum()) if total_calls else 0
    rejected_calls = (
        int(calls["is_rejected_bool"].sum())
        if total_calls and "is_rejected_bool" in calls
        else 0
    )
    rejected_jobs = (
        int(jobs["is_rejected_bool"].sum())
        if total_jobs and "is_rejected_bool" in jobs
        else 0
    )
    classifiable_calls = int(calls["call_goodput_bool"].notna().sum()) if total_calls else 0
    goodput_calls = int(calls["call_goodput_bool"].fillna(False).sum()) if total_calls else 0
    classifiable_jobs = int(jobs["job_goodput_bool"].notna().sum()) if total_jobs else 0
    goodput_jobs = int(jobs["job_goodput_bool"].fillna(False).sum()) if total_jobs else 0

    total_tokens = float(calls["input_tokens"].sum() + calls["output_tokens"].sum())
    non_goodput_ids = (
        set(jobs.loc[jobs["job_goodput_bool"] != True, "task_id"]) if total_jobs else set()
    )
    wasted_calls = calls[calls["task_id"].isin(non_goodput_ids)]
    wasted_tokens = float(wasted_calls["input_tokens"].sum() + wasted_calls["output_tokens"].sum())

    if total_calls and calls["start_time"].notna().any() and calls["end_time"].notna().any():
        duration_s = float(calls["end_time"].max() - calls["start_time"].min())
    else:
        duration_s = np.nan

    output_tps = (
        float(calls["output_tokens"].sum()) / duration_s
        if duration_s and duration_s > 0
        else np.nan
    )

    return pd.DataFrame([{
        "total_calls": total_calls,
        "successful_calls": successful_calls,
        "call_success_rate": successful_calls / total_calls if total_calls else np.nan,
        "rejected_calls": rejected_calls,
        "call_rejection_rate": rejected_calls / total_calls if total_calls else np.nan,
        "classifiable_calls": classifiable_calls,
        "goodput_calls": goodput_calls,
        "call_goodput_rate": goodput_calls / classifiable_calls if classifiable_calls else np.nan,
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "job_completion_rate": completed_jobs / total_jobs if total_jobs else np.nan,
        "rejected_jobs": rejected_jobs,
        "job_rejection_rate": rejected_jobs / total_jobs if total_jobs else np.nan,
        "classifiable_jobs": classifiable_jobs,
        "goodput_jobs": goodput_jobs,
        "job_goodput_rate": goodput_jobs / classifiable_jobs if classifiable_jobs else np.nan,
        "total_tokens": total_tokens,
        "wasted_tokens": wasted_tokens,
        "wasted_compute_ratio": wasted_tokens / total_tokens if total_tokens else np.nan,
        "duration_s": duration_s,
        "output_tokens_per_s": output_tps,
    }])


def build_timeseries(calls: pd.DataFrame, jobs: pd.DataFrame) -> pd.DataFrame:
    if calls.empty:
        return pd.DataFrame()

    t0_candidates = [calls["start_time"].min()]
    if not jobs.empty:
        t0_candidates.append(jobs["job_submit_time"].min())
    t0 = float(np.nanmin(t0_candidates))

    calls = calls.copy()
    jobs = jobs.copy()
    calls["rel_start_min"] = (calls["start_time"] - t0) / 60.0
    calls["rel_end_min"] = (calls["end_time"] - t0) / 60.0
    if not jobs.empty:
        jobs["rel_submit_min"] = (jobs["job_submit_time"] - t0) / 60.0
        jobs["rel_end_min"] = (jobs["job_end_time"] - t0) / 60.0

    max_min = float(np.nanmax([
        calls["rel_end_min"].max(),
        jobs["rel_end_min"].max() if not jobs.empty else 0,
    ]))
    minutes = np.arange(0, int(np.ceil(max_min)) + 1)
    rows = []

    calls["tokens"] = calls["input_tokens"].fillna(0) + calls["output_tokens"].fillna(0)
    if "is_rejected_bool" not in calls:
        calls["is_rejected_bool"] = False
    if not jobs.empty:
        job_goodput = jobs.set_index("task_id")["job_goodput_bool"]
        calls["job_goodput_bool"] = calls["task_id"].map(job_goodput)

    for minute in minutes:
        call_window = calls[(calls["rel_end_min"] >= minute) & (calls["rel_end_min"] < minute + 1)]
        job_window = (
            jobs[(jobs["rel_end_min"] >= minute) & (jobs["rel_end_min"] < minute + 1)]
            if not jobs.empty
            else jobs
        )
        calls_so_far = calls[calls["rel_end_min"] < minute + 1]
        jobs_so_far = jobs[jobs["rel_end_min"] < minute + 1] if not jobs.empty else jobs
        active_calls = calls[
            (calls["rel_start_min"] <= minute + 0.5)
            & (calls["rel_end_min"] >= minute + 0.5)
        ]

        classifiable_call_window = call_window.dropna(subset=["call_goodput_bool"])
        if not classifiable_call_window.empty:
            window_call_goodput_rate = float(classifiable_call_window["call_goodput_bool"].mean())
        else:
            window_call_goodput_rate = np.nan

        classifiable_calls_so_far = calls_so_far.dropna(subset=["call_goodput_bool"])
        if not classifiable_calls_so_far.empty:
            call_goodput_rate = float(classifiable_calls_so_far["call_goodput_bool"].mean())
        else:
            call_goodput_rate = np.nan

        classifiable_job_window = job_window.dropna(subset=["job_goodput_bool"])
        if not classifiable_job_window.empty:
            window_job_goodput_rate = float(classifiable_job_window["job_goodput_bool"].mean())
        else:
            window_job_goodput_rate = np.nan

        classifiable_jobs_so_far = jobs_so_far.dropna(subset=["job_goodput_bool"])
        if not classifiable_jobs_so_far.empty:
            job_goodput_rate = float(classifiable_jobs_so_far["job_goodput_bool"].mean())
        else:
            job_goodput_rate = np.nan

        if not calls_so_far.empty:
            goodput_tokens = float(
                calls_so_far.loc[
                    calls_so_far["job_goodput_bool"] == True, "tokens"
                ].sum()
            )
            wasted_tokens = float(
                calls_so_far.loc[
                    calls_so_far["job_goodput_bool"] != True, "tokens"
                ].sum()
            )
            classified_total_tokens = float(calls_so_far["tokens"].sum())
            running_wcr = (
                wasted_tokens / classified_total_tokens
                if classified_total_tokens
                else np.nan
            )
        else:
            goodput_tokens = 0.0
            wasted_tokens = 0.0
            classified_total_tokens = 0.0
            running_wcr = np.nan

        rows.append({
            "minute": minute,
            "output_tokens_per_s": float(call_window["output_tokens"].sum()) / 60.0,
            "active_calls": len(active_calls),
            "rejected_calls": int(call_window["is_rejected_bool"].sum()),
            "cumulative_rejected_calls": int(calls_so_far["is_rejected_bool"].sum()),
            "window_call_rejection_rate": (
                float(call_window["is_rejected_bool"].mean()) if len(call_window) else np.nan
            ),
            "call_rejection_rate": (
                float(calls_so_far["is_rejected_bool"].mean()) if len(calls_so_far) else np.nan
            ),
            "call_goodput_rate": call_goodput_rate,
            "job_goodput_rate": job_goodput_rate,
            "window_call_goodput_rate": window_call_goodput_rate,
            "window_job_goodput_rate": window_job_goodput_rate,
            "running_wcr": running_wcr,
            "goodput_tokens": goodput_tokens,
            "wasted_tokens": wasted_tokens,
            "classified_total_tokens": classified_total_tokens,
            "total_tokens": float(calls_so_far["tokens"].sum()),
            "unknown_tokens": float(
                calls_so_far.loc[calls_so_far["job_goodput_bool"].isna(), "tokens"].sum()
            ),
            "call_count": len(call_window),
            "classifiable_call_count": len(classifiable_call_window),
            "cumulative_call_count": len(classifiable_calls_so_far),
            "job_count": len(job_window),
            "classifiable_job_count": len(classifiable_job_window),
            "cumulative_job_count": len(classifiable_jobs_so_far),
        })

    return pd.DataFrame(rows)


CALL_JOB_BUCKET_ORDER = [
    "call_goodput__job_not_goodput",
    "call_not_goodput__job_not_goodput",
    "call_goodput__job_goodput",
    "call_not_goodput__job_goodput",
    "unclassified",
]

JOB_CALL_BUCKET_ORDER = [
    "all_observed_calls_goodput__job_not_goodput",
    "some_observed_call_not_goodput__job_not_goodput",
    "all_observed_calls_goodput__job_goodput",
    "some_observed_call_not_goodput__job_goodput",
    "no_classifiable_observed_calls__job_not_goodput",
    "no_classifiable_observed_calls__job_goodput",
    "unclassified",
]


def _as_nullable_bool(series: pd.Series) -> pd.Series:
    out = pd.Series(pd.NA, index=series.index, dtype="boolean")
    known = series.notna()
    out.loc[known] = series.loc[known].astype(str).str.lower().isin({"true", "1", "yes"})
    return out


def _call_job_bucket(call_goodput: object, job_goodput: object) -> str:
    if pd.isna(call_goodput) or pd.isna(job_goodput):
        return "unclassified"
    if bool(call_goodput) and not bool(job_goodput):
        return "call_goodput__job_not_goodput"
    if not bool(call_goodput) and not bool(job_goodput):
        return "call_not_goodput__job_not_goodput"
    if bool(call_goodput) and bool(job_goodput):
        return "call_goodput__job_goodput"
    return "call_not_goodput__job_goodput"


def _job_call_bucket(row: pd.Series) -> str:
    job_goodput = row["job_goodput_bool"]
    if pd.isna(job_goodput):
        return "unclassified"
    suffix = "job_goodput" if bool(job_goodput) else "job_not_goodput"
    if row["classifiable_call_count"] == 0:
        return f"no_classifiable_observed_calls__{suffix}"
    if row["call_not_goodput_count"] == 0:
        return f"all_observed_calls_goodput__{suffix}"
    return f"some_observed_call_not_goodput__{suffix}"


def build_call_job_goodput_tables(
    calls: pd.DataFrame,
    jobs: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    call_cols = [
        "task_id",
        "base_task_id",
        "iteration",
        "agent",
        "call_index",
        "total_calls_expected",
        "start_time",
        "end_time",
        "latency",
        "input_tokens",
        "output_tokens",
        "success_bool",
        "call_goodput_bool",
        "call_slowdown",
        "baseline_call_latency",
        "call_latency_threshold",
    ]
    job_cols = [
        "task_id",
        "job_completed_bool",
        "job_goodput_bool",
        "job_slowdown",
        "baseline_job_latency",
        "job_latency_threshold",
        "is_job_timeout",
        "is_server_terminated",
        "job_submit_time",
        "job_end_time",
    ]
    call_cols = [col for col in call_cols if col in calls.columns]
    job_cols = [col for col in job_cols if col in jobs.columns]

    call_table = calls[call_cols].copy()
    job_table_for_merge = jobs[job_cols].copy()
    call_table = call_table.merge(job_table_for_merge, on="task_id", how="left", validate="many_to_one")

    call_bool = _as_nullable_bool(call_table["call_goodput_bool"])
    job_bool = _as_nullable_bool(call_table["job_goodput_bool"])
    call_table["call_goodput_classifiable"] = call_bool.notna()
    call_table["job_goodput_classifiable"] = job_bool.notna()
    call_table["call_job_goodput_bucket"] = [
        _call_job_bucket(call_value, job_value)
        for call_value, job_value in zip(call_bool, job_bool)
    ]
    call_table["total_tokens"] = (
        pd.to_numeric(call_table.get("input_tokens", 0), errors="coerce").fillna(0)
        + pd.to_numeric(call_table.get("output_tokens", 0), errors="coerce").fillna(0)
    )

    grouped = (
        call_table.groupby("call_job_goodput_bucket", dropna=False)
        .agg(
            call_count=("task_id", "count"),
            total_tokens=("total_tokens", "sum"),
            input_tokens=("input_tokens", "sum"),
            output_tokens=("output_tokens", "sum"),
            median_call_slowdown=("call_slowdown", "median"),
            p90_call_slowdown=("call_slowdown", lambda s: s.quantile(0.90)),
        )
        .reindex(CALL_JOB_BUCKET_ORDER)
        .fillna({"call_count": 0, "total_tokens": 0, "input_tokens": 0, "output_tokens": 0})
        .reset_index()
        .rename(columns={"index": "call_job_goodput_bucket"})
    )
    total_calls = float(grouped["call_count"].sum())
    total_tokens = float(grouped["total_tokens"].sum())
    grouped["call_rate"] = grouped["call_count"] / total_calls if total_calls else np.nan
    grouped["token_rate"] = grouped["total_tokens"] / total_tokens if total_tokens else np.nan

    job_bool_by_task = _as_nullable_bool(jobs["job_goodput_bool"]) if "job_goodput_bool" in jobs else pd.Series(
        pd.NA, index=jobs.index, dtype="boolean"
    )
    job_table = jobs.copy()
    job_table["job_goodput_bool"] = job_bool_by_task
    per_job_calls = (
        call_table.assign(_call_goodput_bool=call_bool)
        .groupby("task_id")
        .agg(
            observed_call_count=("call_index", "count"),
            classifiable_call_count=("_call_goodput_bool", lambda s: int(s.notna().sum())),
            call_goodput_count=("_call_goodput_bool", lambda s: int((s == True).sum())),
            call_not_goodput_count=("_call_goodput_bool", lambda s: int((s == False).sum())),
            observed_call_tokens=("total_tokens", "sum"),
        )
        .reset_index()
    )
    job_table = job_table.merge(per_job_calls, on="task_id", how="left")
    count_cols = [
        "observed_call_count",
        "classifiable_call_count",
        "call_goodput_count",
        "call_not_goodput_count",
        "observed_call_tokens",
    ]
    job_table[count_cols] = job_table[count_cols].fillna(0)
    job_table["job_call_goodput_bucket"] = job_table.apply(_job_call_bucket, axis=1)

    job_summary = (
        job_table.groupby("job_call_goodput_bucket", dropna=False)
        .agg(
            job_count=("task_id", "count"),
            observed_call_count=("observed_call_count", "sum"),
            classifiable_call_count=("classifiable_call_count", "sum"),
            call_goodput_count=("call_goodput_count", "sum"),
            call_not_goodput_count=("call_not_goodput_count", "sum"),
            observed_call_tokens=("observed_call_tokens", "sum"),
            median_job_slowdown=("job_slowdown", "median"),
            p90_job_slowdown=("job_slowdown", lambda s: s.quantile(0.90)),
        )
        .reindex(JOB_CALL_BUCKET_ORDER)
        .fillna(
            {
                "job_count": 0,
                "observed_call_count": 0,
                "classifiable_call_count": 0,
                "call_goodput_count": 0,
                "call_not_goodput_count": 0,
                "observed_call_tokens": 0,
            }
        )
        .reset_index()
        .rename(columns={"index": "job_call_goodput_bucket"})
    )
    total_jobs = float(job_summary["job_count"].sum())
    job_summary["job_rate"] = job_summary["job_count"] / total_jobs if total_jobs else np.nan
    return call_table, grouped, job_summary


def build_transition_adjusted_job_tables(
    jobs: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build separate job timing tables with and without non-LLM transition time."""
    columns = [
        "task_id",
        "base_task_id",
        "job_completed_bool",
        "latency_with_transition_s",
        "transition_time_s",
        "latency_without_transition_s",
        "baseline_job_latency",
        "job_latency_threshold",
        "job_slowdown_with_transition",
        "job_slowdown_without_transition",
        "job_goodput_with_transition_bool",
        "job_goodput_without_transition_bool",
        "is_job_timeout",
        "is_server_terminated",
        "job_submit_time",
        "job_end_time",
    ]
    if jobs.empty:
        return pd.DataFrame(columns=columns), pd.DataFrame()

    job_timing = pd.DataFrame(index=jobs.index)
    for col in ["task_id", "base_task_id", "job_completed_bool"]:
        job_timing[col] = jobs[col] if col in jobs else pd.NA

    latency = pd.to_numeric(jobs["latency"], errors="coerce")
    transition_time = pd.to_numeric(jobs["transition_time"], errors="coerce").fillna(0.0)
    baseline = pd.to_numeric(jobs["baseline_job_latency"], errors="coerce")
    threshold = pd.to_numeric(jobs["job_latency_threshold"], errors="coerce")

    job_timing["latency_with_transition_s"] = latency
    job_timing["transition_time_s"] = transition_time
    job_timing["latency_without_transition_s"] = (latency - transition_time).clip(lower=0)
    job_timing["baseline_job_latency"] = baseline
    job_timing["job_latency_threshold"] = threshold
    job_timing["job_slowdown_with_transition"] = latency / baseline
    job_timing["job_slowdown_without_transition"] = (
        job_timing["latency_without_transition_s"] / baseline
    )

    classifiable = baseline.notna() & (baseline > 0)
    completed = _as_nullable_bool(jobs["job_completed"]) if "job_completed" in jobs else pd.Series(
        pd.NA,
        index=jobs.index,
        dtype="boolean",
    )
    goodput_with = pd.Series(pd.NA, index=jobs.index, dtype="boolean")
    goodput_without = pd.Series(pd.NA, index=jobs.index, dtype="boolean")
    goodput_with.loc[classifiable] = (
        completed.loc[classifiable]
        & (job_timing.loc[classifiable, "latency_with_transition_s"] < threshold.loc[classifiable])
    )
    goodput_without.loc[classifiable] = (
        completed.loc[classifiable]
        & (job_timing.loc[classifiable, "latency_without_transition_s"] < threshold.loc[classifiable])
    )
    job_timing["job_goodput_with_transition_bool"] = goodput_with
    job_timing["job_goodput_without_transition_bool"] = goodput_without

    for col in ["is_job_timeout", "is_server_terminated", "job_submit_time", "job_end_time"]:
        job_timing[col] = jobs[col] if col in jobs else pd.NA

    classifiable_count = int(classifiable.sum())
    goodput_with_count = int((goodput_with == True).sum())
    goodput_without_count = int((goodput_without == True).sum())
    summary = pd.DataFrame(
        [
            {
                "total_jobs": len(job_timing),
                "classifiable_jobs": classifiable_count,
                "goodput_jobs_with_transition": goodput_with_count,
                "goodput_jobs_without_transition": goodput_without_count,
                "job_goodput_with_transition_rate": (
                    goodput_with_count / classifiable_count if classifiable_count else np.nan
                ),
                "job_goodput_without_transition_rate": (
                    goodput_without_count / classifiable_count if classifiable_count else np.nan
                ),
                "median_transition_time_s": float(transition_time.median()),
                "p90_transition_time_s": float(transition_time.quantile(0.90)),
                "total_transition_time_s": float(transition_time.sum()),
                "median_job_slowdown_with_transition": float(
                    job_timing["job_slowdown_with_transition"].median()
                ),
                "median_job_slowdown_without_transition": float(
                    job_timing["job_slowdown_without_transition"].median()
                ),
                "p90_job_slowdown_with_transition": float(
                    job_timing["job_slowdown_with_transition"].quantile(0.90)
                ),
                "p90_job_slowdown_without_transition": float(
                    job_timing["job_slowdown_without_transition"].quantile(0.90)
                ),
            }
        ]
    )
    return job_timing[columns], summary


def build_parallel_call_tables(
    result_dir: str,
    calls: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build parallel execution-round analysis tables when parallel_calls.csv exists."""
    path = Path(result_dir) / "parallel_calls.csv"
    if not path.is_file():
        return pd.DataFrame(), pd.DataFrame()

    parallel_calls = pd.read_csv(path)
    if parallel_calls.empty:
        return parallel_calls, pd.DataFrame()

    numeric_cols = [
        "call_index",
        "total_calls_expected",
        "execution_round",
        "round_size",
        "tool_delay_before_round_s",
        "tool_delay_before_call_s",
        "round_start_time",
        "round_end_time",
        "call_recorded_at",
    ]
    for col in numeric_cols:
        if col in parallel_calls:
            parallel_calls[col] = pd.to_numeric(parallel_calls[col], errors="coerce")

    if not calls.empty:
        call_metrics = calls[
            [
                col
                for col in [
                    "task_id",
                    "call_index",
                    "start_time",
                    "end_time",
                    "latency",
                    "input_tokens",
                    "output_tokens",
                    "success_bool",
                    "is_rejected_bool",
                    "rejection_reason",
                    "call_goodput_bool",
                    "call_slowdown",
                    "baseline_call_latency",
                    "call_latency_threshold",
                ]
                if col in calls.columns
            ]
        ].copy()
        parallel_calls = parallel_calls.merge(
            call_metrics,
            on=["task_id", "call_index"],
            how="left",
            validate="one_to_one",
        )

    parallel_calls["is_parallel_call_bool"] = _as_bool(parallel_calls["is_parallel_call"])
    parallel_calls["is_round_leader_bool"] = _as_bool(parallel_calls["is_round_leader"])

    group_cols = ["task_id", "execution_round"]
    round_summary = (
        parallel_calls.groupby(group_cols, dropna=False)
        .agg(
            base_task_id=("base_task_id", "first"),
            parallel_group_id=("parallel_group_id", "first"),
            round_size=("round_size", "max"),
            stage_list=("stage", lambda s: ",".join(s.astype(str))),
            call_indices=("call_index", lambda s: ",".join(str(int(v)) for v in s.dropna())),
            depends_on_call_indices=("depends_on_call_indices", "first"),
            tool_delay_before_round_s=("tool_delay_before_round_s", "max"),
            round_start_time=("round_start_time", "min"),
            round_end_time=("round_end_time", "max"),
            first_call_start_time=("start_time", "min"),
            last_call_end_time=("end_time", "max"),
            max_call_latency_s=("latency", "max"),
            sum_call_latency_s=("latency", "sum"),
            input_tokens=("input_tokens", "sum"),
            output_tokens=("output_tokens", "sum"),
            successful_calls=("success_bool", lambda s: int(s.fillna(False).sum())),
            goodput_calls=("call_goodput_bool", lambda s: int(s.fillna(False).sum())),
        )
        .reset_index()
    )
    round_summary["round_wall_time_s"] = (
        round_summary["last_call_end_time"] - round_summary["first_call_start_time"]
    )
    round_summary["call_latency_overlap_savings"] = (
        round_summary["sum_call_latency_s"] - round_summary["round_wall_time_s"]
    )
    return parallel_calls, round_summary


def parse_application_metrics(
    result_dir: str,
    output_dir: str,
    baseline_dir: str | None = None,
    tau: float | None = None,
) -> dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    config = load_run_config(result_dir)
    baseline_dir = baseline_dir or config.get("baseline_dir")
    baseline_dir = resolve_baseline_dir(result_dir, baseline_dir)
    tau = float(tau if tau is not None else config.get("tau", 1.0))
    df = load_metrics(result_dir)
    calls, jobs = split_metrics(df)
    calls, jobs = add_tau_goodput(calls, jobs, baseline_dir, tau)

    outputs = {
        "calls": os.path.join(output_dir, "application_calls.csv"),
        "jobs": os.path.join(output_dir, "application_jobs.csv"),
        "summary": os.path.join(output_dir, "application_summary.csv"),
        "timeseries": os.path.join(output_dir, "application_timeseries.csv"),
        "call_job_goodput": os.path.join(output_dir, "application_call_job_goodput.csv"),
        "call_job_goodput_summary": os.path.join(
            output_dir,
            "application_call_job_goodput_summary.csv",
        ),
        "job_call_goodput_summary": os.path.join(
            output_dir,
            "application_job_call_goodput_summary.csv",
        ),
        "job_transition_adjusted": os.path.join(
            output_dir,
            "application_job_transition_adjusted.csv",
        ),
        "job_transition_adjusted_summary": os.path.join(
            output_dir,
            "application_job_transition_adjusted_summary.csv",
        ),
        "parallel_calls": os.path.join(output_dir, "application_parallel_calls.csv"),
        "parallel_rounds": os.path.join(output_dir, "application_parallel_rounds.csv"),
    }
    call_job, call_job_summary, job_call_summary = build_call_job_goodput_tables(calls, jobs)
    job_timing, job_timing_summary = build_transition_adjusted_job_tables(jobs)
    parallel_calls, parallel_rounds = build_parallel_call_tables(result_dir, calls)
    calls.to_csv(outputs["calls"], index=False)
    jobs.to_csv(outputs["jobs"], index=False)
    build_summary(calls, jobs).to_csv(outputs["summary"], index=False)
    build_timeseries(calls, jobs).to_csv(outputs["timeseries"], index=False)
    call_job.to_csv(outputs["call_job_goodput"], index=False)
    call_job_summary.to_csv(outputs["call_job_goodput_summary"], index=False)
    job_call_summary.to_csv(outputs["job_call_goodput_summary"], index=False)
    job_timing.to_csv(outputs["job_transition_adjusted"], index=False)
    job_timing_summary.to_csv(outputs["job_transition_adjusted_summary"], index=False)
    if not parallel_calls.empty:
        parallel_calls.to_csv(outputs["parallel_calls"], index=False)
        parallel_rounds.to_csv(outputs["parallel_rounds"], index=False)
    else:
        outputs.pop("parallel_calls")
        outputs.pop("parallel_rounds")
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", help="Experiment result directory containing metrics.csv")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for parsed CSVs (default: <result_dir>/analysis)",
    )
    parser.add_argument(
        "--baseline-dir",
        default=None,
        help="Baseline run directory. Defaults to baseline_dir in run_config.json.",
    )
    parser.add_argument(
        "--tau",
        type=float,
        default=None,
        help="Latency slowdown threshold. Defaults to tau in run_config.json.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or os.path.join(args.result_dir, "analysis")
    outputs = parse_application_metrics(
        args.result_dir,
        output_dir,
        baseline_dir=args.baseline_dir,
        tau=args.tau,
    )
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()

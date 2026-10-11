# Analysis Scripts

Post-processing for `results/<run>/` produced by `run_experiment.py`.
Scripts are split by the **shape of the workload's metrics**, not by
metric name:

```
analysis_scripts/
├── job_level/        # multi-call job workloads (chain_call_* rows + job_summary)
├── request_level/    # flat request-level workloads (agent=="request" rows)
├── parse_server_logs.py   # neutral — parses server.stderr* into server_metrics.csv
└── plot_server_metrics.py # neutral — server-side figures
```

The two neutral scripts work for any workload because they read the
fetched remote `server.stderr*` rather than the client-side `metrics.csv`.

## Which folder for which workload

| Workload | Folder | Per-run parser |
|---|---|---|
| `swe_bench_coding` (job/chain) | `job_level/` | `parse_application_metrics.py` |
| `swe_bench_coding_tool_delay` | `job_level/` | `parse_application_metrics.py` |
| `swe_bench_coding_parallel_tool_delay` | `job_level/` | `parse_application_metrics.py` |
| `codingagent_request_level_poisson` (transcript+tau) | `request_level/` | `parse_request_metrics.py` |
| `sharegpt_request_level_poisson` (direct, absolute SLO) | `request_level/` | `parse_request_summary.py` |

See `job_level/README.md` and `request_level/README.md` for the full list
of scripts inside each folder.

## Typical flow

Per-run:

```bash
# job workloads
python analysis_scripts/job_level/parse_application_metrics.py results/<run>
python analysis_scripts/job_level/plot_application_metrics.py results/<run>

# request-level: ShareGPT (direct, no baseline)
python analysis_scripts/request_level/parse_request_summary.py results/<run>

# request-level: codingagent (transcript + tau)
python analysis_scripts/request_level/parse_request_metrics.py results/<run>

# server-side (neutral, any workload)
python analysis_scripts/parse_server_logs.py results/<run>
python analysis_scripts/plot_server_metrics.py results/<run>
```

Cross-run (sweep summaries) live in the workload-type subfolder, e.g.:

```bash
python analysis_scripts/request_level/summarize_lambda_sweep.py \
  --run-dirs results/*sharegpt_sweep_lambda_* \
  --output-dir results/aggregate_analysis/sharegpt_lambda_summary \
  --print-markdown --plot-png
```

## Why the split

Job and request workloads have **different metrics.csv shapes** (job
workloads write `chain_call_*` rows + `job_summary` per call/job;
request-level workloads write `agent=="request"` + `job_summary` per
request). The analysis pipelines diverged enough that mixing them in one
folder made it unclear which script applies to which run. Keeping them
side-by-side under `analysis_scripts/<type>/` makes the matching obvious
and prevents accidentally running a job-level parser on a request-level
run (or vice versa).

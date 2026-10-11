# Legacy Scripts

These files were kept for reference because they are not used by the current
experiment execution path.

- `load_monitor.py`: older live `/metrics` polling helper.
- `vllm_logger.py`: older vLLM-style log tail/parser helper. The current flow
  expects server logs to be copied into a result directory and parsed with
  `parse_server_logs.py`.
- `plot_timeseries.py`: older mixed application/server plotting script.
- `plot_ppt_v3.py`: older PPT-specific plotting script that mixed client CSV and
  server stderr parsing.
- `nul`: stray legacy file.
- `run_logs/`: old top-level experiment log files.

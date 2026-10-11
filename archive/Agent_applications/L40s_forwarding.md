In L40S single GPU node - run "ssh -N -p 23570 root@61.206.39.5 -L 30000:localhost:30000" in another terminal  of 3990.

In L40S 2 GPU node - run "ssh -N -p 31983 root@118.163.199.123  -L 30000:localhost:30000" in another terminal  of 3990.

source .venv/bin/activate

python run_swebench.py \
  --server-base-url http://localhost:30000 \
  --request-rate-per-min 120 \
  --replay-count 1 \
  --max-workers 300 \
  --log-level quiet (info, debug)

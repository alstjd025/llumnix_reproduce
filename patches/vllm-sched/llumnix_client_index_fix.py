#!/usr/bin/env python3
"""EXP-114: give the llumlet the client index it actually occupies.

WHY
---
vLLM V1's engine core keeps one output socket per client and routes a reply by
the index the client stamped on its request:

    core_client.py:948   encode((self.client_index, call_id, method, args))
    core.py:1085,1097    output_queue.put((client_idx, EngineCoreOutputs(...)))
    core.py:1269         sockets[client_index].send_multipart(...)

`addresses.outputs` is built with exactly one entry per API server process
(v1/engine/utils.py, `for _ in range(num_api_servers)`), and Llumnix APPENDS the
llumlet's own address to that list (engine_client/utils.py `add_llumlet_addresses`).
So the llumlet's socket index is the NUMBER OF API SERVERS.

Llumnix hardcodes it instead: `VLLMEngineClient.__init__` defaults
`client_index=1` and `LlumletProc.__init__` never passes one.  With a single API
server that constant is accidentally correct (0 = the API server, 1 = the
llumlet).  With `--api-server-count N > 1` the llumlet still says 1, the engine
core sends its utility replies to ApiServer_1, and that process dies:

    core_client.py:630  future = utility_results.pop(output.call_id)
    KeyError: 5911085152105140721
    -> RuntimeError: Process ApiServer_1 died with exit code None
    -> vllm serve exits 0 -> kubelet restarts -> LWS recreates the group

which is why the eight-instance pod was rebuilt every 2m45s.

WHAT THIS CHANGES
-----------------
One assignment, inside Llumnix.  vLLM is not touched, and neither is the engine
core's output routing.  The number of API servers reaches the engine core (and
therefore the forked llumlet) as `vllm_config.parallel_config._api_process_count`,
set by `run_multi_api_server` (entrypoints/cli/serve.py:173).

At --api-server-count 1 the new expression evaluates to 1, i.e. exactly the
constant it replaces: this patch cannot change the behaviour of any condition we
have measured so far, all of which ran with one API server.

FAILURE MODE
------------
Loud.  If the anchor is not found exactly once -- which is what an image update
would look like -- this exits non-zero and the launch script aborts the
container rather than starting an engine whose llumlet indexes itself wrongly.
"""
import hashlib
import sys

TARGET = ("/usr/local/lib/python3.12/dist-packages/"
          "llumnix/engine_client/vllm_v1/engine_client.py")

ANCHOR = "        self.client_index = client_index\n"

REPLACEMENT = '''        # LLUMNIX-FIX(EXP-114): the llumlet's output address is APPENDED to
        # addresses.outputs, which already holds one entry per API server
        # process, so the llumlet's index in the engine core's socket list is
        # the number of API servers -- not the constant 1, which is correct only
        # when there is exactly one API server.  See llumnix_client_index_fix.py.
        _n_api = getattr(vllm_config.parallel_config, "_api_process_count", None)
        self.client_index = int(_n_api) if _n_api else client_index
        logger.info(
            "Llumnix engine client index: api_process_count=%s -> client_index=%s",
            _n_api, self.client_index)
'''

MARK = "LLUMNIX-FIX(EXP-114)"


def main() -> int:
    try:
        with open(TARGET, encoding="utf-8") as f:
            src = f.read()
    except OSError as exc:
        print(f"[client-index-fix] cannot read {TARGET}: {exc}", file=sys.stderr)
        return 1

    if MARK in src:
        print("[client-index-fix] already applied, nothing to do")
        return 0

    n = src.count(ANCHOR)
    if n != 1:
        print(f"[client-index-fix] ABORT: anchor found {n} times, expected 1. "
              f"The image's llumnix has changed; re-derive the patch.",
              file=sys.stderr)
        return 1

    out = src.replace(ANCHOR, REPLACEMENT)
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(out)

    # Read the file back rather than trusting the write: a success message means
    # a string in memory changed, not that the file did.
    with open(TARGET, encoding="utf-8") as f:
        back = f.read()
    if MARK not in back or ANCHOR in back:
        print("[client-index-fix] ABORT: re-read does not show the patch",
              file=sys.stderr)
        return 1

    import py_compile
    try:
        py_compile.compile(TARGET, doraise=True)
    except py_compile.PyCompileError as exc:
        print(f"[client-index-fix] ABORT: patched file does not compile: {exc}",
              file=sys.stderr)
        return 1

    print(f"[client-index-fix] applied, md5={hashlib.md5(back.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

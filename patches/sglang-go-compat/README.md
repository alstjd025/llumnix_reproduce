# sglang-go-compat — EXP-10 gateway rebuild shim

The public `sgl-project/sglang` submodule pinned by this repo does not export
the Go wrappers (`sglang.Tokenizer`, `sglang.ToolParser`, …) that
`pkg/gateway` imports — those live in the vendor's private SDK. The C symbols
they wrap ARE in the prebuilt FFI archive shipped inside the stock gateway
image, so the gateway can be rebuilt from this repo with two ingredients:

1. **FFI archive** — extract from the stock gateway image once:

   ```bash
   GP=$(kubectl -n llumnix get pod -l app=gateway -o name | head -1)
   mkdir -p lib/sglang/sgl-model-gateway/bindings/golang/lib
   kubectl -n llumnix cp ${GP#pod/}:/usr/lib/libsgl_model_gateway_go.a \
     lib/sglang/sgl-model-gateway/bindings/golang/lib/libsgl_model_gateway_go.a
   ```

2. **Wrapper shim** — copy [tokenizer_compat.go](tokenizer_compat.go) into the
   submodule package (untracked there; canonical copy is this directory):

   ```bash
   cp patches/sglang-go-compat/tokenizer_compat.go \
     lib/sglang/sgl-model-gateway/bindings/golang/tokenizer_compat.go
   ```

Then build **inside the vllm image** (glibc 2.35 must match the gateway
image; the host is newer):

```bash
kubectl apply -f Agent_applications/agent_motivation_experiment/k8s/exp07/builder-gateway.yaml
# -> bin/gateway-exp10
```

Deploy with
`Agent_applications/agent_motivation_experiment/k8s/exp07/patch-gateway-timeout.sh`
(hostPath-injects the binary and sets `GATEWAY_SSE_READ_TIMEOUT` /
`GATEWAY_RESPONSE_HEADER_TIMEOUT`).

Shim deviations from the vendor SDK (safe for this deployment — see file
header): chat-template argument ignored (completions-only gateway), NULL
tools JSON for empty strings.

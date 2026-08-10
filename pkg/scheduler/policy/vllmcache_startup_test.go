package policy

import (
	"bytes"
	"strings"
	"testing"

	"k8s.io/klog/v2"

	"llumnix/cmd/config"
	"llumnix/cmd/scheduler/app/options"
)

// The start-up line is the only authority on what the scheduler resolved, and
// ms_dev/scripts/set_scheduler_profiling.py parses THIS line to decide whether a
// condition may run. That makes the wording of the line an interface between a
// Go file and a Python one, with nothing but this test holding the two together:
// renaming a key here would make the checker report a configuration it could not
// find, or -- worse for `localaccount`, which is checked by absence of "true" --
// let a run proceed with the load signal collapsed back to the poll snapshot.
//
// Printing the captured line on failure is deliberate: when this breaks, the
// thing needed to fix the checker is the line itself.
func TestVllmCacheStartupLineIsWhatTheCheckerParses(t *testing.T) {
	var buf bytes.Buffer
	klog.LogToStderr(false)
	klog.SetOutput(&buf)
	defer func() {
		klog.SetOutput(nil)
		klog.LogToStderr(true)
	}()

	cfg := &options.SchedulerConfig{}
	cfg.VllmCacheThreshold = 0.3
	cfg.VllmCacheBalanceAbs = 64
	cfg.VllmCacheBalanceRel = 1.5
	cfg.VllmCacheEvictionSecs = 120
	cfg.VllmCacheMaxTreeSize = 1 << 26
	cfg.EnableInstanceStatusLocalAccount = true
	var _ config.FullModeSchedulingConfig = cfg.FullModeSchedulingConfig

	newVllmCacheDispatchFullMode(cfg)
	klog.Flush()

	var line string
	for _, l := range strings.Split(buf.String(), "\n") {
		if strings.Contains(l, "vLLM router cache_aware policy created") {
			line = l
		}
	}
	if line == "" {
		t.Fatalf("no start-up line was emitted; got:\n%s", buf.String())
	}
	for _, want := range []string{
		"cacheThreshold 0.30", "balanceAbs 64", "balanceRel 1.50",
		"evictionSecs 120", "maxTreeSize 67108864", "localaccount=true",
	} {
		if !strings.Contains(line, want) {
			t.Fatalf("start-up line is missing %q, so _verify_vllm_cache in "+
				"set_scheduler_profiling.py cannot read it.\nline: %s", want, line)
		}
	}
	t.Logf("start-up line: %s", line)
}

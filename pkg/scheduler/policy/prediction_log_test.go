package policy

import (
	"math"
	"regexp"
	"strconv"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"llumnix/pkg/consts"
)

// The pattern build_request_engine_map.py uses to attribute a request to the
// engine that served it. The prediction line MUST NOT match it: the attribution
// join has to keep reading the generic dispatch line, and a second line matching
// it would double every request in that map.
var dispatchRE = regexp.MustCompile(
	`\[Schedule\] dispatch request ([0-9a-fA-F-]{8,}) to \S+ instance (\d+)`)

// The pattern llumnix_metrics.py greps at the source. The prediction line MUST
// match it, or the line is emitted by the scheduler and never written to the run
// directory -- the failure that lost four conditions in EXP-96.
var collectorRE = regexp.MustCompile(`\[Schedule\] dispatch request`)

func predictionLine(vals map[string]float32, instanceId string) string {
	// Mirrors logPredictionForRequest's format so the two patterns above can be
	// tested without capturing klog's output.
	line := "[Schedule] dispatch request 2b6b9f91-5e07-4d9e-afec-8f835ba84e62 prediction"
	for _, name := range predictionMetrics {
		v, ok := vals[name]
		if !ok {
			continue
		}
		if math.IsInf(float64(v), 0) || math.IsNaN(float64(v)) {
			line += " " + name + "=inf"
			continue
		}
		line += " " + name + "=" + trimFloat(v)
	}
	return line + " inst=" + instanceId
}

func trimFloat(v float32) string {
	return strconv.FormatFloat(float64(v), 'f', 3, 64)
}

func TestPredictionLineIsCollectedButNotAttributed(t *testing.T) {
	line := predictionLine(map[string]float32{
		consts.SchedulingMetricPredictedTtft: 1234.5,
		consts.SchedulingMetricPredictedTpot: 41.25,
	}, "1788776211167090324")
	assert.True(t, collectorRE.MatchString(line),
		"the collector greps this prefix; without it the line never reaches the run directory")
	assert.False(t, dispatchRE.MatchString(line),
		"the engine-attribution join must not see this line as a placement")
}

func TestPredictionLineCarriesTheSentinelForAnInfiniteForecast(t *testing.T) {
	line := predictionLine(map[string]float32{
		consts.SchedulingMetricPredictedTtft: float32(math.Inf(1)),
	}, "1788776211167090324")
	assert.Contains(t, line, "predicted_ttft=inf",
		"an infinite forecast is the policy saying 'not feasible here' and has to "+
			"survive as a sentinel, not as a number an offline parser would read")
}

func TestPredictionMetricsCoverBothPolicies(t *testing.T) {
	require.Contains(t, predictionMetrics, consts.SchedulingMetricPredictedTtft)
	require.Contains(t, predictionMetrics, consts.SchedulingMetricPredictedTpot)
	require.Contains(t, predictionMetrics, consts.SchedulingMetricPolyserveIterNow)
	require.Contains(t, predictionMetrics, consts.SchedulingMetricPolyserveIterMax)
}

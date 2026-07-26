package policy

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"llumnix/pkg/cms"
	"llumnix/pkg/consts"
	"llumnix/pkg/scheduler/predictor"
	"llumnix/pkg/types"
)

// fixedPrefillPredictor stands in for the measured ttft.json table with a curve
// that is linear through the one point the tests reason about: a full 8192-token
// chunk costs 520 ms, which is what the idle-engine sweep measured.
func fixedPrefillPredictor() *LatencyPredictor {
	p := predictor.NewInterpolationPredictor()
	for _, c := range []int{1, 512, 1024, 2048, 4096, 8192, 16384, 65536} {
		p.AddSample(float64(c), 0, 520.0*float64(c)/8192.0)
	}
	return &LatencyPredictor{ttftPredictor: p, tpotPredictor: p}
}

// marshalAndRead round-trips a profile through the on-disk format so the
// validation that runs at startup is exercised on the same path the scheduler
// uses.
func marshalAndRead(t *testing.T, p *fluidserveProfile) (*fluidserveProfile, error) {
	t.Helper()
	raw, err := json.Marshal(p)
	require.NoError(t, err)
	path := filepath.Join(t.TempDir(), "fluidserve.json")
	require.NoError(t, os.WriteFile(path, raw, 0o600))
	got, _, err := readFluidserveProfile(path)
	return got, err
}

// ---------------------------------------------------------------------------
// test fixtures
// ---------------------------------------------------------------------------

// testProfile mirrors the shape of the generated profile with a small, exactly
// known survival function, so the expected values below can be derived by hand.
func testProfile() *fluidserveProfile {
	p := &fluidserveProfile{}
	p.DecodeStepLaw.C0Ms = 16.0
	p.DecodeStepLaw.CKvMsPerToken = 1e-5
	p.DecodeStepLaw.CNMsPerRequest = 0.08
	// Two tiers. Lengths are uniform on [0, 400] and [0, 800] respectively, so
	// S(j) = 1 - j/L and E[L-j | L>j] = (L-j)/2.
	p.Classes = []*classProfile{
		{Name: "short", TpotSloMs: 50, N: 1000, Mean: 200, P90: 360,
			Grid: linspace(0, 400, 41), Survival: uniformSurvival(0, 400, 41)},
		{Name: "long", TpotSloMs: 25, N: 1000, Mean: 400, P90: 720,
			Grid: linspace(0, 800, 41), Survival: uniformSurvival(0, 800, 41)},
	}
	return p
}

func linspace(lo, hi, n int) []int {
	out := make([]int, n)
	for i := range out {
		out[i] = lo + (hi-lo)*i/(n-1)
	}
	return out
}

func uniformSurvival(lo, hi, n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		j := float64(lo + (hi-lo)*i/(n-1))
		out[i] = 1 - j/float64(hi)
	}
	return out
}

func testLengths() *lengthModel { return newLengthModel(testProfile().Classes) }

// fsView builds an instance view with the engine-reported fields the policy
// reads. Everything the policy needs comes from InstanceStatus, so a view can
// be assembled without a live CMS.
type fsViewOpts struct {
	id            string
	decodeReqs    int32
	decodeTokens  int32
	pendingPre    int32
	usedGpu       int32
	totalGpu      int32
	stepID        int32
	stepDurationS float64
	prefillInStep int32
}

func fsView(o fsViewOpts) *instanceViewScheduling {
	if o.totalGpu == 0 {
		o.totalGpu = 600000
	}
	cmsView := &cms.InstanceView{
		Instance: &types.LLMInstance{InferType: consts.InferTypeNeutral},
		Status: &cms.InstanceStatus{
			InstanceId:                            o.id,
			SchedulerRunningToDecodeRequestsNum:   o.decodeReqs,
			SchedulerRunningToDecodeTokensNum:     o.decodeTokens,
			NumUncomputedTokensAllWaitingPrefills: o.pendingPre,
			NumUsedGpuTokens:                      o.usedGpu,
			NumTotalGpuTokens:                     o.totalGpu,
			StepId:                                o.stepID,
			StepDuration:                          o.stepDurationS,
			NumScheduledPrefillTokens:             o.prefillInStep,
		},
		Metadata: &cms.InstanceMetadata{InstanceId: o.id, MaxNumBatchedTokens: 8192},
	}
	return &instanceViewScheduling{
		cmsView:               cmsView,
		InstanceViewInterface: cmsView,
		schedulingCtx:         schedulingCtx{metrics: map[string]instanceSchedulingMetric{}},
	}
}

// ---------------------------------------------------------------------------
// C1: output-length distribution
// ---------------------------------------------------------------------------

func TestSurvivalAndCompletionProbability(t *testing.T) {
	m := testLengths()
	short := m.forTier(50)
	require.NotNil(t, short)

	assert.InDelta(t, 1.0, short.survivalAt(0), 1e-9)
	assert.InDelta(t, 0.5, short.survivalAt(200), 1e-6)
	assert.InDelta(t, 0.0, short.survivalAt(400), 1e-6)

	// For a uniform length on [0, 400], a request at j=200 has half the mass
	// left, and 100 more tokens covers half of that.
	assert.InDelta(t, 0.5, short.completionProb(200, 100), 1e-6)
	// The same 100 tokens matters much less to a request that has just started.
	assert.InDelta(t, 0.25, short.completionProb(0, 100), 1e-6)
}

func TestCompletionProbabilityIsConditional(t *testing.T) {
	// The reason for conditioning: charging a request its class mean would say
	// that anything past the mean is certain to finish, which overstates how
	// much KV is about to be released.
	m := testLengths()
	long := m.forTier(25)
	// A request 600 tokens in, on a distribution whose mean is 400, is NOT
	// certain to finish in the next 50 tokens.
	p := long.completionProb(600, 50)
	assert.Less(t, p, 0.3)
	assert.Greater(t, p, 0.0)
}

func TestExpectedRemainingDecreasesWithProgress(t *testing.T) {
	m := testLengths()
	short := m.forTier(50)
	prev := math.Inf(1)
	for j := 0; j <= 380; j += 20 {
		r := short.expectedRemaining(j)
		assert.LessOrEqual(t, r, prev, "expected remaining must not grow with progress")
		assert.GreaterOrEqual(t, r, 1.0)
		prev = r
	}
	// Uniform on [0,400]: E[L-j | L>j] = (400-j)/2.
	assert.InDelta(t, 200.0, short.expectedRemaining(0), 12.0)
	assert.InDelta(t, 100.0, short.expectedRemaining(200), 12.0)
}

func TestUnknownTierFallsBackToLongestTail(t *testing.T) {
	m := testLengths()
	// An unprofiled tier must not be assumed short: assuming a short output
	// overstates how soon its KV is released.
	assert.Equal(t, m.forTier(25), m.forTier(999))
}

func TestProfileValidationRejectsIncreasingSurvival(t *testing.T) {
	p := testProfile()
	p.Classes[0].Survival[5] = p.Classes[0].Survival[4] + 0.1
	_, err := marshalAndRead(t, p)
	assert.ErrorContains(t, err, "survival increases")
}

func TestProfileValidationRejectsFreeSteps(t *testing.T) {
	p := testProfile()
	p.DecodeStepLaw.C0Ms = 0
	_, err := marshalAndRead(t, p)
	assert.ErrorContains(t, err, "non-positive")
}

// ---------------------------------------------------------------------------
// class budgets
// ---------------------------------------------------------------------------

func TestParseClassBudgets(t *testing.T) {
	b, err := parseClassBudgets("25:e2e:30000,50:decode,100:decode")
	require.NoError(t, err)

	assert.Equal(t, budgetE2E, b.forTier(25).mode)
	assert.Equal(t, 30000.0, b.forTier(25).totalMs)
	assert.Equal(t, budgetDecode, b.forTier(50).mode)
	assert.Equal(t, 50.0, b.forTier(50).perTokMs)
	// An unlisted tier is read as a per-token budget equal to its key.
	assert.Equal(t, budgetDecode, b.forTier(70).mode)
	assert.Equal(t, 70.0, b.forTier(70).perTokMs)

	for _, bad := range []string{"", "25", "25:e2e", "25:bogus", "x:decode"} {
		_, err := parseClassBudgets(bad)
		assert.Error(t, err, "should reject %q", bad)
	}
}

// ---------------------------------------------------------------------------
// C3: capacity
// ---------------------------------------------------------------------------

func testCapacity(t *testing.T) *capacityModel {
	t.Helper()
	return &capacityModel{
		c0: 16.0, cKv: 1e-5, cN: 0.08, seedC: 16.0,
		correction: 1.0,
		predictor:  fixedPrefillPredictor(),
	}
}

func TestDecodeStepGrowsWithOccupancy(t *testing.T) {
	m := testCapacity(t)
	empty := m.decodeStepMs(0, 0)
	loaded := m.decodeStepMs(400000, 40)
	assert.InDelta(t, 16.0, empty, 1e-9)
	// 16 + 400000*1e-5 + 40*0.08 = 16 + 4 + 3.2
	assert.InDelta(t, 23.2, loaded, 1e-6)
	assert.Greater(t, loaded, empty)
}

func TestMeanStepChargesPrefillCarryingIterations(t *testing.T) {
	m := testCapacity(t)
	kv, n := 200000.0, 20.0
	decodeOnly := m.meanStepMs(kv, n, 0, 8192, 100)

	// One full chunk queued: one of the next 100 iterations carries it, and that
	// iteration costs the prefill pass plus the decode work of the same step.
	withChunk := m.meanStepMs(kv, n, 8192, 8192, 100)
	assert.Greater(t, withChunk, decodeOnly)

	dec := m.decodeStepMs(kv, n)
	expected := (99*dec + 1*(520.0+dec-16.0)) / 100
	assert.InDelta(t, expected, withChunk, 1e-6)

	// Enough queued prefill to fill the whole horizon drives the mean to the
	// cost of a prefill-carrying iteration.
	saturated := m.meanStepMs(kv, n, 8192*200, 8192, 100)
	assert.InDelta(t, 520.0+dec-16.0, saturated, 1e-6)
}

func TestMaxKvForAllowanceInvertsMeanStep(t *testing.T) {
	m := testCapacity(t)
	for _, tc := range []struct {
		allowance, n, pending float64
	}{
		{50, 20, 0},
		{100, 5, 8192},
		{40, 60, 4096},
	} {
		cap := m.maxKvForAllowance(tc.allowance, tc.n, tc.pending, 8192, 100)
		require.False(t, math.IsInf(cap, 0))
		got := m.meanStepMs(cap, tc.n, tc.pending, 8192, 100)
		assert.InDelta(t, tc.allowance, got, 1e-6,
			"mean step at the capacity bound must equal the allowance")
	}
}

func TestQueuedPrefillCollapsesCapacity(t *testing.T) {
	m := testCapacity(t)
	// This is the effect that makes routing, not engine scheduling, the lever:
	// a queued prompt raises the mean iteration time for every request already
	// decoding on that instance, so the instance can hold far less while still
	// meeting a 50 ms per-token budget.
	idle := m.maxKvForAllowance(50, 20, 0, 8192, 100)
	loaded := m.maxKvForAllowance(50, 20, 8192*4, 8192, 100)
	assert.Greater(t, idle, loaded)
	assert.Less(t, loaded, idle/2)
}

func TestAllowanceBelowFloorIsUnreachable(t *testing.T) {
	m := testCapacity(t)
	// 10 ms per token is below the fixed cost of an iteration, so no occupancy
	// satisfies it. The model reports a negative capacity rather than clamping,
	// because "cannot be met at all" and "can be met only when empty" call for
	// different decisions.
	assert.Less(t, m.maxKvForAllowance(10, 1, 0, 8192, 100), 0.0)
	assert.Equal(t, 16.0, m.floorStepMs())
}

func TestOnlineCalibrationTracksDriftAndIgnoresPrefillSteps(t *testing.T) {
	m := testCapacity(t)
	base := m.decodeStepMs(200000, 20)

	// A step that carried a chunk must not be attributed to the decode law.
	for i := 0; i < 500; i++ {
		m.observe(200000, 20, 8192, 500)
	}
	assert.InDelta(t, 1.0, m.correctionFactor(), 1e-9)

	// Sustained evidence that iterations cost 30% more than predicted moves the
	// correction towards it.
	for i := 0; i < 3000; i++ {
		m.observe(200000, 20, 0, base*1.3)
	}
	assert.Greater(t, m.correctionFactor(), 1.15)
	assert.Less(t, m.correctionFactor(), 1.35)

	// Implausible samples are rejected rather than absorbed.
	before := m.correctionFactor()
	for i := 0; i < 100; i++ {
		m.observe(200000, 20, 0, 60000)
	}
	assert.InDelta(t, before, m.correctionFactor(), 1e-9)
}

// ---------------------------------------------------------------------------
// C2: registry
// ---------------------------------------------------------------------------

func testRegistry(t *testing.T) *requestRegistry {
	t.Helper()
	b, err := parseClassBudgets("25:e2e:30000,50:decode")
	require.NoError(t, err)
	return newRequestRegistry(testLengths(), b)
}

func TestProgressComesFromTheStepCounter(t *testing.T) {
	r := testRegistry(t)
	now := int64(1_000_000)
	r.noteArrival("a", now)
	// A 16k prompt needs two iterations at an 8192-token budget before the
	// request produces anything.
	r.onDispatch("e0", "a", 50, 16384, 8192, 1000, now)

	live := r.reconcile("e0", 1, 1002, now+100)
	require.Len(t, live, 1)
	assert.Equal(t, 0, live[0].j, "still prefilling")

	live = r.reconcile("e0", 1, 1152, now+3000)
	require.Len(t, live, 1)
	assert.Equal(t, 150, live[0].j)
	assert.InDelta(t, float64(16384+150), live[0].kvTokens, 1e-9)
}

func TestReconcileTrimsToTheEngineCount(t *testing.T) {
	r := testRegistry(t)
	now := int64(1_000_000)
	for _, id := range []string{"a", "b", "c"} {
		r.noteArrival(id, now)
	}
	// Dispatched at different times, so they have made different progress.
	r.onDispatch("e0", "a", 50, 1000, 8192, 1000, now)
	r.onDispatch("e0", "b", 50, 1000, 8192, 1100, now)
	r.onDispatch("e0", "c", 50, 1000, 8192, 1200, now)

	// The engine says only two are decoding, so the one furthest along is the
	// one presumed finished.
	live := r.reconcile("e0", 2, 1300, now+5000)
	require.Len(t, live, 2)
	ids := map[string]bool{}
	for _, l := range live {
		ids[l.id] = true
	}
	assert.False(t, ids["a"], "the most advanced request should be retired first")
	assert.True(t, ids["b"])
	assert.True(t, ids["c"])
	byCount, _, _ := r.counters()
	assert.Equal(t, int64(1), byCount)
}

func TestRecordsDropWhenTheEngineRestarts(t *testing.T) {
	r := testRegistry(t)
	now := int64(1_000_000)
	r.noteArrival("a", now)
	r.onDispatch("e0", "a", 50, 1000, 8192, 5000, now)
	// A restarted engine reports a step counter below what we recorded.
	live := r.reconcile("e0", 1, 3, now+1000)
	assert.Empty(t, live)
	assert.Equal(t, 0, r.instanceCount("e0"))
}

func TestRequestsPastEveryObservedLengthAreRetired(t *testing.T) {
	r := testRegistry(t)
	now := int64(1_000_000)
	r.noteArrival("a", now)
	r.onDispatch("e0", "a", 50, 100, 8192, 1000, now)
	// The 50 ms tier tops out at 400 tokens in the test profile.
	live := r.reconcile("e0", 5, 1000+1+500, now+20000)
	assert.Empty(t, live)
	_, bySurvival, _ := r.counters()
	assert.Equal(t, int64(1), bySurvival)
}

func TestDecodeBudgetCarriesCreditForward(t *testing.T) {
	r := testRegistry(t)
	now := int64(1_000_000)
	r.noteArrival("a", now)
	r.onDispatch("e0", "a", 50, 100, 8192, 1000, now)

	// First observation with progress starts the decode clock.
	r.reconcile("e0", 1, 1001, now+1000)

	// 100 tokens produced in 2s is 20 ms each, well inside the 50 ms budget, so
	// the request has banked credit and its allowance for the remaining tokens
	// is above the nominal per-token figure. This is the property that stops a
	// tier key from pinning an instance's capacity at a value stricter than the
	// request is actually judged by.
	live := r.reconcile("e0", 1, 1101, now+3000)
	require.Len(t, live, 1)
	assert.Greater(t, live[0].allowanceMs, 50.0)

	// The same request having produced only 20 more tokens over the following
	// 12 seconds is averaging 600 ms per token, far past its budget, so almost
	// nothing is left for the tokens still to come.
	live = r.reconcile("e0", 1, 1121, now+15000)
	require.Len(t, live, 1)
	assert.Less(t, live[0].allowanceMs, 50.0)
}

func TestEndToEndBudgetCountsQueueingTime(t *testing.T) {
	r := testRegistry(t)
	now := int64(1_000_000)
	// Tier 25 is scored end to end, so time spent before dispatch is spent
	// budget, unlike the decode-mode tiers.
	r.noteArrival("s", now)
	r.onDispatch("e0", "s", 25, 20000, 8192, 1000, now+5000)

	early := r.reconcile("e0", 1, 1010, now+6000)
	require.Len(t, early, 1)

	r2 := testRegistry(t)
	r2.noteArrival("s", now)
	r2.onDispatch("e0", "s", 25, 20000, 8192, 1000, now+5000)
	late := r2.reconcile("e0", 1, 1010, now+20000)
	require.Len(t, late, 1)

	assert.Greater(t, early[0].allowanceMs, late[0].allowanceMs,
		"a request that has been waiting has less time left per remaining token")
}

func TestNewRequestAllowanceShrinksWithWaiting(t *testing.T) {
	r := testRegistry(t)
	now := int64(1_000_000)
	fresh, tokens := r.newRequestAllowance(25, 20000, now, now)
	waited, _ := r.newRequestAllowance(25, 20000, now, now+10000)
	assert.Greater(t, tokens, 1.0)
	assert.Greater(t, fresh, waited)

	// A decode-mode tier is judged on its per-token budget, which queueing does
	// not consume; that is what the separate time-to-first-token budget covers.
	a, _ := r.newRequestAllowance(50, 1000, now, now)
	b, _ := r.newRequestAllowance(50, 1000, now, now+10000)
	assert.Equal(t, a, b)
}

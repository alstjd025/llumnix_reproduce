package policy

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"sync"

	"k8s.io/klog/v2"
)

// FluidServe's offline seed: what the controller knows before it has observed
// anything on this run. Produced by ms_dev/scripts/gen_fluidserve_profile.py
// from the EXP-16 per-step scheduler dumps and the per-request metrics of
// completed runs.
//
// Seeding matters beyond saving warm-up time. The capacity estimate and the
// safety margin adapt to each other: a controller that starts conservative
// keeps occupancy low, never observes what a heavily loaded step costs, and so
// has no evidence on which to relax. Starting from measurements taken across
// the full load range removes that dependency.

type fluidserveProfile struct {
	DecodeStepLaw struct {
		C0Ms           float64 `json:"c0_ms"`
		CKvMsPerToken  float64 `json:"c_kv_ms_per_token"`
		CNMsPerRequest float64 `json:"c_n_ms_per_request"`
		R2             float64 `json:"r2"`
		Samples        int     `json:"samples"`
	} `json:"decode_step_law"`
	Classes []*classProfile `json:"classes"`
}

// classProfile is the empirical output-length distribution of one SLO tier,
// stored as a survival function on a discrete grid.
type classProfile struct {
	Name      string    `json:"name"`
	TpotSloMs int       `json:"tpot_slo_ms"`
	N         int       `json:"n"`
	Mean      float64   `json:"mean"`
	P50       float64   `json:"p50"`
	P90       float64   `json:"p90"`
	Grid      []int     `json:"grid"`
	Survival  []float64 `json:"survival"`

	// expectedRemainingCache[i] is E[L - j | L > j] evaluated at Grid[i]. It is
	// precomputed because the decision path evaluates it for every live request
	// on every instance on every scheduling call.
	expectedRemainingCache []float64
}

// survivalAt returns P(output length > j), linearly interpolated between grid
// points and clamped outside the grid. Interpolation rather than a step lookup
// matters because the conditional completion probability is a difference of two
// survival values, and a step function makes that difference zero for any pair
// falling inside the same bin.
func (c *classProfile) survivalAt(j int) float64 {
	if len(c.Grid) == 0 {
		return 0
	}
	if j <= c.Grid[0] {
		return c.Survival[0]
	}
	last := len(c.Grid) - 1
	if j >= c.Grid[last] {
		return c.Survival[last]
	}
	i := sort.SearchInts(c.Grid, j)
	if i == 0 {
		return c.Survival[0]
	}
	lo, hi := c.Grid[i-1], c.Grid[i]
	if hi == lo {
		return c.Survival[i]
	}
	w := float64(j-lo) / float64(hi-lo)
	return c.Survival[i-1]*(1-w) + c.Survival[i]*w
}

// completionProb is p_c(j, k): the chance a request that has already produced j
// tokens finishes within the next k iterations.
//
// The conditional form is what makes this usable. Charging every running
// request its class mean would say that any request past the mean is certain to
// finish immediately, which for a heavy-tailed length distribution overstates
// the tokens about to be freed and leads to admitting more work than the
// instance can hold.
func (c *classProfile) completionProb(j, k int) float64 {
	if k <= 0 {
		return 0
	}
	sj := c.survivalAt(j)
	if sj <= 1e-9 {
		return 1 // already past every observed length; treat as finishing
	}
	p := (sj - c.survivalAt(j+k)) / sj
	if p < 0 {
		return 0
	}
	if p > 1 {
		return 1
	}
	return p
}

// expectedRemaining is E[L - j | L > j], the mean number of tokens a request
// still has to produce. Computed as the sum of the conditional survival over
// the grid, which is the discrete form of the standard identity.
func (c *classProfile) expectedRemaining(j int) float64 {
	if len(c.Grid) == 0 {
		return 0
	}
	sj := c.survivalAt(j)
	if sj <= 1e-9 {
		return 1 // past every observed length: assume it ends imminently
	}
	// Interpolate the precomputed table on the same grid.
	last := len(c.Grid) - 1
	if j >= c.Grid[last] {
		return 1
	}
	i := sort.SearchInts(c.Grid, j)
	if i == 0 {
		return c.expectedRemainingCache[0]
	}
	lo, hi := c.Grid[i-1], c.Grid[i]
	if hi == lo {
		return c.expectedRemainingCache[i]
	}
	w := float64(j-lo) / float64(hi-lo)
	r := c.expectedRemainingCache[i-1]*(1-w) + c.expectedRemainingCache[i]*w
	if r < 1 {
		return 1
	}
	return r
}

func (c *classProfile) precompute() {
	c.expectedRemainingCache = make([]float64, len(c.Grid))
	for i := range c.Grid {
		sj := c.Survival[i]
		if sj <= 1e-9 {
			c.expectedRemainingCache[i] = 1
			continue
		}
		// E[L-j | L>j] = sum over the remaining grid of S(x)/S(j), weighted by
		// the spacing between grid points.
		acc := 0.0
		for x := i + 1; x < len(c.Grid); x++ {
			width := float64(c.Grid[x] - c.Grid[x-1])
			acc += c.Survival[x] / sj * width
		}
		if acc < 1 {
			acc = 1
		}
		c.expectedRemainingCache[i] = acc
	}
}

// lengthModel is C1: the per-tier length distributions plus the fallback used
// for a tier that was not profiled.
type lengthModel struct {
	byTier   map[int]*classProfile
	fallback *classProfile
}

func newLengthModel(classes []*classProfile) *lengthModel {
	m := &lengthModel{byTier: map[int]*classProfile{}}
	var widest *classProfile
	for _, c := range classes {
		c.precompute()
		m.byTier[c.TpotSloMs] = c
		// The fallback is the class with the longest tail, so an unknown tier is
		// assumed to hold its KV for as long as anything we have measured. That
		// keeps the outflow estimate on the low side, which is the direction
		// that under-admits rather than over-admits.
		if widest == nil || c.P90 > widest.P90 {
			widest = c
		}
	}
	m.fallback = widest
	return m
}

func (m *lengthModel) forTier(tpotSloMs int) *classProfile {
	if c, ok := m.byTier[tpotSloMs]; ok {
		return c
	}
	return m.fallback
}

var (
	fluidserveProfileOnce sync.Once
	fluidserveProfileData *fluidserveProfile
	fluidserveLengths     *lengthModel
)

// loadFluidserveProfile reads the profile once per process. A missing or
// malformed profile is fatal for the same reason the latency tables are: every
// routing decision depends on it, so continuing would mean routing on defaults
// that describe no real hardware.
func loadFluidserveProfile(path string) (*fluidserveProfile, *lengthModel) {
	fluidserveProfileOnce.Do(func() {
		p, l, err := readFluidserveProfile(path)
		if err != nil {
			klog.Fatalf("FluidServe profile %q unusable: %v", path, err)
		}
		fluidserveProfileData, fluidserveLengths = p, l
		klog.Infof("FluidServe profile loaded from %s: decode step law "+
			"%.3f + %.4e*kv + %.4e*n (R2=%.3f, %d samples), %d classes",
			path, p.DecodeStepLaw.C0Ms, p.DecodeStepLaw.CKvMsPerToken,
			p.DecodeStepLaw.CNMsPerRequest, p.DecodeStepLaw.R2,
			p.DecodeStepLaw.Samples, len(p.Classes))
		for _, c := range p.Classes {
			klog.Infof("  tier %3dms (%s): n=%d mean=%.0f p50=%.0f p90=%.0f",
				c.TpotSloMs, c.Name, c.N, c.Mean, c.P50, c.P90)
		}
	})
	return fluidserveProfileData, fluidserveLengths
}

func readFluidserveProfile(path string) (*fluidserveProfile, *lengthModel, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, err
	}
	var p fluidserveProfile
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, nil, fmt.Errorf("parse: %w", err)
	}
	if p.DecodeStepLaw.C0Ms <= 0 || p.DecodeStepLaw.CKvMsPerToken <= 0 {
		return nil, nil, fmt.Errorf(
			"decode step law has non-positive coefficients (%.4f, %.4e); a step "+
				"cannot be free and cannot get cheaper as the cache fills",
			p.DecodeStepLaw.C0Ms, p.DecodeStepLaw.CKvMsPerToken)
	}
	if len(p.Classes) == 0 {
		return nil, nil, fmt.Errorf("no class profiles")
	}
	for _, c := range p.Classes {
		if len(c.Grid) != len(c.Survival) || len(c.Grid) < 2 {
			return nil, nil, fmt.Errorf("class %q: grid/survival mismatch (%d vs %d)",
				c.Name, len(c.Grid), len(c.Survival))
		}
		for i := 1; i < len(c.Survival); i++ {
			if c.Survival[i] > c.Survival[i-1]+1e-9 {
				return nil, nil, fmt.Errorf(
					"class %q: survival increases at j=%d, which cannot happen",
					c.Name, c.Grid[i])
			}
		}
	}
	return &p, newLengthModel(p.Classes), nil
}

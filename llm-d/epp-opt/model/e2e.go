// Package model defines the prediction service contract and path ranking.
package model

import (
	"math"
)

const (
	AMD            = "amd"
	NVIDIA         = "nvidia"
	APND           = "ap-nd"
	NPAD           = "np-ad"
	TTFTPenaltyMS  = 5000
	DecodeHeader   = "x-epp-opt-decoder"
	ExecutedHeader = "x-epp-opt-executed-pd"
)

type Load struct {
	Cached  int `json:"cached_tokens"`
	Running int `json:"running"`
	Waiting int `json:"waiting"`
}

type Features struct {
	Model   string `json:"model"`
	Runtime string `json:"runtime"`
	Route   string `json:"route"`
	Input   int    `json:"input_tokens"`
	Budget  int64  `json:"output_budget"`
	Prefill Load   `json:"prefill"`
	Decode  Load   `json:"decode"`
}

func IsPD(route string) bool { return route == APND || route == NPAD }

func (f Features) Valid() bool {
	if f.Model == "" || f.Runtime == "" || f.Input <= 0 || f.Budget <= 0 {
		return false
	}
	if f.Route != AMD && f.Route != NVIDIA && !IsPD(f.Route) {
		return false
	}
	for _, load := range []Load{f.Prefill, f.Decode} {
		if load.Cached < 0 || load.Cached > f.Input || load.Running < 0 || load.Waiting < 0 {
			return false
		}
	}
	return IsPD(f.Route) || f.Prefill == f.Decode
}

type Candidate struct {
	ID       string   `json:"id"`
	Features Features `json:"features"`
}

type Prediction struct {
	TTFT float64 `json:"ttft_ms"`
	TPOT float64 `json:"tpot_ms"`
}

type Batch struct {
	Candidates []Candidate `json:"candidates"`
}

type Predictions struct {
	Version string                `json:"version"`
	ByID    map[string]Prediction `json:"predictions"`
}

// Observation carries the frozen request-time features of one completed execution.
type Observation struct {
	RequestID string   `json:"request_id"`
	Features  Features `json:"features"`
	Version   string   `json:"prediction_version,omitempty"`
	Output    int      `json:"output_tokens"`
	TTFT      float64  `json:"ttft_ms"`
	E2E       float64  `json:"e2e_ms"`
}

func (o Observation) Valid() bool {
	return o.RequestID != "" && o.Features.Valid() && o.Output > 0 &&
		Positive(o.TTFT) && Positive(o.E2E) && o.E2E >= o.TTFT &&
		(o.Output == 1 || o.E2E > o.TTFT)
}

func Positive(value float64) bool {
	return value > 0 && !math.IsInf(value, 0) && !math.IsNaN(value)
}

func (p Prediction) E2E(budget int64) float64 {
	if budget == 1 {
		return p.TTFT
	}
	return p.TTFT + float64(budget-1)*p.TPOT
}

// Choose returns the stable best alternative, or the baseline if its estimate is
// missing or no alternative can improve E2E within the relative TTFT budget.
func Choose(baseline string, estimates map[string]Prediction, budget int64) string {
	valid := func(p Prediction) bool {
		return budget > 0 && Positive(p.TTFT) && (budget == 1 || Positive(p.TPOT)) && Positive(p.E2E(budget))
	}
	base := estimates[baseline]
	if !valid(base) {
		return baseline
	}
	bestID, best := baseline, base
	for id, p := range estimates {
		if id == baseline || !valid(p) || p.TTFT > base.TTFT+TTFTPenaltyMS || p.E2E(budget) >= base.E2E(budget) {
			continue
		}
		if p.E2E(budget) < best.E2E(budget) ||
			(p.E2E(budget) == best.E2E(budget) && (p.TTFT < best.TTFT || (p.TTFT == best.TTFT && id < bestID))) {
			bestID, best = id, p
		}
	}
	return bestID
}

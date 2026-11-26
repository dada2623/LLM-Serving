package predictor

import (
	"crypto/sha256"
	"math"
	"sort"

	"github.com/llm-d/llm-d-router/eppopt/model"
)

const (
	neighbors     = 12
	minTraining   = 3
	minValidation = 2
)

type groupKey struct {
	model, runtime, route string
}

func key(f model.Features) groupKey { return groupKey{f.Model, f.Runtime, f.Route} }

type vector [8]float64

func features(f model.Features) vector {
	return vector{
		float64(f.Input) / 1024, float64(f.Budget) / 1024,
		float64(f.Input-f.Prefill.Cached) / 576, float64(f.Prefill.Running) / 4, float64(f.Prefill.Waiting) / 2,
		float64(f.Input-f.Decode.Cached) / 576, float64(f.Decode.Running) / 4, float64(f.Decode.Waiting) / 2,
	}
}

type point struct {
	x          vector
	ttft, tpot float64
}

type regression struct {
	points   []point
	min, max vector
}

func fit(samples []model.Observation) regression {
	var result regression
	for i, s := range samples {
		p := point{x: features(s.Features), ttft: s.TTFT}
		if s.Output > 1 {
			p.tpot = (s.E2E - s.TTFT) / float64(s.Output-1)
		}
		for j, value := range p.x {
			if i == 0 {
				result.min[j], result.max[j] = value, value
			} else {
				result.min[j] = min(result.min[j], value)
				result.max[j] = max(result.max[j], value)
			}
		}
		result.points = append(result.points, p)
	}
	return result
}

func (r regression) predict(f model.Features) (model.Prediction, bool) {
	if len(r.points) < minTraining {
		return model.Prediction{}, false
	}
	query := features(f)
	for i, value := range query {
		if value < r.min[i] || value > r.max[i] {
			return model.Prediction{}, false
		}
	}
	return r.interpolate(query, f.Budget)
}

func (r regression) interpolate(query vector, budget int64) (model.Prediction, bool) {
	type neighbor struct {
		distance float64
		point
	}
	var ttft, tpot []neighbor
	// Keep at most k neighbors for each label. The stable ordering resolves ties
	// in favor of newer observations, including repeated exact locations.
	insert := func(items []neighbor, item neighbor) []neighbor {
		index := sort.Search(len(items), func(i int) bool { return items[i].distance >= item.distance })
		if index == neighbors {
			return items
		}
		if len(items) < neighbors {
			items = append(items, neighbor{})
		}
		copy(items[index+1:], items[index:])
		items[index] = item
		return items
	}
	for _, p := range r.points {
		var distance float64
		for i, value := range query {
			gap := value - p.x[i]
			distance += gap * gap
		}
		n := neighbor{distance, p}
		ttft = insert(ttft, n)
		if p.tpot > 0 {
			tpot = insert(tpot, n)
		}
	}
	average := func(items []neighbor, token bool) float64 {
		var total, weights float64
		for _, item := range items {
			weight := 1 / (item.distance + 0.01)
			value := item.ttft
			if token {
				value = item.tpot
			}
			total += weight * value
			weights += weight
		}
		return total / weights
	}
	p := model.Prediction{TTFT: average(ttft, false)}
	if budget > 1 {
		p.TPOT = average(tpot, true)
	}
	return p, model.Positive(p.TTFT) && (budget == 1 || model.Positive(p.TPOT))
}

func holdout(id string) bool { return sha256.Sum256([]byte(id))[0] < 64 }

// errorOn checks TTFT and TPOT separately so a regression in one target cannot
// be hidden by an improvement in the other. Labels with one token only test TTFT.
func errorOn(r regression, validation []model.Observation) (first, token float64, ok bool) {
	if len(validation) < minValidation {
		return 0, 0, false
	}
	for _, sample := range validation {
		p, ok := r.interpolate(features(sample.Features), int64(sample.Output))
		if !ok {
			return 0, 0, false
		}
		first += math.Abs(p.TTFT-sample.TTFT) / sample.TTFT
		if sample.Output > 1 {
			actual := (sample.E2E - sample.TTFT) / float64(sample.Output-1)
			token += math.Abs(p.TPOT-actual) / actual
		}
	}
	return first, token, true
}

func validate(candidate, previous regression, validation []model.Observation) bool {
	ttft, tpot, ok := errorOn(candidate, validation)
	if !ok {
		return false
	}
	baseTTFT, baseTPOT, hasPrevious := errorOn(previous, validation)
	if !hasPrevious {
		// Cold-start models must stay within 50% mean absolute percentage error.
		tpotCount := 0
		for _, sample := range validation {
			if sample.Output > 1 {
				tpotCount++
			}
		}
		return ttft <= 0.5*float64(len(validation)) && tpot <= 0.5*float64(tpotCount)
	}
	return ttft <= baseTTFT*1.05+0.01 && tpot <= baseTPOT*1.05+0.01
}

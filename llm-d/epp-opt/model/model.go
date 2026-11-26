// Package model implements the calibrated TTFT policy used by EPP-Opt.
package model

import (
	"errors"
	"fmt"
	"math"
	"sort"
)

const (
	Local  = "nvidia"
	Remote = "ap-nd"
)

type Workload struct {
	InputTokens   int `json:"input_tokens"`
	NvidiaRunning int `json:"nvidia_running"`
	NvidiaWaiting int `json:"nvidia_waiting"`
	AMDRunning    int `json:"amd_running"`
	AMDWaiting    int `json:"amd_waiting"`
	NvidiaCached  int `json:"nvidia_cached_tokens"`
	AMDCached     int `json:"amd_cached_tokens"`
	BurstSize     int `json:"burst_size"`
}

func (w Workload) dimensions() [8]int {
	return [8]int{w.InputTokens, w.NvidiaRunning, w.NvidiaWaiting, w.AMDRunning, w.AMDWaiting, w.NvidiaCached, w.AMDCached, w.BurstSize}
}

func (w Workload) valid() bool {
	return w.InputTokens > 0 && w.BurstSize > 0 && w.NvidiaRunning >= 0 && w.NvidiaWaiting >= 0 && w.AMDRunning >= 0 && w.AMDWaiting >= 0 && w.NvidiaCached >= 0 && w.NvidiaCached <= w.InputTokens && w.AMDCached >= 0 && w.AMDCached <= w.InputTokens
}

type Calibration struct {
	Workload          Workload `json:"workload"`
	LocalSamples      int      `json:"local_samples"`
	RemoteSamples     int      `json:"remote_samples"`
	LocalTTFT         float64  `json:"local_ttft_ms"`
	RemoteTTFT        float64  `json:"remote_observed_ttft_ms"`
	RemoteNonTransfer float64  `json:"remote_nontransfer_ms"`
	KVBytes           int64    `json:"kv_bytes"`
	KVGbps            float64  `json:"kv_gbps"`
	KVMS              float64  `json:"kv_ms"`
}

type Options struct {
	KVGbps        float64 `json:"kvGbps"`
	NvidiaExtraMS float64 `json:"nvidiaLocalExtraMs"`
	AMDExtraMS    float64 `json:"amdExtraMs"`
}

type Decision struct {
	Route             string  `json:"route"`
	Reason            string  `json:"reason"`
	Estimation        string  `json:"estimation"`
	Samples           int     `json:"measured_samples_per_arm"`
	LocalTTFT         float64 `json:"local_ttft_ms"`
	RemoteTTFT        float64 `json:"remote_ttft_ms"`
	RemoteNonTransfer float64 `json:"remote_nontransfer_ms"`
	KVMS              float64 `json:"kv_transfer_ms"`
	KVBytes           int64   `json:"kv_bytes"`
}

// Model is immutable once fitted. EPP builds it outside the request path.
type Model struct {
	profiles          map[Workload]Calibration
	min, max          [8]int
	local, remote, kv []float64
	bytesPerToken     float64
}

func finite(x float64) bool { return !math.IsNaN(x) && !math.IsInf(x, 0) }

func Sort(rows []Calibration) {
	sort.Slice(rows, func(i, j int) bool {
		a, b := rows[i].Workload.dimensions(), rows[j].Workload.dimensions()
		for k := range a {
			if a[k] != b[k] {
				return a[k] < b[k]
			}
		}
		return false
	})
}

func Fit(rows []Calibration) (*Model, error) {
	if len(rows) == 0 {
		return nil, errors.New("no usable TTFT calibration data")
	}
	rows = append([]Calibration(nil), rows...)
	Sort(rows)
	m := &Model{profiles: make(map[Workload]Calibration, len(rows))}
	m.min, m.max = rows[0].Workload.dimensions(), rows[0].Workload.dimensions()
	var localX, remoteX, kvX [][]float64
	var localY, remoteY, kvY, rates []float64
	for _, p := range rows {
		w := p.Workload
		if !w.valid() || !finite(p.LocalTTFT) || p.LocalTTFT <= 0 || !finite(p.RemoteTTFT) || p.RemoteTTFT <= 0 || !finite(p.RemoteNonTransfer) || p.RemoteNonTransfer < 0 || !finite(p.KVMS) || p.KVMS < 0 || p.KVMS > p.RemoteTTFT || p.KVBytes < 0 || p.LocalSamples <= 0 || p.RemoteSamples <= 0 {
			return nil, fmt.Errorf("invalid calibration profile %+v", w)
		}
		if _, exists := m.profiles[w]; exists {
			return nil, fmt.Errorf("duplicate calibration profile %+v", w)
		}
		m.profiles[w] = p
		for i, d := range w.dimensions() {
			m.min[i], m.max[i] = min(m.min[i], d), max(m.max[i], d)
		}
		localX, localY = append(localX, localFeatures(w)), append(localY, p.LocalTTFT)
		remoteX, remoteY = append(remoteX, remoteFeatures(w)), append(remoteY, p.RemoteNonTransfer)
		if p.KVBytes > 0 && w.InputTokens > w.NvidiaCached {
			kvX, kvY = append(kvX, transferFeatures(w)), append(kvY, p.KVMS)
			rates = append(rates, float64(p.KVBytes)/float64(w.InputTokens-w.NvidiaCached))
		}
	}
	m.local, m.remote = linearFit(localX, localY), linearFit(remoteX, remoteY)
	for i := 0; i < 2; i++ {
		if m.max[i+3] == 0 {
			m.remote[i+5] = m.local[i+2]
		}
	}
	if len(rates) != 0 {
		m.kv, m.bytesPerToken = linearFit(kvX, kvY), median(rates)
	}
	return m, nil
}

func (o Options) Validate() error {
	for _, x := range []float64{o.KVGbps, o.NvidiaExtraMS, o.AMDExtraMS} {
		if !finite(x) || x < 0 {
			return errors.New("bandwidth and additional delays must be finite and nonnegative")
		}
	}
	return nil
}

func (m *Model) Decide(w Workload, o Options) (Decision, error) {
	if !w.valid() {
		return Decision{}, fmt.Errorf("invalid workload %+v", w)
	}
	if err := o.Validate(); err != nil {
		return Decision{}, err
	}
	d := Decision{Route: Local, Reason: "insufficient-remote-gain", Estimation: "linear-interpolation"}
	if p, ok := m.profiles[w]; ok {
		d.Estimation, d.Samples = "measured", p.LocalSamples
		d.LocalTTFT, d.RemoteNonTransfer = p.LocalTTFT, p.RemoteTTFT-p.KVMS
		d.KVBytes, d.KVMS = p.KVBytes, p.KVMS
	} else {
		for i, n := range w.dimensions() {
			if n < m.min[i] || n > m.max[i] {
				d.Estimation = "linear-extrapolation"
				break
			}
		}
		d.LocalTTFT = max(.001, dot(localFeatures(w), m.local))
		d.RemoteNonTransfer = max(.001, dot(remoteFeatures(w), m.remote))
		if missing := w.InputTokens - w.NvidiaCached; missing > 0 {
			if m.kv == nil {
				return Decision{}, errors.New("no positive KV transfer measured for cache miss")
			}
			d.KVBytes = int64(math.RoundToEven(float64(missing) * m.bytesPerToken))
			d.KVMS = max(.001, dot(transferFeatures(w), m.kv))
		}
	}
	if o.KVGbps > 0 {
		d.KVMS = float64(d.KVBytes) * 8e-6 / o.KVGbps
	}
	d.LocalTTFT += o.NvidiaExtraMS
	d.RemoteNonTransfer += o.AMDExtraMS
	d.RemoteTTFT = d.RemoteNonTransfer + d.KVMS
	if d.RemoteTTFT < d.LocalTTFT {
		d.Route, d.Reason = Remote, "remote-gain"
	}
	return d, nil
}

func localFeatures(w Workload) []float64 {
	return []float64{1, float64(w.InputTokens-w.NvidiaCached) / 1024, float64(w.NvidiaRunning) / 8, float64(w.NvidiaWaiting) / 4, float64(w.BurstSize-1) / 3}
}
func remoteFeatures(w Workload) []float64 {
	return []float64{1, float64(w.InputTokens-w.AMDCached) / 1024, float64(w.NvidiaRunning) / 8, float64(w.NvidiaWaiting) / 4, float64(w.BurstSize-1) / 3, float64(w.AMDRunning) / 8, float64(w.AMDWaiting) / 4}
}
func transferFeatures(w Workload) []float64 {
	missing, burst := float64(w.InputTokens-w.NvidiaCached)/1024, float64(w.BurstSize-1)/3
	return []float64{1, missing, burst, missing * burst}
}
func dot(x, y []float64) float64 {
	var sum float64
	for i, v := range x {
		sum += v * y[i]
	}
	return sum
}
func median(x []float64) float64 {
	x = append([]float64(nil), x...)
	sort.Float64s(x)
	n := len(x) / 2
	if len(x)%2 == 0 {
		return (x[n-1] + x[n]) / 2
	}
	return x[n]
}

func linearFit(rows [][]float64, target []float64) []float64 {
	weights, norms := make([]float64, len(rows[0])), make([]float64, len(rows[0]))
	residual := append([]float64(nil), target...)
	for _, row := range rows {
		for i, x := range row {
			norms[i] += x * x
		}
	}
	for pass := 0; pass < 400; pass++ {
		var maxChange float64
		for i := range weights {
			var penalty, gradient float64
			if i > 0 {
				penalty = .02
			}
			for j, row := range rows {
				gradient += row[i] * residual[j]
			}
			gradient -= penalty * weights[i]
			updated := max(0, weights[i]+gradient/(norms[i]+penalty))
			change := updated - weights[i]
			if change != 0 {
				weights[i] = updated
				for j, row := range rows {
					residual[j] -= change * row[i]
				}
				maxChange = max(maxChange, math.Abs(change))
			}
		}
		if maxChange < 1e-6 {
			break
		}
	}
	return weights
}

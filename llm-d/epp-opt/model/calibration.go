package model

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type requests struct {
	Running float64 `json:"num_requests_running"`
	Waiting float64 `json:"num_requests_waiting"`
}

type snapshot struct {
	Nvidia requests `json:"nvidia"`
	AMD    requests `json:"amd"`
}

func (s snapshot) workload(tokens int) Workload {
	return Workload{InputTokens: tokens, NvidiaRunning: int(s.Nvidia.Running), NvidiaWaiting: int(s.Nvidia.Waiting), AMDRunning: int(s.AMD.Running), AMDWaiting: int(s.AMD.Waiting), BurstSize: 1}
}

type sample struct {
	Tokens  int     `json:"input_tokens"`
	Success bool    `json:"success"`
	TTFT    float64 `json:"ttft_s"`
	SHA     string  `json:"token_sha256"`
}

type nixl struct {
	Time     float64 `json:"nixl_xfer_time_seconds_sum"`
	Count    float64 `json:"nixl_xfer_time_seconds_count"`
	KVTokens float64 `json:"external_prefix_cache_hits_total"`
	Bytes    float64 `json:"nixl_bytes_transferred_sum"`
}

type measurement struct {
	sample
	Route   string   `json:"route"`
	Arrival snapshot `json:"arrival"`
	NIXL    nixl     `json:"nixl"`
	Target  *sample  `json:"target"`
}

type metricsDelta struct {
	Hits float64 `json:"prefix_cache_hits_total"`
}

type arm struct {
	Invalid json.RawMessage `json:"invalid"`
	Issues  json.RawMessage `json:"issues"`
	Burst   []sample        `json:"burst"`
	Arrival struct {
		State snapshot `json:"state"`
	} `json:"arrival"`
	Delta map[string]metricsDelta `json:"metrics_delta"`
	NIXL  nixl                    `json:"nixl"`
}

type round struct {
	Arms map[string]arm `json:"arms"`
}

type remoteSample struct{ ttft, kvms, size float64 }
type samples struct {
	local  []float64
	remote []remoteSample
}

func truthy(value json.RawMessage) bool {
	var decoded any
	if json.Unmarshal(value, &decoded) != nil {
		return false
	}
	switch v := decoded.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case float64:
		return v != 0
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	default:
		return false
	}
}

func paired(r round) (Workload, float64, remoteSample, bool) {
	local, remote := r.Arms[Local], r.Arms[Remote]
	size := len(local.Burst)
	if size == 0 || len(remote.Burst) != size || truthy(local.Invalid) || truthy(remote.Invalid) || truthy(local.Issues) || truthy(remote.Issues) {
		return Workload{}, 0, remoteSample{}, false
	}
	tokens := local.Burst[0].Tokens
	w := local.Arrival.State.workload(tokens)
	if w != remote.Arrival.State.workload(tokens) {
		return Workload{}, 0, remoteSample{}, false
	}
	localTTFT, remoteTTFT := make([]float64, size), make([]float64, size)
	for i, l := range local.Burst {
		d := remote.Burst[i]
		if !l.Success || !d.Success || l.Tokens != tokens || l.Tokens != d.Tokens || l.SHA == "" || l.SHA != d.SHA {
			return Workload{}, 0, remoteSample{}, false
		}
		localTTFT[i], remoteTTFT[i] = l.TTFT, d.TTFT
	}
	localHits, okLocal := local.Delta[Local]
	decodeHits, okDecode := remote.Delta[Local]
	amdHits, okAMD := remote.Delta["amd"]
	if !okLocal || !okDecode || !okAMD || localHits.Hits != decodeHits.Hits {
		return Workload{}, 0, remoteSample{}, false
	}
	for _, raw := range []float64{localHits.Hits, amdHits.Hits} {
		hits := int(raw)
		if float64(hits) != raw || hits < 0 || hits > tokens*size || hits%size != 0 {
			return Workload{}, 0, remoteSample{}, false
		}
	}
	w.NvidiaCached, w.AMDCached, w.BurstSize = int(localHits.Hits)/size, int(amdHits.Hits)/size, size
	l := 1000 * median(localTTFT)
	n := remote.NIXL
	d := remoteSample{1000 * median(remoteTTFT), 1000 * n.Time / float64(size), n.Bytes / float64(size)}
	missing := tokens*size - int(decodeHits.Hits)
	if !finite(l) || !finite(d.ttft) || !finite(d.size) || !finite(d.kvms) || l <= 0 || d.ttft <= 0 || d.kvms < 0 || d.kvms > d.ttft || n.KVTokens != float64(missing) {
		return Workload{}, 0, remoteSample{}, false
	}
	if missing > 0 && (n.Count != float64(size) || d.size <= 0 || d.kvms <= 0) || missing == 0 && (n.Count != 0 || d.size != 0 || d.kvms != 0) {
		return Workload{}, 0, remoteSample{}, false
	}
	return w, l, d, true
}

func readJSON(path string, out any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, out); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	return nil
}

// Calibrate combines the original single-request measurements and audited rounds.
// Pair paths accept either a trials.json file or its containing run directory.
func Calibrate(dir string, pairPaths []string) ([]Calibration, error) {
	grouped := make(map[Workload]*samples)
	get := func(w Workload) *samples {
		if grouped[w] == nil {
			grouped[w] = &samples{}
		}
		return grouped[w]
	}
	for _, name := range []string{"idle-calibration.json", "queued-calibration.json"} {
		var measurements []measurement
		if err := readJSON(filepath.Join(dir, name), &measurements); err != nil {
			return nil, err
		}
		for _, m := range measurements {
			target := m.sample
			if m.Target != nil {
				target = *m.Target
			}
			if (m.Route != Local && m.Route != Remote) || !target.Success {
				continue
			}
			ttft := 1000 * target.TTFT
			if !finite(ttft) || ttft <= 0 {
				return nil, fmt.Errorf("invalid measured TTFT in %s", name)
			}
			w := m.Arrival.workload(target.Tokens)
			bucket := get(w)
			if m.Route == Local {
				bucket.local = append(bucket.local, ttft)
				continue
			}
			n := m.NIXL
			transfer := 1000 * n.Time
			if n.Count != 1 || n.KVTokens != float64(target.Tokens) || !finite(transfer) || transfer <= 0 || transfer > ttft || !finite(n.Bytes) || n.Bytes <= 0 {
				return nil, fmt.Errorf("invalid single-request NIXL evidence for %+v", w)
			}
			bucket.remote = append(bucket.remote, remoteSample{ttft, transfer, n.Bytes})
		}
	}
	for w, b := range grouped {
		if len(b.local) < 3 || len(b.remote) < 3 {
			delete(grouped, w)
		}
	}
	for _, path := range pairPaths {
		if info, err := os.Stat(path); err == nil && info.IsDir() {
			path = filepath.Join(path, "trials.json")
		}
		var rounds []round
		if err := readJSON(path, &rounds); err != nil {
			return nil, err
		}
		for _, r := range rounds {
			w, l, d, ok := paired(r)
			if !ok {
				continue
			}
			bucket := get(w)
			bucket.local, bucket.remote = append(bucket.local, l), append(bucket.remote, d)
		}
	}
	rows := make([]Calibration, 0, len(grouped))
	for w, b := range grouped {
		var ttft, transfer, compute, sizes, gbps []float64
		for _, d := range b.remote {
			ttft, transfer = append(ttft, d.ttft), append(transfer, d.kvms)
			compute, sizes = append(compute, d.ttft-d.kvms), append(sizes, d.size)
			if d.kvms > 0 {
				gbps = append(gbps, 8e-6*d.size/d.kvms)
			}
		}
		var rate float64
		if len(gbps) > 0 {
			rate = median(gbps)
		}
		rows = append(rows, Calibration{w, len(b.local), len(b.remote), median(b.local), median(ttft), median(compute), int64(median(sizes)), rate, median(transfer)})
	}
	Sort(rows)
	if _, err := Fit(rows); err != nil {
		return nil, err
	}
	return rows, nil
}

func Load(path string) (*Model, error) {
	var profiles []Calibration
	if err := readJSON(path, &profiles); err != nil {
		return nil, err
	}
	return Fit(profiles)
}

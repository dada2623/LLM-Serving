package model

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"os"
)

type E2ESample struct {
	Route         string  `json:"route"`
	PromptTokens  int     `json:"prompt_tokens"`
	OutputTokens  int     `json:"output_tokens"`
	PrefixTokens  int     `json:"nominal_prefix_tokens"`
	CachedTokens  *int    `json:"cached_tokens"`
	NvidiaRunning int     `json:"nvidia_running"`
	NvidiaWaiting int     `json:"nvidia_waiting"`
	TTFT          float64 `json:"ttft_s"`
	TPOT          float64 `json:"tpot_s"`
}

type Latency struct {
	TTFT float64
	TPOT float64
	E2E  float64
}

type E2EModel struct {
	samples []E2ESample
}

func LoadE2E(path string) (*E2EModel, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	fitted := &E2EModel{}
	counts := map[string]int{}
	scanner := bufio.NewScanner(file)
	for line := 1; scanner.Scan(); line++ {
		var sample E2ESample
		if err := json.Unmarshal(scanner.Bytes(), &sample); err != nil {
			return nil, fmt.Errorf("line %d: %w", line, err)
		}
		if sample.Route != "amd" && sample.Route != "pd" {
			continue
		}
		if sample.PromptTokens <= 0 || sample.OutputTokens <= 1 ||
			!finite(sample.TTFT) || sample.TTFT <= 0 ||
			!finite(sample.TPOT) || sample.TPOT <= 0 {
			return nil, fmt.Errorf("invalid latency calibration on line %d", line)
		}
		fitted.samples = append(fitted.samples, sample)
		counts[sample.Route]++
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if counts["amd"] == 0 || counts["pd"] == 0 {
		return nil, errors.New("E2E calibration needs both AMD and P/D samples")
	}
	return fitted, nil
}

func (m *E2EModel) Estimate(w Workload, output int64) (Latency, Latency, error) {
	if !w.valid() || output <= 0 {
		return Latency{}, Latency{}, errors.New("invalid E2E workload")
	}
	return m.estimate("amd", w, output), m.estimate("pd", w, output), nil
}

func (m *E2EModel) estimate(route string, w Workload, output int64) Latency {
	best := -1.0
	var ttft, tpot []float64
	for _, sample := range m.samples {
		if sample.Route != route {
			continue
		}
		cached := sample.PrefixTokens / 576 * 576
		if route == "amd" && sample.CachedTokens != nil {
			cached = *sample.CachedTokens
		}
		distance := squared(float64(w.InputTokens-sample.PromptTokens)/1024) +
			squared(float64(output-int64(sample.OutputTokens))/1024) +
			squared(float64(w.AMDCached-cached)/576)
		if route == "pd" {
			distance += squared(float64(w.NvidiaCached-cached)/576) +
				squared(float64(w.NvidiaRunning-sample.NvidiaRunning)/4) +
				squared(float64(w.NvidiaWaiting-sample.NvidiaWaiting)/2)
		}
		if best < 0 || distance < best {
			best, ttft, tpot = distance, ttft[:0], tpot[:0]
		}
		if distance == best {
			ttft, tpot = append(ttft, sample.TTFT), append(tpot, sample.TPOT)
		}
	}
	first, token := median(ttft), median(tpot)
	return Latency{TTFT: first, TPOT: token, E2E: first + float64(output-1)*token}
}

func squared(x float64) float64 { return x * x }

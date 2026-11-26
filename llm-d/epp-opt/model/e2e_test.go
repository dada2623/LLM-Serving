package model

import "testing"

func TestChoose(t *testing.T) {
	baseline := Prediction{TTFT: 400, TPOT: 30}
	tests := []struct {
		name         string
		base         Prediction
		alternatives map[string]Prediction
		want         string
	}{
		{"inclusive 5000 ms", baseline, map[string]Prediction{"ap-nd": {5400, 20}}, "ap-nd"},
		{"over TTFT budget", baseline, map[string]Prediction{"ap-nd": {5400.1, 20}}, "nvidia"},
		{"no E2E improvement", baseline, map[string]Prediction{"amd": {900, 30}}, "nvidia"},
		{"E2E tie retains fallback", baseline, map[string]Prediction{"amd": baseline}, "nvidia"},
		{"whole path wins", baseline, map[string]Prediction{"ap-nd": {1300, 24}, "np-ad": {6500, 1}, "amd": {250, 18}}, "amd"},
		{"reverse path eligible", baseline, map[string]Prediction{"ap-nd": {1300, 24}, "np-ad": {900, 18}, "amd": {250, 29}}, "np-ad"},
		{"stable endpoint ties", baseline, map[string]Prediction{"amd/b": {250, 19}, "amd/a": {250, 19}}, "amd/a"},
		{"unavailable baseline", Prediction{}, map[string]Prediction{"amd": {900, 10}}, "nvidia"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			tc.alternatives["nvidia"] = tc.base
			if got := Choose("nvidia", tc.alternatives, 513); got != tc.want {
				t.Fatalf("selected %q, want %q", got, tc.want)
			}
		})
	}
	if got := Choose("nvidia", map[string]Prediction{"nvidia": {400, 0}, "amd": {300, 0}}, 1); got != "amd" {
		t.Fatalf("single-token path selected %q, want amd", got)
	}
}

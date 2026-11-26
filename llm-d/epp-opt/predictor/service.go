package predictor

import (
	"bufio"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"

	"github.com/llm-d/llm-d-router/eppopt/model"
)

const maxOnlineSamples = 8192

type snapshot struct {
	Version string
	Fitted  [][]model.Observation
	groups  map[groupKey]regression
}

type Service struct {
	calibration []model.Observation
	dir         string
	mu          sync.Mutex
	online      []model.Observation
	seen        map[string]bool
	current     atomic.Pointer[snapshot]
}

func New(seed, dir string) (*Service, error) {
	calibration, err := loadCalibration(seed)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(dir, 0750); err != nil {
		return nil, err
	}
	s := &Service{calibration: calibration, dir: dir, seen: make(map[string]bool)}
	if err := s.read("observations.json", &s.online); err != nil {
		return nil, err
	}
	for _, o := range s.online {
		s.seen[o.RequestID] = true
	}
	var saved snapshot
	if err := s.read("model.json", &saved); err != nil {
		return nil, err
	}
	saved.groups = make(map[groupKey]regression)
	for _, samples := range saved.Fitted {
		if len(samples) > 0 {
			saved.groups[key(samples[0].Features)] = fit(samples)
		}
	}
	s.current.Store(&saved)
	if err := s.Train(); err != nil {
		return nil, err
	}
	return s, nil
}

func loadCalibration(path string) ([]model.Observation, error) {
	if path == "" {
		return nil, nil
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var samples []model.Observation
	ids := make(map[string]bool)
	scanner := bufio.NewScanner(f)
	for line := 1; scanner.Scan(); line++ {
		var o model.Observation
		if err := json.Unmarshal(scanner.Bytes(), &o); err != nil || !o.Valid() || ids[o.RequestID] {
			return nil, fmt.Errorf("invalid or duplicate calibration observation at line %d", line)
		}
		ids[o.RequestID] = true
		samples = append(samples, o)
	}
	return samples, scanner.Err()
}

func (s *Service) read(name string, out any) error {
	bytes, err := os.ReadFile(filepath.Join(s.dir, name))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	return json.Unmarshal(bytes, out)
}

func (s *Service) persist(name string, value any) error {
	f, err := os.CreateTemp(s.dir, name+"-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if err = json.NewEncoder(f).Encode(value); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(f.Name(), filepath.Join(s.dir, name))
}

func (s *Service) observe(o model.Observation) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.seen[o.RequestID] {
		return nil
	}
	start := max(0, len(s.online)+1-maxOnlineSamples)
	next := append(append([]model.Observation(nil), s.online[start:]...), o)
	if err := s.persist("observations.json", next); err != nil {
		return err
	}
	for _, expired := range s.online[:start] {
		delete(s.seen, expired.RequestID)
	}
	s.seen[o.RequestID] = true
	s.online = next
	return nil
}

// Train swaps a snapshot only after independent holdout validation and persistence.
// A failing group retains its last working version without blocking other groups.
func (s *Service) Train() error {
	s.mu.Lock()
	online := s.online // immutable, observe replaces the slice on every write
	s.mu.Unlock()

	type partition struct{ train, validation []model.Observation }
	partitions := make(map[groupKey]*partition)
	seen := make(map[string]bool)
	for _, batch := range [][]model.Observation{s.calibration, online} {
		for _, o := range batch {
			if seen[o.RequestID] {
				continue
			}
			seen[o.RequestID] = true
			k := key(o.Features)
			p := partitions[k]
			if p == nil {
				p = &partition{}
				partitions[k] = p
			}
			if holdout(o.RequestID) {
				p.validation = append(p.validation, o)
			} else {
				p.train = append(p.train, o)
			}
		}
	}

	active := s.current.Load()
	next := &snapshot{groups: make(map[groupKey]regression)}
	for k, r := range active.groups {
		next.groups[k] = r
	}
	changed := false
	for _, fitted := range active.Fitted {
		next.Fitted = append(next.Fitted, fitted)
	}
	for k, p := range partitions {
		if len(p.train) < minTraining {
			continue
		}
		p.train = p.train[max(0, len(p.train)-maxOnlineSamples):]
		p.validation = p.validation[max(0, len(p.validation)-256):]
		candidate := fit(p.train)
		if !validate(candidate, active.groups[k], p.validation) {
			continue
		}
		replaced := false
		for i, fitted := range next.Fitted {
			if key(fitted[0].Features) == k {
				a, _ := json.Marshal(fitted)
				b, _ := json.Marshal(p.train)
				if string(a) != string(b) {
					next.Fitted[i], next.groups[k], changed = p.train, candidate, true
				}
				replaced = true
				break
			}
		}
		if !replaced {
			next.Fitted = append(next.Fitted, p.train)
			next.groups[k], changed = candidate, true
		}
	}
	if !changed {
		return nil
	}
	bytes, err := json.Marshal(next.Fitted)
	if err != nil {
		return err
	}
	next.Version = fmt.Sprintf("%x", sha256.Sum256(bytes))[:16]
	if err := s.persist("model.json", next); err != nil {
		return err
	}
	s.current.Store(next)
	slog.Info("updated latency models", "version", next.Version, "groups", len(next.groups))
	return nil
}

func parse(w http.ResponseWriter, r *http.Request, out any) bool {
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 256<<10))
	d.DisallowUnknownFields()
	if err := d.Decode(out); err != nil || d.Decode(new(any)) != io.EOF {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return false
	}
	return true
}

func (s *Service) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("POST /v1/predict", func(w http.ResponseWriter, r *http.Request) {
		var batch model.Batch
		if !parse(w, r, &batch) {
			return
		}
		if len(batch.Candidates) > 256 {
			http.Error(w, "too many candidates", http.StatusBadRequest)
			return
		}
		active := s.current.Load()
		answer := model.Predictions{Version: active.Version, ByID: make(map[string]model.Prediction)}
		for _, c := range batch.Candidates {
			if c.ID == "" || !c.Features.Valid() {
				http.Error(w, "invalid candidate", http.StatusBadRequest)
				return
			}
			if p, ok := active.groups[key(c.Features)].predict(c.Features); ok {
				answer.ByID[c.ID] = p
			}
			if r.Context().Err() != nil {
				return
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(answer)
	})
	mux.HandleFunc("POST /v1/observe", func(w http.ResponseWriter, r *http.Request) {
		var o model.Observation
		if !parse(w, r, &o) {
			return
		}
		if !o.Valid() {
			http.Error(w, "invalid observation", http.StatusBadRequest)
			return
		}
		if err := s.observe(o); err != nil {
			slog.Error("persist observation", "error", err)
			http.Error(w, "observation unavailable", http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	return mux
}

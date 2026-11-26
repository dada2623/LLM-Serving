package main

import (
	"context"
	"errors"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/llm-d/llm-d-router/eppopt/predictor"
)

func main() {
	address := flag.String("listen", ":8080", "HTTP listen address")
	seed := flag.String("calibration", "", "optional calibration observation JSONL")
	state := flag.String("state-dir", "/state", "persisted observations and models")
	interval := flag.Duration("train-interval", time.Minute, "interval between model updates")
	flag.Parse()
	if *interval <= 0 {
		slog.Error("train-interval must be positive")
		os.Exit(1)
	}
	service, err := predictor.New(*seed, *state)
	if err != nil {
		slog.Error("start predictor", "error", err)
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	server := &http.Server{Addr: *address, Handler: service.Handler(), ReadHeaderTimeout: 2 * time.Second, ReadTimeout: 5 * time.Second, WriteTimeout: 5 * time.Second}
	go func() {
		timer := time.NewTicker(*interval)
		defer timer.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-timer.C:
				if err := service.Train(); err != nil {
					slog.Error("update predictor", "error", err)
				}
			}
		}
	}()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("serve predictor", "error", err)
		os.Exit(1)
	}
}

// Command calibrate converts raw benchmark evidence into an EPP-Opt profile file.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"github.com/llm-d/llm-d-router/eppopt/model"
)

func main() {
	dir := flag.String("data", "", "directory holding idle-calibration.json and queued-calibration.json")
	flag.Parse()
	if *dir == "" {
		fmt.Fprintln(os.Stderr, "usage: calibrate -data DIR [PAIRED_RUN ...]")
		os.Exit(2)
	}
	rows, err := model.Calibrate(*dir, flag.Args())
	if err == nil {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		err = enc.Encode(rows)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

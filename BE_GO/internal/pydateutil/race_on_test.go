//go:build race

package pydateutil

// raceDetector is set under -race, which CI's race shards use: timing budgets
// then allow for the detector's slowdown and a shared runner.
const raceDetector = true

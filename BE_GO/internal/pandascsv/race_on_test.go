//go:build race

package pandascsv

// raceDetector is set under -race, which CI's tabular shard uses: the
// backtrack replay then samples its row-limit frames (see
// TestBacktrackMatchesPandas).
const raceDetector = true

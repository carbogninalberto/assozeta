package dto

import (
	"bytes"
	"errors"
	"math"
)

var ErrLoginRateJSONValue = errors.New("JSON value exceeds Python integer limit")

// ParseLoginRateJSON matches the json.loads call before DRF parsing. Unlike DRF's
// strict JSON parser, that call accepts NaN and infinities. Replace only complete
// unquoted literal tokens, retaining offsets and restoring their typed values.
// ParseReferenceJSON remains strict for the later authentication/view pipeline.
func ParseLoginRateJSON(data []byte) (any, error) {
	var rewritten []byte
	constants := map[int64]any{}
	for i := 0; i < len(data); {
		if data[i] == '"' {
			i++
			for i < len(data) {
				if data[i] == '\\' {
					i += 2
					continue
				}
				if data[i] == '"' {
					i++
					break
				}
				i++
			}
			continue
		}
		if bytes.IndexByte([]byte(" \t\r\n[]{}:,"), data[i]) >= 0 {
			i++
			continue
		}
		start := i
		for i < len(data) && bytes.IndexByte([]byte(" \t\r\n[]{}:,\""), data[i]) < 0 {
			i++
		}
		var value float64
		switch string(data[start:i]) {
		case "NaN":
			value = math.NaN()
		case "Infinity":
			value = math.Inf(1)
		case "-Infinity":
			value = math.Inf(-1)
		default:
			continue
		}
		if rewritten == nil {
			rewritten = bytes.Clone(data)
		}
		copy(rewritten[start:], "0.0")
		for j := start + 3; j < i; j++ {
			rewritten[j] = ' '
		}
		constants[int64(start+3)] = value
	}
	if rewritten != nil {
		data = rewritten
	}
	return parseReferenceJSON(data, constants, true)
}

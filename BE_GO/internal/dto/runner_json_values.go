package dto

import "errors"

// RunnerJSONValues is json.loads(response.read()) as RunnerJSONDecode reads
// it, except that a lone surrogate is kept: its string holds the code
// unit's three-byte encoding, which RenderableJSON reports as not
// renderable. deep=true is JSON nested past the decoder's limit.
func RunnerJSONValues(raw []byte) (value any, decoded, deep bool) {
	text, ok := runnerJSONText(raw)
	if !ok {
		return nil, false, false
	}
	if auditJSONDepth(text) > runnerJSONDepth {
		return nil, false, true
	}
	data := []byte(text)
	value, err := parseProfileTablesJSONConstants(data, pythonJSONConstants(data), true)
	switch {
	case err == nil:
		return value, true, false
	case errors.Is(err, ErrReferenceNesting):
		return nil, false, true
	default:
		return nil, false, false
	}
}

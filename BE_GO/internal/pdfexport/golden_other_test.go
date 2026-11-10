package pdfexport

import (
	"encoding/json"
	"testing"
	"time"
)

func buildGoldenOther(t *testing.T, engine string, raw json.RawMessage, now time.Time) ([]byte, error) {
	t.Helper()
	t.Fatalf("engine %s", engine)
	return nil, nil
}

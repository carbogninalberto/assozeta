//go:build manualavailabilityintegration

package manual

import (
	"context"
	"encoding/json"
	"os"
	"testing"
)

func TestManualAvailabilityOracle(t *testing.T) {
	path := os.Getenv("ASSOZETA_MANUAL_AVAILABILITY_FIXTURE")
	if path == "" {
		t.Fatal("manual availability fixture is required")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name     string
		Expected bool
		Settings Settings
	}
	if err = json.Unmarshal(raw, &cases); err != nil || len(cases) == 0 {
		t.Fatal("missing manual fixtures", err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			loader := New(tc.Settings)
			got, err := loader.Available(context.Background())
			if err != nil || got != tc.Expected {
				t.Fatalf("availability=%v error=%v want=%v", got, err, tc.Expected)
			}
		})
	}
}

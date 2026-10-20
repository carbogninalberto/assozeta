//go:build interop

package jwt

import (
	"encoding/json"
	"os"
	"testing"
	"time"
)

func TestDjangoTokenInteroperability(t *testing.T) {
	path := os.Getenv("ASSOZETA_CREDENTIAL_FIXTURE")
	if path == "" {
		t.Fatal("interop requires TESTS/adapters/django/credentials.py; no skipped test")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal("cannot read temporary fixture")
	}
	var fixture struct {
		Now     int64  `json:"now"`
		Private string `json:"private"`
		Public  string `json:"public"`
		Tokens  []struct {
			Name, Token, Kind string
			Valid             bool
		} `json:"tokens"`
		Refresh string `json:"refresh"`
	}
	if json.Unmarshal(raw, &fixture) != nil {
		t.Fatal("invalid fixture")
	}
	manager, err := New([]byte(fixture.Private), []byte(fixture.Public), func() time.Time { return time.Unix(fixture.Now, 0) })
	if err != nil {
		t.Fatal(err)
	}
	for _, vector := range fixture.Tokens {
		t.Run(vector.Name, func(t *testing.T) {
			_, err := manager.Verify(vector.Token, vector.Kind)
			if (err == nil) != vector.Valid {
				t.Fatalf("token acceptance differs: valid=%t", err == nil)
			}
		})
	}
	issued, err := manager.Issue("7b23bb0d-27a3-471a-bb05-2e9be79d52fb", 2, "member@example.test")
	if err != nil {
		t.Fatal(err)
	}
	refreshed, err := manager.Refresh(fixture.Refresh)
	if err != nil {
		t.Fatal(err)
	}
	output, err := json.Marshal(map[string]any{"issued": issued, "refreshed": refreshed})
	if err != nil {
		t.Fatal("cannot encode result")
	}
	if err := os.WriteFile(os.Getenv("ASSOZETA_TOKEN_RESULT"), output, 0600); err != nil {
		t.Fatal("cannot write result")
	}
}

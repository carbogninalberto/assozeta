//go:build manualsourceintegration

package manual

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The Python adapter runs the actual Django publisher in another process and
// holds its HTTP response while this process contends on the shared cache.
func TestManualRefreshCrossProcess(t *testing.T) {
	path := os.Getenv("ASSOZETA_MANUAL_LOCK_FIXTURE")
	if !strings.HasPrefix(filepath.Base(filepath.Dir(filepath.Dir(path))), "az-manual-lock-") {
		t.Fatal("private cross-process fixture required")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Settings  Settings
		ReadyPath string
		Cancel    bool
	}
	if err = json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if fixture.Cancel {
		var stop context.CancelFunc
		ctx, stop = context.WithTimeout(ctx, 300*time.Millisecond)
		defer stop()
	}
	if err = os.WriteFile(fixture.ReadyPath, []byte("ready"), 0600); err != nil {
		t.Fatal(err)
	}
	err = New(fixture.Settings).synchronize(ctx)
	if fixture.Cancel {
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("shared lock did not block until cancellation", err)
		}
	} else if err != nil {
		t.Fatal(err)
	}
}

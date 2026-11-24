//go:build handoff

package handoff

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

func Barrier(point, key string) {
	dir := os.Getenv("ASSOZETA_HANDOFF_BARRIER_DIR")
	if dir == "" {
		return
	}

	body, _ := json.Marshal(map[string]any{"pid": os.Getpid(), "point": point, "key": key})
	tmp := filepath.Join(dir, fmt.Sprintf(".tmp-%d-%d", os.Getpid(), time.Now().UnixNano()))
	if os.WriteFile(tmp, body, 0o600) == nil {
		_ = os.Rename(tmp, filepath.Join(dir, "arrived."+point+"."+key))
	}
	exists := func(name string) bool { _, err := os.Stat(filepath.Join(dir, name)); return err == nil }
	if !exists("hold."+point+"."+key) && !exists("hold."+point+".all") {
		return
	}
	deadline := time.Now().Add(120 * time.Second)
	for time.Now().Before(deadline) && (exists("hold."+point+"."+key) || exists("hold."+point+".all")) {
		if exists("release."+point+"."+key) || exists("release."+point+".all") {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
}

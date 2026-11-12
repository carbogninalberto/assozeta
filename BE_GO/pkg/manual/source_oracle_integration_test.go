//go:build manualsourceintegration

package manual

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestManualSourceOracle(t *testing.T) {
	path := os.Getenv("ASSOZETA_MANUAL_SOURCE_FIXTURE")
	if !strings.HasPrefix(filepath.Base(filepath.Dir(path)), "az-manual-http-") {
		t.Fatal("private HTTP fixture required")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var tc struct {
		Name      string
		Settings  Settings
		Features  []string
		AssetPath string
		Expected  struct {
			Status int
			Result string
			IDs    []string
			Kind   string
		}
	}
	if json.Unmarshal(data, &tc) != nil || tc.Name == "" {
		t.Fatal("invalid authored fixture")
	}
	l := New(tc.Settings)
	ctx := context.Background()
	if tc.AssetPath != "" {
		data, err := l.ReadAsset(ctx, tc.Features, tc.AssetPath)
		if tc.Expected.Status == 404 {
			if !errors.Is(err, ErrAssetNotFound) || data != nil {
				t.Fatal("asset should be unavailable", err)
			}
			return
		}
		if err != nil || !bytes.Equal(data, sourcePNG) {
			t.Fatal("verified source asset differs", err)
		}
		return
	}
	snapshot, err := l.LoadReader(ctx, tc.Features)
	if tc.Expected.Result == "no_evidence" {
		if !errors.Is(err, ErrReaderUnavailable) || snapshot != nil {
			t.Fatal("invalid source exposed", err)
		}
		return
	}
	if err != nil || snapshot == nil {
		t.Fatal("source unavailable", err)
	}
	if snapshot.IsPreview() != (tc.Expected.Result == "development_preview") {
		t.Fatal("preview precedence")
	}
	if !reflect.DeepEqual(snapshot.SectionIDs(), tc.Expected.IDs) {
		t.Fatal("source applicability", snapshot.SectionIDs(), tc.Expected.IDs)
	}
}

//go:build manualreaderintegration

package manual

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
)

type readerCase struct {
	Name      string   `json:"name"`
	Settings  Settings `json:"settings"`
	Features  []string `json:"features"`
	AssetPath string   `json:"asset_path"`
	Expected  struct {
		Unavailable bool     `json:"unavailable"`
		IDs         []string `json:"ids"`
		Asset       string   `json:"asset"`
	} `json:"expected"`
}
type readerFixture struct {
	Cases []readerCase      `json:"cases"`
	Bytes map[string]string `json:"bytes"`
}

func loadReaderFixture(t *testing.T) readerFixture {
	t.Helper()
	path := os.Getenv("ASSOZETA_MANUAL_READER_FIXTURE")
	if !strings.HasPrefix(filepath.Base(filepath.Dir(path)), "az-manual-reader-") {
		t.Fatal("private offline fixture required")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture readerFixture
	if json.Unmarshal(raw, &fixture) != nil || len(fixture.Cases) != 36 {
		t.Fatal("exact36-case manifest required")
	}
	return fixture
}
func TestManualReaderOracle(t *testing.T) {
	fixture := loadReaderFixture(t)
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			snapshot, err := New(tc.Settings).LoadPublicBundle(context.Background(), tc.Features)
			if tc.Expected.Unavailable {
				if !errors.Is(err, ErrReaderUnavailable) || snapshot != nil {
					t.Fatal("invalid bundle was exposed")
				}
				if strings.Contains(err.Error(), tc.Settings.BundlePath) {
					t.Fatal("path leaked")
				}
				return
			}
			if err != nil || snapshot == nil {
				t.Fatal("valid bundle unavailable", err)
			}
			if !reflect.DeepEqual(snapshot.SectionIDs(), tc.Expected.IDs) {
				t.Fatal("public applicability/order", snapshot.SectionIDs(), tc.Expected.IDs)
			}
			actual, err := snapshot.ReadAsset(context.Background(), tc.AssetPath)
			if tc.Expected.Asset == "not_found" {
				if !errors.Is(err, ErrAssetNotFound) || actual != nil {
					t.Fatal("asset denial changed", err)
				}
				return
			}
			expected, _ := base64.StdEncoding.DecodeString(fixture.Bytes[tc.Expected.Asset])
			if err != nil || !bytes.Equal(actual, expected) {
				t.Fatal("verified asset bytes differ", err)
			}
		})
	}
}
func privateReaderCopy(t *testing.T, original Settings) Settings {
	t.Helper()
	root := t.TempDir()
	parent := filepath.Dir(original.BundlePath)
	err := filepath.WalkDir(parent, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relative, e := filepath.Rel(parent, path)
		if e != nil {
			return e
		}
		target := filepath.Join(root, relative)
		if entry.IsDir() {
			return os.MkdirAll(target, 0700)
		}
		data, e := os.ReadFile(path)
		if e != nil {
			return e
		}
		return os.WriteFile(target, data, 0600)
	})
	if err != nil {
		t.Fatal(err)
	}
	original.BundlePath = filepath.Join(root, "bundle")
	original.SourceRoot = filepath.Join(root, "source")
	return original
}
func TestManualReaderCopiesAndLiveChecks(t *testing.T) {
	fixture := loadReaderFixture(t)
	settings := privateReaderCopy(t, fixture.Cases[0].Settings)
	loader := New(settings)
	ctx := context.Background()
	load := func(features []string) *PublicBundle {
		t.Helper()
		value, err := loader.LoadPublicBundle(ctx, features)
		if err != nil {
			t.Fatal(err)
		}
		return value
	}
	t.Run("request-local features ids and bytes", func(t *testing.T) {
		features := []string{"self_hosted"}
		first := load(features)
		features[0] = "other"
		ids := first.SectionIDs()
		ids[0] = "mutated"
		if !reflect.DeepEqual(first.SectionIDs(), []string{"public-z", "public-a", "host"}) || !reflect.DeepEqual(load(nil).SectionIDs(), []string{"public-z", "public-a"}) {
			t.Fatal("shared mutable applicability")
		}
		data, err := first.ReadAsset(ctx, "images/z.png")
		if err != nil {
			t.Fatal(err)
		}
		expected := append([]byte(nil), data...)
		data[0] ^= 0xff
		next, err := first.ReadAsset(ctx, "images/z.png")
		if err != nil || !bytes.Equal(next, expected) {
			t.Fatal("returned bytes aliased")
		}
	})
	t.Run("live source rehash and restoration", func(t *testing.T) {
		path := filepath.Join(settings.SourceRoot, "BE/z.py")
		original, _ := os.ReadFile(path)
		if err := os.WriteFile(path, []byte("changed"), 0600); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(load(nil).SectionIDs(), []string{"public-a"}) {
			t.Fatal("source cache hid live edit")
		}
		os.WriteFile(path, original, 0600)
		if !reflect.DeepEqual(load(nil).SectionIDs(), []string{"public-z", "public-a"}) {
			t.Fatal("source restoration stale")
		}
	})
	t.Run("read once bytes survive replacement", func(t *testing.T) {
		snapshot := load(nil)
		path := filepath.Join(settings.BundlePath, "images/z.png")
		original, _ := os.ReadFile(path)
		data, err := snapshot.ReadAsset(ctx, "images/z.png")
		if err != nil {
			t.Fatal(err)
		}
		os.WriteFile(path, []byte("tampered"), 0600)
		if !bytes.Equal(data, original) {
			t.Fatal("verified response buffer changed")
		}
		if _, err = snapshot.ReadAsset(ctx, "images/z.png"); !errors.Is(err, ErrAssetNotFound) {
			t.Fatal("asset hash reused")
		}
		os.WriteFile(path, original, 0600)
	})
	t.Run("receipt and index changes never reuse prior validity", func(t *testing.T) {
		indexPath := filepath.Join(settings.BundlePath, "index.json")
		receiptPath := filepath.Join(settings.BundlePath, "bundle.json")
		originalIndex, _ := os.ReadFile(indexPath)
		originalReceipt, _ := os.ReadFile(receiptPath)
		defer os.WriteFile(indexPath, originalIndex, 0600)
		defer os.WriteFile(receiptPath, originalReceipt, 0600)
		first := load(nil)
		os.WriteFile(receiptPath, []byte("{}"), 0600)
		if _, err := loader.LoadPublicBundle(ctx, nil); !errors.Is(err, ErrReaderUnavailable) {
			t.Fatal("receipt ignored")
		}
		os.WriteFile(receiptPath, originalReceipt, 0600)
		decoded, decodeErr := decodeJSON(originalIndex)
		if decodeErr != nil {
			t.Fatal(decodeErr)
		}
		value := decoded.(map[string]any)
		value["chunks"].([]any)[0].(map[string]any)["status"] = "pending"
		delete(value, "identity")
		sealed, err := canonical(value)
		if err != nil {
			t.Fatal(err)
		}
		value["identity"] = hash(sealed)
		raw, _ := json.Marshal(value)
		var receipt map[string]any
		json.Unmarshal(originalReceipt, &receipt)
		receipt["index_sha256"] = hash(raw)
		receipt["corpus_identity"] = value["identity"]
		receiptRaw, _ := json.Marshal(receipt)
		os.WriteFile(indexPath+".next", raw, 0600)
		os.Rename(indexPath+".next", indexPath)
		os.WriteFile(receiptPath, receiptRaw, 0600)
		if !reflect.DeepEqual(load(nil).SectionIDs(), []string{"public-a"}) || !reflect.DeepEqual(first.SectionIDs(), []string{"public-z", "public-a"}) {
			t.Fatal("index replacement or old request snapshot changed")
		}
		os.WriteFile(indexPath, []byte("broken"), 0600)
		if _, err := loader.LoadPublicBundle(ctx, nil); !errors.Is(err, ErrReaderUnavailable) {
			t.Fatal("corrupted index reused")
		}
	})
	t.Run("cancellation and concurrent request-local views", func(t *testing.T) {
		canceled, cancel := context.WithCancel(ctx)
		cancel()
		if _, err := loader.LoadPublicBundle(canceled, nil); !errors.Is(err, context.Canceled) {
			t.Fatal("load swallowed cancellation")
		}
		snapshot := load(nil)
		if _, err := snapshot.ReadAsset(canceled, "images/z.png"); !errors.Is(err, context.Canceled) {
			t.Fatal("asset swallowed cancellation")
		}
		if _, err := loader.LoadPublicBundle(nil, nil); err == nil {
			t.Fatal("nil context accepted")
		}
		if _, err := snapshot.ReadAsset(nil, "images/z.png"); err == nil {
			t.Fatal("nil context accepted")
		}
		var wait sync.WaitGroup
		for i := 0; i < 32; i++ {
			wait.Add(1)
			go func(i int) {
				defer wait.Done()
				features := []string{}
				count := 2
				if i%2 == 0 {
					features = []string{"self_hosted"}
					count = 3
				}
				value, err := loader.LoadPublicBundle(ctx, features)
				if err != nil || len(value.SectionIDs()) != count {
					t.Error("concurrent applicability", err)
				}
			}(i)
		}
		wait.Wait()
	})
}

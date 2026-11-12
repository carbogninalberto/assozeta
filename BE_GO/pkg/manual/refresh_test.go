package manual

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

var sourcePNG = []byte("\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01")

func remoteCorpus(t *testing.T, root, origin string) []byte {
	t.Helper()
	path := filepath.Join(root, "publisher.json")
	v := sourceCorpus(t, path)
	meta := v["metadata"].(map[string]any)
	meta["application_revision"] = strings.Repeat("a", 40)
	meta["manual_revision"] = strings.Repeat("c", 40)
	meta["manual_state"] = "committed"
	for _, raw := range v["chunks"].([]any) {
		m := raw.(map[string]any)
		m["screenshots"] = []any{map[string]any{"path": "images/a.png", "sha256": hash(sourcePNG), "url": origin + "/image.png"}}
	}
	writeSourceCorpus(t, path, v)
	raw, _ := os.ReadFile(path)
	return raw
}
func TestManualRefreshAtomicAssetsAndFailure(t *testing.T) {
	root := t.TempDir()
	var payload []byte
	var corrupt atomic.Bool
	entered := make(chan struct{}, 1)
	release := make(chan struct{})
	var held atomic.Bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/image.png" {
			if held.Load() {
				entered <- struct{}{}
				select {
				case <-release:
				case <-r.Context().Done():
					return
				}
			}
			if corrupt.Load() {
				w.Write([]byte("wrong"))
			} else {
				w.Write(sourcePNG)
			}
		} else {
			w.Write(payload)
		}
	}))
	defer server.Close()
	payload = remoteCorpus(t, root, server.URL)
	s := Settings{BundlePath: filepath.Join(root, "absent"), IndexPath: filepath.Join(root, "cache/index.json"), CorpusBaseURL: server.URL, ApplicationRevision: strings.Repeat("a", 40), Release: "release", RunID: "fixture"}
	os.MkdirAll(filepath.Dir(s.IndexPath), 0700)
	old := []byte("old active corpus")
	os.WriteFile(s.IndexPath, old, 0600)
	l := New(s)
	corrupt.Store(true)
	if err := l.synchronize(context.Background()); err == nil {
		t.Fatal("accepted invalid image")
	}
	got, _ := os.ReadFile(s.IndexPath)
	if string(got) != string(old) {
		t.Fatal("failed download changed active index")
	}
	corrupt.Store(false)
	held.Store(true)
	done := make(chan error, 1)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { done <- l.synchronize(ctx) }()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("image request missing")
	}
	got, _ = os.ReadFile(s.IndexPath)
	if string(got) != string(old) {
		t.Fatal("promoted before image verification")
	}
	close(release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	snapshot, err := l.LoadReader(context.Background(), nil)
	if err != nil || len(snapshot.SectionIDs()) != 1 {
		t.Fatal("new corpus unavailable", err)
	}
	b, err := l.ReadAsset(context.Background(), nil, "images/a.png")
	if err != nil || string(b) != string(sourcePNG) {
		t.Fatal("content-addressed assets", err)
	}
}
func TestManualRefreshThrottleAndConcurrentLoaders(t *testing.T) {
	root := t.TempDir()
	var payload []byte
	var corpus, image atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/image.png" {
			image.Add(1)
			w.Write(sourcePNG)
		} else {
			corpus.Add(1)
			w.Write(payload)
		}
	}))
	defer server.Close()
	payload = remoteCorpus(t, root, server.URL)
	s := Settings{BundlePath: filepath.Join(root, "absent"), IndexPath: filepath.Join(root, "cache/index.json"), CorpusBaseURL: server.URL, ApplicationRevision: strings.Repeat("a", 40), Release: "release", RunID: "fixture"}
	var wg sync.WaitGroup
	errs := make(chan error, 12)
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); _, err := New(s).LoadReader(context.Background(), nil); errs <- err }()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	if corpus.Load() != 1 || image.Load() != 1 {
		t.Fatal("refresh must serialize/throttle and deduplicate hashes", corpus.Load(), image.Load())
	}
}
func TestManualRefreshFileLockCancellation(t *testing.T) {
	root := t.TempDir()
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { requests.Add(1) }))
	defer server.Close()
	lock, err := os.OpenFile(filepath.Join(root, ".sync.lock"), os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	l := New(Settings{IndexPath: filepath.Join(root, "index.json"), CorpusBaseURL: server.URL, ApplicationRevision: strings.Repeat("a", 40), Release: "release", RunID: "fixture"})
	if err = l.synchronize(ctx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("lock wait cancellation", err)
	}
	if requests.Load() != 0 {
		t.Fatal("download before shared lock")
	}
}
func TestManualRefreshURLPolicy(t *testing.T) {
	for _, u := range []string{"http://example.test/x", "https://user:password@example.test/x", "https://example.test/x#fragment", "file:///tmp/index", "https://example.test/base?query=yes"} {
		l := New(Settings{CorpusBaseURL: u, ApplicationRevision: strings.Repeat("a", 40), Release: "release"})
		if _, err := l.corpusURL(); err == nil {
			t.Fatalf("accepted invalid configured source %q", u)
		}
	}
	l := New(Settings{CorpusBaseURL: "https://example.test/base/", ApplicationRevision: strings.Repeat("a", 40), Release: "release /+"})
	u, err := l.corpusURL()
	if err != nil || !strings.HasSuffix(u, "/release%20%2F%2B.json") {
		t.Fatal("release URL encoding", u, err)
	}
}
func TestManualRefreshRetiresOnlyOwnedInactiveFiles(t *testing.T) {
	root := t.TempDir()
	l := New(Settings{IndexPath: filepath.Join(root, "index.json")})
	assets := filepath.Join(root, "assets")
	os.MkdirAll(assets, 0700)
	active := strings.Repeat("a", 64)
	old := strings.Repeat("b", 64)
	other := filepath.Join(root, "outside.png")
	os.WriteFile(other, sourcePNG, 0600)
	for _, name := range []string{active + ".png", old + ".png", "unowned.png"} {
		p := filepath.Join(assets, name)
		os.WriteFile(p, sourcePNG, 0600)
		os.Chtimes(p, time.Now().Add(-48*time.Hour), time.Now().Add(-48*time.Hour))
	}
	os.Symlink(other, filepath.Join(assets, strings.Repeat("c", 64)+".png"))
	l.pruneAssets(map[string]bool{active: true})
	if _, err := os.Stat(filepath.Join(assets, old+".png")); !os.IsNotExist(err) {
		t.Fatal("expired inactive retained")
	}
	for _, p := range []string{filepath.Join(assets, active+".png"), filepath.Join(assets, "unowned.png"), other} {
		if _, err := os.Stat(p); err != nil {
			t.Fatal("owned active or unrelated file removed")
		}
	}
}

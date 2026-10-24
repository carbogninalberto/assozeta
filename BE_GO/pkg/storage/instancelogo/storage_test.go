package instancelogo

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func logoCredentials(context.Context) (aws.CredentialsProvider, error) {
	return credentials.NewStaticCredentialsProvider("fixture", "private-test-only", ""), nil
}
func waitFor(t *testing.T, predicate func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for !predicate() {
		if time.Now().After(deadline) {
			t.Fatal("resource did not reach expected terminal state")
		}
		time.Sleep(5 * time.Millisecond)
	}
}
func spoolCount(t *testing.T, dir string) int {
	t.Helper()
	files, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	return len(files)
}

func TestDiskLogoFilesystemAndClose(t *testing.T) {
	dir := t.TempDir()
	if err := os.Mkdir(filepath.Join(dir, "instance"), 0700); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(t.TempDir(), "owned-external-target")
	if err := os.WriteFile(target, []byte("exact bytes"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, filepath.Join(dir, "instance/logo.png")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(dir, "absent"), filepath.Join(dir, "instance/logo.jpg")); err != nil {
		t.Fatal(err)
	}
	store, err := NewDisk(dir)
	if err != nil {
		t.Fatal(err)
	}
	exists, err := store.Exists(context.Background(), "instance/logo.jpg")
	if !exists || err != nil {
		t.Fatal(exists, err)
	}
	if _, err = store.Open(context.Background(), "instance/logo.jpg"); err == nil {
		t.Fatal("broken link opened")
	}
	file, err := store.Open(context.Background(), "instance/logo.png")
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(file.Body)
	if err != nil || string(body) != "exact bytes" || file.Size != 11 {
		t.Fatal(string(body), file.Size, err)
	}
	if err = file.Body.Close(); err != nil {
		t.Fatal(err)
	}
	if err = file.Body.Close(); err != nil {
		t.Fatal(err)
	}
	if len(store.admission.slots) != 0 {
		t.Fatal("disk lease retained")
	}
	if _, err = store.Exists(context.Background(), "../escape"); err == nil {
		t.Fatal("unsafe lexical path accepted")
	}
}

func TestS3LogoLeaseOwnsSpoolThroughCloseAndCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/bucket/prefix/instance/logo.png" {
			t.Errorf("unexpected path %s", r.URL.Path)
		}
		w.Header().Set("Content-Length", "11")
		if r.Method != "HEAD" {
			io.WriteString(w, "exact bytes")
		}
	}))
	defer server.Close()
	dir := t.TempDir()
	store := NewS3(S3Options{Endpoint: server.URL, Region: "us-east-1", Bucket: "bucket", Location: "prefix", Credentials: logoCredentials, TempDir: dir})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var bodies []io.ReadCloser
	for i := 0; i < 4; i++ {
		file, err := store.Open(ctx, "instance/logo.png")
		if err != nil {
			t.Fatal(err)
		}
		bodies = append(bodies, file.Body)
	}
	if len(store.admission.slots) != 4 || len(unlinkedSpools(t, dir)) != 4 || spoolCount(t, dir) != 0 {
		t.Fatal("spools not retained until close")
	}
	for _, stat := range unlinkedSpools(t, dir) {
		if stat.Mode&0777 != 0600 || stat.Size != 11 || stat.Nlink != 0 {
			t.Fatal("invalid private anonymous spool", stat)
		}
	}
	short, stop := context.WithTimeout(ctx, 30*time.Millisecond)
	defer stop()
	if _, err := store.Open(short, "instance/logo.png"); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	bodies[0].Close()
	if len(unlinkedSpools(t, dir)) != 3 || spoolCount(t, dir) != 0 {
		t.Fatal("close did not remove spool")
	}
	replacement, err := store.Open(ctx, "instance/logo.png")
	if err != nil {
		t.Fatal(err)
	}
	defer replacement.Body.Close()
	cancel()
	waitFor(t, func() bool {
		return len(store.admission.slots) == 0 && len(unlinkedSpools(t, dir)) == 0 && spoolCount(t, dir) == 0
	})
	for _, body := range bodies {
		body.Close()
	}
}

func TestS3LogoCancellationDuringDownloadBeforeProviderRelease(t *testing.T) {
	entered, ended, release := make(chan struct{}, 1), make(chan struct{}, 1), make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", "100000")
		if r.Method == "HEAD" {
			return
		}
		io.WriteString(w, "prefix")
		w.(http.Flusher).Flush()
		entered <- struct{}{}
		select {
		case <-r.Context().Done():
			ended <- struct{}{}
		case <-release:
		}
	}))
	defer server.Close()
	defer close(release)
	dir := t.TempDir()
	store := NewS3(S3Options{Endpoint: server.URL, Region: "us-east-1", Bucket: "bucket", Credentials: logoCredentials, TempDir: dir})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := store.Open(ctx, "instance/logo.png"); done <- err }()
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("provider read not entered")
	}
	waitFor(t, func() bool { return len(unlinkedSpools(t, dir)) == 1 && spoolCount(t, dir) == 0 })
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("download did not cancel")
	}
	select {
	case <-ended:
	case <-time.After(3 * time.Second):
		t.Fatal("upstream body remained open before release")
	}
	if len(store.admission.slots) != 0 || len(unlinkedSpools(t, dir)) != 0 || spoolCount(t, dir) != 0 {
		t.Fatal("canceled spool retained")
	}
}

func TestS3LogoLazyInitializationFailureCanRecover(t *testing.T) {
	var calls atomic.Int32
	store := NewS3(S3Options{Endpoint: "http://127.0.0.1:1", Region: "us-east-1", Bucket: "bucket", Credentials: func(ctx context.Context) (aws.CredentialsProvider, error) { calls.Add(1); return nil, context.Canceled }})
	if calls.Load() != 0 {
		t.Fatal("provider initialized before request")
	}
	for i := 0; i < 2; i++ {
		if _, err := store.Exists(context.Background(), "instance/logo.png"); !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	}
	if calls.Load() != 2 || len(store.admission.slots) != 0 {
		t.Fatal("failed provider initialization cached or lease leaked")
	}
}

func TestS3LogoTimeoutWithLiveCallerAndRecovery(t *testing.T) {
	for _, stage := range []string{"headers", "body"} {
		t.Run(stage, func(t *testing.T) {
			var stalled atomic.Bool
			stalled.Store(true)
			var active atomic.Int32
			release := make(chan struct{})
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				active.Add(1)
				defer active.Add(-1)
				if stalled.Load() && (stage == "headers" || r.Method == "GET") {
					if stage == "body" {
						w.Header().Set("Content-Length", "11")
						io.WriteString(w, "x")
						w.(http.Flusher).Flush()
					}
					select {
					case <-r.Context().Done():
					case <-release:
					}
					return
				}
				w.Header().Set("Content-Length", "11")
				if r.Method == "GET" {
					io.WriteString(w, "exact bytes")
				}
			}))
			defer server.Close()
			defer close(release)
			dir := t.TempDir()
			store := NewS3(S3Options{Endpoint: server.URL, Region: "us-east-1", Bucket: "bucket", Credentials: logoCredentials, TempDir: dir, IOTimeout: 30 * time.Millisecond})
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			done := make(chan error, 1)
			go func() { _, err := store.Open(ctx, "instance/logo.png"); done <- err }()
			select {
			case err := <-done:
				if err == nil {
					t.Fatal("stalled provider succeeded")
				}
			case <-time.After(45 * time.Second):
				cancel()
				t.Fatal("live caller was not bounded by provider idle timeout")
			}
			if ctx.Err() != nil {
				t.Fatal("caller cancellation was mistaken for provider timeout")
			}
			waitFor(t, func() bool {
				return active.Load() == 0 && len(store.admission.slots) == 0 && len(unlinkedSpools(t, dir)) == 0 && spoolCount(t, dir) == 0
			})
			stalled.Store(false)
			file, err := store.Open(ctx, "instance/logo.png")
			if err != nil {
				t.Fatal(err)
			}
			body, err := io.ReadAll(file.Body)
			file.Body.Close()
			if err != nil || string(body) != "exact bytes" {
				t.Fatal(string(body), err)
			}
		})
	}
}

func TestS3LogoSpoolCreationFailureReleasesLease(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", "1")
		if r.Method == "GET" {
			io.WriteString(w, "x")
		}
	}))
	defer server.Close()
	dir := t.TempDir()
	invalid := filepath.Join(dir, "file")
	os.WriteFile(invalid, []byte("not a directory"), 0600)
	store := NewS3(S3Options{Endpoint: server.URL, Region: "us-east-1", Bucket: "bucket", Credentials: logoCredentials, TempDir: invalid})
	if _, err := store.Open(context.Background(), "instance/logo.png"); err == nil {
		t.Fatal("unwritable spool succeeded")
	}
	if len(store.admission.slots) != 0 {
		t.Fatal("failed preparation retained admission")
	}
	store.options.TempDir = dir
	file, err := store.Open(context.Background(), "instance/logo.png")
	if err != nil {
		t.Fatal(err)
	}
	file.Body.Close()
	if spoolCount(t, dir) != 1 {
		t.Fatal("unexpected spool leak")
	}
}

func TestS3LogoPathNormalization(t *testing.T) {
	for _, tt := range []struct {
		location, key string
		bad           bool
	}{{"", "instance/logo.png", false}, {"media/", "media/instance/logo.png", false}, {".", "", true}, {"a/../b", "", true}} {
		store := NewS3(S3Options{Location: tt.location})
		key, err := store.key("instance/logo.png")
		if (err != nil) != tt.bad || key != tt.key || (tt.bad && !errors.Is(err, domain.ErrInstanceLogoPath)) {
			t.Fatal(tt, key, err)
		}
	}
	if !strings.Contains(ErrStorage.Error(), "storage") {
		t.Fatal("generic storage error changed")
	}
}

func TestS3LogoProgressRefreshesIdleTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", "12")
		if r.Method == "HEAD" {
			return
		}
		for i := 0; i < 12; i++ {
			io.WriteString(w, "x")
			w.(http.Flusher).Flush()
			time.Sleep(20 * time.Millisecond)
		}
	}))
	defer server.Close()
	store := NewS3(S3Options{Endpoint: server.URL, Region: "us-east-1", Bucket: "bucket", Credentials: logoCredentials, TempDir: t.TempDir(), IOTimeout: 100 * time.Millisecond})
	start := time.Now()
	file, err := store.Open(context.Background(), "instance/logo.png")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Body.Close()
	body, err := io.ReadAll(file.Body)
	if err != nil || string(body) != strings.Repeat("x", 12) || time.Since(start) < 100*time.Millisecond {
		t.Fatal(string(body), err)
	}
}

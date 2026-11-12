//go:build logoresourceintegration && linux

package instancelogo

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"sync/atomic"
	"syscall"
	"testing"
)

// This test requires an owned container with kernel seccomp denying unlink and
// unlinkat. The real filesystem operation fails; no production hook substitutes
// the result. The empty pathname cannot be removed under a persistent denial,
// so the runner must destroy its owned tmpfs/container after inspecting it.
func TestLogoKernelUnlinkDenialBeforePayload(t *testing.T) {
	dir := os.Getenv("ASSOZETA_LOGO_UNLINK_DENIED_TMPFS")
	if dir != "/spool" {
		t.Fatal("requires explicitly owned /spool tmpfs and unlink-denying seccomp")
	}
	var gets, heads atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", "15")
		if r.Method == "HEAD" {
			heads.Add(1)
			return
		}
		gets.Add(1)
		io.WriteString(w, "private-payload")
	}))
	defer server.Close()
	store := NewS3(S3Options{Endpoint: server.URL, Region: "us-east-1", Bucket: "bucket", Credentials: logoCredentials, TempDir: dir})
	file, err := store.Open(context.Background(), "instance/logo.png")
	if file.Body != nil {
		file.Body.Close()
		t.Fatal("denied unlink returned a body")
	}
	if !errors.Is(err, os.ErrPermission) {
		t.Fatalf("expected kernel EPERM, got %v", err)
	}
	if gets.Load() != 0 || heads.Load() != 1 || len(store.admission.slots) != 0 {
		t.Fatalf("payload or lease escaped: GET=%d HEAD=%d leases=%d", gets.Load(), heads.Load(), len(store.admission.slots))
	}
	names, err := os.ReadDir(dir)
	if err != nil || len(names) != 1 {
		t.Fatal(names, err)
	}
	info, err := os.Stat(filepath.Join(dir, names[0].Name()))
	if err != nil {
		t.Fatal(err)
	}
	if info.Size() != 0 || info.Mode().Perm() != 0600 {
		t.Fatal("unlink denial retained payload or unsafe permissions", info)
	}
	inode := info.Sys().(*syscall.Stat_t)
	directory, err := os.Open("/dev/fd")
	if err != nil {
		t.Fatal(err)
	}
	descriptors, err := directory.Readdirnames(-1)
	directory.Close()
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range descriptors {
		fd, err := strconv.Atoi(name)
		if err != nil {
			continue
		}
		var stat syscall.Stat_t
		if syscall.Fstat(fd, &stat) == nil && stat.Dev == inode.Dev && stat.Ino == inode.Ino {
			t.Fatalf("empty spool descriptor %d was not closed", fd)
		}
	}
	t.Log("real kernel unlink EPERM; HEAD1/GET0; descriptor and admission released; one empty0600 pathname requires owned tmpfs teardown")
}

package instancelogo

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"syscall"
	"testing"
)

// Fstat observes allocated anonymous files even though pathname enumeration is
// empty. Tests run serially in this process; matching the owned directory's
// device and zero-link regular files avoids counting pipes, sockets or Disk.
func unlinkedSpools(t *testing.T, dir string) []syscall.Stat_t {
	t.Helper()
	var directory syscall.Stat_t
	if err := syscall.Stat(dir, &directory); err != nil {
		t.Fatal(err)
	}
	fdDirectory, err := os.Open("/dev/fd")
	if err != nil {
		t.Fatal(err)
	}
	entries, err := fdDirectory.Readdirnames(-1)
	fdDirectory.Close()
	if err != nil {
		t.Fatal(err)
	}
	var files []syscall.Stat_t
	for _, entry := range entries {
		fd, err := strconv.Atoi(entry)
		if err != nil {
			continue
		}
		var stat syscall.Stat_t
		if syscall.Fstat(fd, &stat) == nil && stat.Dev == directory.Dev && stat.Nlink == 0 && stat.Mode&syscall.S_IFMT == syscall.S_IFREG {
			files = append(files, stat)
		}
	}
	return files
}

func TestS3LogoUnlinksBeforeRemotePayload(t *testing.T) {
	dir := t.TempDir()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", "11")
		if r.Method == "GET" {
			if spoolCount(t, dir) != 0 {
				t.Error("private spool pathname existed when remote payload began")
			}
			files := unlinkedSpools(t, dir)
			if len(files) != 1 || files[0].Mode&0777 != 0600 || files[0].Size != 0 {
				t.Errorf("missing empty private unlinked descriptor: %+v", files)
			}
			io.WriteString(w, "exact bytes")
		}
	}))
	defer server.Close()
	store := NewS3(S3Options{Endpoint: server.URL, Region: "us-east-1", Bucket: "bucket", Credentials: logoCredentials, TempDir: dir})
	file, err := store.Open(context.Background(), "instance/logo.png")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Body.Close()
	descriptors := unlinkedSpools(t, dir)
	if spoolCount(t, dir) != 0 || len(descriptors) != 1 || descriptors[0].Size != 11 {
		t.Fatalf("prepared spool lifetime not retained anonymously: %+v", descriptors)
	}
	body, err := io.ReadAll(file.Body)
	if err != nil || string(body) != "exact bytes" {
		t.Fatal(string(body), err)
	}
	file.Body.Close()
	if len(unlinkedSpools(t, dir)) != 0 || len(store.admission.slots) != 0 {
		t.Fatal("closed anonymous descriptor retained")
	}
}

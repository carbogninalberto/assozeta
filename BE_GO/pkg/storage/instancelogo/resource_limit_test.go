//go:build logoresourceintegration

package instancelogo_test

import (
	"context"
	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/instancelogo"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
)

// Run only inside the owned disposable container with /spool as a 64MiB tmpfs,
// retaining the former constrained-capacity boundary. Production now uses a
// dedicated disk-backed logo volume. The provider is a local HTTP fixture;
// this proves real local ENOSPC handling, not MinIO or arbitrary-size parity.
func TestLogoSpoolTmpfsPressureAndRecovery(t *testing.T) {
	dir := os.Getenv("ASSOZETA_LOGO_RESOURCE_TMPFS")
	if dir != "/spool" {
		t.Fatal("requires explicitly owned /spool 64MiB tmpfs")
	}
	var size atomic.Int64
	size.Store(65 << 20)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		png := strings.HasSuffix(r.URL.Path, ".png")
		if !png && !strings.HasSuffix(r.URL.Path, ".jpg") {
			w.WriteHeader(404)
			return
		}
		n := int64(3)
		if png {
			n = size.Load()
		}
		w.Header().Set("Content-Length", strconv.FormatInt(n, 10))
		if r.Method == "HEAD" {
			return
		}
		if !png {
			io.WriteString(w, "jpg")
			return
		}
		chunk := make([]byte, 64<<10)
		for n > 0 {
			count := int64(len(chunk))
			if n < count {
				count = n
			}
			written, err := w.Write(chunk[:count])
			if err != nil {
				return
			}
			n -= int64(written)
		}
	}))
	defer server.Close()
	store := instancelogo.NewS3(instancelogo.S3Options{Endpoint: server.URL, Region: "us-east-1", Bucket: "bucket", TempDir: dir,
		Credentials: func(context.Context) (aws.CredentialsProvider, error) {
			return credentials.NewStaticCredentialsProvider("fixture", "private-test-only", ""), nil
		}})
	reader := service.NewInstanceLogo(store)
	assertSpools := func(count int) {
		t.Helper()
		files, err := os.ReadDir(dir)
		if err != nil || len(files) != 0 {
			t.Fatalf("named private files: %v %v", files, err)
		}
		var directory syscall.Stat_t
		if err = syscall.Stat(dir, &directory); err != nil {
			t.Fatal(err)
		}
		fds, err := os.Open("/dev/fd")
		if err != nil {
			t.Fatal(err)
		}
		names, err := fds.Readdirnames(-1)
		fds.Close()
		if err != nil {
			t.Fatal(err)
		}
		actual := 0
		for _, name := range names {
			fd, err := strconv.Atoi(name)
			if err != nil {
				continue
			}
			var stat syscall.Stat_t
			if syscall.Fstat(fd, &stat) == nil && stat.Dev == directory.Dev && stat.Nlink == 0 && stat.Mode&syscall.S_IFMT == syscall.S_IFREG {
				actual++
			}
		}
		if actual != count {
			t.Fatalf("anonymous descriptors=%d want=%d", actual, count)
		}
	}
	logo, err := reader.Read(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if logo.Filename != "logo.jpg" || logo.Size != 3 {
		t.Fatalf("ENOSPC did not fall through: %+v", logo)
	}
	assertSpools(1)
	if err = logo.Body.Close(); err != nil {
		t.Fatal(err)
	}
	assertSpools(0)
	t.Log("65MiB PNG exceeded actual 64MiB tmpfs; failed spool removed before JPG fallback; final close emptied mount")
	size.Store(20 << 20)
	held := []io.Closer{}
	defer func() {
		for _, body := range held {
			body.Close()
		}
	}()
	for i := 0; i < 3; i++ {
		value, err := reader.Read(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		if value.Filename != "logo.png" {
			t.Fatal("unexpected early pressure")
		}
		held = append(held, value.Body)
	}
	value, err := reader.Read(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if value.Filename != "logo.jpg" {
		value.Body.Close()
		t.Fatal("fourth 20MiB download should encounter real capacity failure")
	}
	assertSpools(4)
	value.Body.Close()
	for _, body := range held {
		body.Close()
	}
	held = nil
	assertSpools(0)
	recovered, err := reader.Read(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if recovered.Filename != "logo.png" || recovered.Size != 20<<20 {
		recovered.Body.Close()
		t.Fatal("capacity recovery failed")
	}
	recovered.Body.Close()
	assertSpools(0)
	t.Log("three held 20MiB bodies retained their slots/spools; fourth encountered capacity pressure and JPG fallback; all releases restored PNG delivery")
}

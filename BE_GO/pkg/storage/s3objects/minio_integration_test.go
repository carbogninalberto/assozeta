//go:build s3integration

package s3objects

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3credentials"
)

type countedTransport struct{ calls atomic.Int32 }

func (t *countedTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	t.calls.Add(1)
	return http.DefaultTransport.RoundTrip(r)
}
func TestMinIODelete(t *testing.T) {
	var f struct {
		Endpoint, AccessKey, SecretKey, PlainBucket, VersionedBucket, LockBucket, OldVersion, LockedVersion, FreeVersion, RecoverMarker string
		Batches                                                                                                                         [][]string
	}
	raw, e := os.ReadFile(os.Getenv("ASSOZETA_S3_FIXTURE"))
	if e != nil || json.Unmarshal(raw, &f) != nil || len(f.Batches) != 5 {
		t.Fatal("run TESTS/adapters/django/s3_objects.py")
	}
	// Exercise the real credential constructor while excluding home profiles and
	// ambient cloud identity configuration from this disposable MinIO rehearsal.
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(name, "AWS_") {
			t.Setenv(name, "")
			if err := os.Unsetenv(name); err != nil {
				t.Fatal(err)
			}
		}
	}
	profileDirectory := t.TempDir()
	t.Setenv("AWS_CONFIG_FILE", filepath.Join(profileDirectory, "config"))
	t.Setenv("AWS_SHARED_CREDENTIALS_FILE", filepath.Join(profileDirectory, "credentials"))
	t.Setenv("AWS_EC2_METADATA_DISABLED", "true")
	provider, e := s3credentials.New(context.Background(), s3credentials.Options{AccessKey: f.AccessKey, SecretKey: f.SecretKey}, nil)
	if e != nil {
		t.Fatal("credential construction failed", e)
	}
	makeClient := func(bucket string, credentials aws.CredentialsProvider) (*Client, *countedTransport) {
		t.Helper()
		opts := fixtureOptions(f.Endpoint)
		opts.Bucket = bucket
		transport := &countedTransport{}
		client, e := New(opts, credentials, transport)
		if e != nil {
			t.Fatal(e)
		}
		return client, transport
	}
	ctx := context.Background()
	t.Run("bounded batches preserve keys and delete missing objects", func(t *testing.T) {
		c, transport := makeClient(f.PlainBucket, provider)
		for _, keys := range f.Batches {
			input := make([]domain.ObjectVersion, len(keys))
			for i, key := range keys {
				input[i] = domain.ObjectVersion{Key: key}
			}
			before := transport.calls.Load()
			got, e := c.DeleteBatch(ctx, input)
			if e != nil || len(got) != len(input) || transport.calls.Load()-before != 1 {
				t.Fatal("batch request bound", len(keys), e)
			}
			for i, row := range got {
				if row.Object != input[i] || !row.Deleted || row.Code != "deleted" {
					t.Fatal("wrong object result", i, row.Code)
				}
			}
			// Retrying the same immutable unversioned keys remains successful.
			if _, e = c.DeleteBatch(ctx, input); e != nil {
				t.Fatal("replay", e)
			}
		}
	})
	t.Run("specific old version preserves current object", func(t *testing.T) {
		c, transport := makeClient(f.VersionedBucket, provider)
		input := []domain.ObjectVersion{{Key: "versioned.pdf", VersionID: f.OldVersion}}
		for i := 0; i < 2; i++ {
			got, e := c.DeleteBatch(ctx, input)
			if e != nil || len(got) != 1 || !got[0].Deleted || got[0].Object != input[0] {
				t.Fatal("version delete/replay", e)
			}
		}
		if transport.calls.Load() != 2 {
			t.Fatal("version retry requests")
		}
	})
	t.Run("current version creates an explicit delete marker", func(t *testing.T) {
		c, transport := makeClient(f.VersionedBucket, provider)
		input := []domain.ObjectVersion{{Key: "marker.pdf"}}
		got, e := c.DeleteBatch(ctx, input)
		if e != nil || len(got) != 1 || !got[0].Deleted || got[0].DeleteMarker == nil || !*got[0].DeleteMarker || got[0].Object != input[0] || got[0].DeleteMarkerID == "" || transport.calls.Load() != 1 {
			t.Fatal("delete marker result", got, e)
		}
		if e = os.WriteFile(os.Getenv("ASSOZETA_S3_MARKER_RESULT"), []byte(got[0].DeleteMarkerID), 0600); e != nil {
			t.Fatal(e)
		}
	})
	t.Run("object lock returns explicit partial outcomes", func(t *testing.T) {
		c, transport := makeClient(f.LockBucket, provider)
		input := []domain.ObjectVersion{{Key: "free.pdf", VersionID: f.FreeVersion}, {Key: "locked.pdf", VersionID: f.LockedVersion}}
		got, e := c.DeleteBatch(ctx, input)
		// This MinIO version returns InvalidRequest for compliance retention,
		// while AWS returns AccessDenied. Both remain explicit failed objects.
		if e != nil || len(got) != 2 || !got[0].Deleted || got[1].Deleted || got[1].Code != "rejected" || transport.calls.Load() != 1 {
			t.Fatal("partial object lock result", got, e)
		}
	})
	t.Run("removing a specific marker restores previous bytes", func(t *testing.T) {
		c, transport := makeClient(f.VersionedBucket, provider)
		input := []domain.ObjectVersion{{Key: "recover-marker.pdf", VersionID: f.RecoverMarker}}
		got, e := c.DeleteBatch(ctx, input)
		// The independent Boto3 probe establishes that this MinIO omits the
		// marker flag on removal. Preserve nil instead of inventing false.
		if e != nil || len(got) != 1 || !got[0].Deleted || got[0].DeleteMarker != nil || got[0].Object != input[0] || transport.calls.Load() != 1 {
			t.Fatal("marker removal metadata", got, e)
		}
	})
	t.Run("invalid credentials leave objects intact", func(t *testing.T) {
		c, transport := makeClient(f.PlainBucket, fixtureCredentials())
		rows, e := c.DeleteBatch(ctx, []domain.ObjectVersion{{Key: "keep.pdf"}})
		if rows != nil || !errors.Is(e, domain.ErrObjectStoreDenied) || transport.calls.Load() != 1 {
			t.Fatal("credential denial", e)
		}
	})
	t.Run("missing bucket is not successful cleanup", func(t *testing.T) {
		c, transport := makeClient("fixture-bucket-missing", provider)
		rows, e := c.DeleteBatch(ctx, []domain.ObjectVersion{{Key: "keep.pdf"}})
		if rows != nil || !errors.Is(e, domain.ErrObjectStoreMissingBucket) || transport.calls.Load() != 1 {
			t.Fatal("missing bucket", e)
		}
	})
}

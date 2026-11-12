package s3objects

import (
	"context"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func fixtureCredentials() aws.CredentialsProvider {
	return aws.CredentialsProviderFunc(func(context.Context) (aws.Credentials, error) {
		return aws.Credentials{AccessKeyID: "fixture-access", SecretAccessKey: "fixture-secret", SessionToken: "fixture-token", Source: "fixture"}, nil
	})
}
func fixtureOptions(endpoint string) Options {
	return Options{Endpoint: endpoint, Region: "us-east-1", Bucket: "fixture-bucket", PathStyle: true, Timeout: time.Second, MaxConcurrent: 2, MaxResponseBytes: 1 << 20}
}
func TestDeleteBatchWire(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != "POST" || r.URL.Path != "/fixture-bucket" && r.URL.Path != "/fixture-bucket/" || !r.URL.Query().Has("delete") {
			t.Error("wrong delete target", r.Method, r.URL.Path)
		}
		if !strings.HasPrefix(r.Header.Get("Authorization"), "AWS4-HMAC-SHA256 Credential=fixture-access/") || r.Header.Get("X-Amz-Security-Token") != "fixture-token" {
			t.Error("missing signed explicit credentials")
		}
		raw, e := io.ReadAll(r.Body)
		if e != nil {
			t.Error(e)
		}
		var doc struct {
			Objects []struct {
				Key       string
				VersionID string `xml:"VersionId"`
			} `xml:"Object"`
			Quiet bool
		}
		if xml.Unmarshal(raw, &doc) != nil || len(doc.Objects) != 3 || doc.Quiet {
			t.Error("invalid verbose delete request")
		}
		if len(doc.Objects) == 3 && (doc.Objects[0].Key != "a<&日本語" || doc.Objects[1].VersionID != "old-version" || doc.Objects[2].Key != "denied") {
			t.Error("object identity changed")
		}
		w.Header().Set("Content-Type", "application/xml")
		io.WriteString(w, `<DeleteResult><Deleted><Key>versioned</Key><VersionId>old-version</VersionId></Deleted><Error><Key>denied</Key><Code>AccessDenied</Code><Message>private-provider-message</Message></Error><Deleted><Key>a&lt;&amp;日本語</Key></Deleted></DeleteResult>`)
	}))
	defer server.Close()
	client, e := New(fixtureOptions(server.URL), fixtureCredentials(), nil)
	if e != nil {
		t.Fatal(e)
	}
	input := []domain.ObjectVersion{{Key: "a<&日本語"}, {Key: "versioned", VersionID: "old-version"}, {Key: "denied"}}
	got, e := client.DeleteBatch(context.Background(), input)
	if e != nil || len(got) != 3 || calls.Load() != 1 {
		t.Fatal("delete batch", got, e)
	}
	for i := range input {
		if got[i].Object != input[i] {
			t.Fatal("response reordered identities")
		}
	}
	if !got[0].Deleted || !got[1].Deleted || got[2].Deleted || got[2].Code != "access_denied" {
		t.Fatal("wrong per-object outcomes", got)
	}
}

func TestDeleteBatchRejectsInvalidWithoutHTTP(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls.Add(1) }))
	defer server.Close()
	c, e := New(fixtureOptions(server.URL), fixtureCredentials(), nil)
	if e != nil {
		t.Fatal(e)
	}
	for _, input := range [][]domain.ObjectVersion{{{Key: ""}}, {{Key: "a\x00"}}, {{Key: string([]byte{255})}}, {{Key: strings.Repeat("a", 1025)}}, {{Key: "a", VersionID: strings.Repeat("v", 1025)}}, {{Key: "a", VersionID: "v\x00"}}, {{Key: "a"}, {Key: "a"}}, make([]domain.ObjectVersion, 1001)} {
		rows, e := c.DeleteBatch(context.Background(), input)
		if rows != nil || !errors.Is(e, domain.ErrObjectDeleteInput) {
			t.Fatal("invalid input", e)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if rows, e := c.DeleteBatch(ctx, []domain.ObjectVersion{{Key: "a"}}); rows != nil || !errors.Is(e, context.Canceled) {
		t.Fatal("cancellation", e)
	}
	if rows, e := c.DeleteBatch(context.Background(), nil); len(rows) != 0 || e != nil {
		t.Fatal("empty batch", e)
	}
	if calls.Load() != 0 {
		t.Fatal("invalid inputs reached provider")
	}
}

func TestDeleteCredentialFailureIsRedacted(t *testing.T) {
	var calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls.Add(1) }))
	defer s.Close()
	provider := aws.CredentialsProviderFunc(func(context.Context) (aws.Credentials, error) {
		return aws.Credentials{}, errors.New("private-credential-detail")
	})
	c, e := New(fixtureOptions(s.URL), provider, nil)
	if e != nil {
		t.Fatal(e)
	}
	rows, e := c.DeleteBatch(context.Background(), []domain.ObjectVersion{{Key: "a"}})
	if rows != nil || !errors.Is(e, domain.ErrObjectStoreUnavailable) || strings.Contains(e.Error(), "private-credential-detail") || calls.Load() != 0 {
		t.Fatal("credential provider error exposed", e)
	}
}

func TestDeleteDistinctVersionsOfSameKey(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `<DeleteResult><Deleted><Key>a</Key><VersionId>v2</VersionId></Deleted><Deleted><Key>a</Key><VersionId>v1</VersionId></Deleted></DeleteResult>`)
	}))
	defer s.Close()
	c, e := New(fixtureOptions(s.URL), fixtureCredentials(), nil)
	if e != nil {
		t.Fatal(e)
	}
	input := []domain.ObjectVersion{{Key: "a", VersionID: "v1"}, {Key: "a", VersionID: "v2"}}
	rows, e := c.DeleteBatch(context.Background(), input)
	if e != nil || len(rows) != 2 || rows[0].Object != input[0] || rows[1].Object != input[1] || !rows[0].Deleted || !rows[1].Deleted {
		t.Fatal("version identities conflated", rows, e)
	}
}

func TestDeleteMarkerFlagsPreserveAbsence(t *testing.T) {
	for _, tt := range []struct {
		name, fields string
		want         *bool
	}{
		{"absent", "", nil}, {"false", "<DeleteMarker>false</DeleteMarker>", aws.Bool(false)},
		{"true", "<DeleteMarker>true</DeleteMarker><DeleteMarkerVersionId>marker-id</DeleteMarkerVersionId>", aws.Bool(true)},
	} {
		t.Run(tt.name, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				fmt.Fprintf(w, `<DeleteResult><Deleted><Key>a</Key>%s</Deleted></DeleteResult>`, tt.fields)
			}))
			defer s.Close()
			c, e := New(fixtureOptions(s.URL), fixtureCredentials(), nil)
			if e != nil {
				t.Fatal(e)
			}
			rows, e := c.DeleteBatch(context.Background(), []domain.ObjectVersion{{Key: "a"}})
			if e != nil || len(rows) != 1 {
				t.Fatal(e)
			}
			got := rows[0].DeleteMarker
			if (got == nil) != (tt.want == nil) || got != nil && *got != *tt.want {
				t.Fatal("marker flag presence changed")
			}
			if tt.want != nil && *tt.want && rows[0].DeleteMarkerID != "marker-id" {
				t.Fatal("marker identity changed")
			}
		})
	}
}

func TestDeleteBatchResponseFailures(t *testing.T) {
	for _, tt := range []struct {
		name   string
		status int
		body   string
		want   error
	}{
		{"missing result", 200, `<DeleteResult/>`, domain.ErrObjectStoreResponse},
		{"unidentified marker", 200, `<DeleteResult><Deleted><Key>a</Key><DeleteMarker>true</DeleteMarker></Deleted></DeleteResult>`, domain.ErrObjectStoreResponse},
		{"unknown result", 200, `<DeleteResult><Deleted><Key>other</Key></Deleted></DeleteResult>`, domain.ErrObjectStoreResponse},
		{"duplicate result", 200, `<DeleteResult><Deleted><Key>a</Key></Deleted><Deleted><Key>a</Key></Deleted></DeleteResult>`, domain.ErrObjectStoreResponse},
		{"malformed", 200, `<DeleteResult>`, domain.ErrObjectStoreResponse},
		{"oversized", 200, strings.Repeat("x", 4097), domain.ErrObjectStoreResponse},
		{"denied", 403, `<Error><Code>AccessDenied</Code><Message>private-secret</Message></Error>`, domain.ErrObjectStoreDenied},
		{"missing bucket", 404, `<Error><Code>NoSuchBucket</Code></Error>`, domain.ErrObjectStoreMissingBucket},
		{"unknown 404", 404, `<Error><Code>NotFound</Code></Error>`, domain.ErrObjectStoreRejected},
		{"unavailable", 503, `<Error><Code>SlowDown</Code></Error>`, domain.ErrObjectStoreUnavailable},
		{"redirect", 307, `<Error><Code>TemporaryRedirect</Code></Error>`, domain.ErrObjectStoreRejected},
	} {
		t.Run(tt.name, func(t *testing.T) {
			var calls atomic.Int32
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				w.Header().Set("Content-Type", "application/xml")
				w.Header().Set("Location", "http://127.0.0.1:1/private-secret")
				w.WriteHeader(tt.status)
				io.WriteString(w, tt.body)
			}))
			defer s.Close()
			opts := fixtureOptions(s.URL)
			opts.MaxResponseBytes = 4096
			c, e := New(opts, fixtureCredentials(), nil)
			if e != nil {
				t.Fatal(e)
			}
			rows, e := c.DeleteBatch(context.Background(), []domain.ObjectVersion{{Key: "a"}})
			if rows != nil || !errors.Is(e, tt.want) || calls.Load() != 1 {
				t.Fatal("wrong failure/no retry", e, calls.Load())
			}
			if strings.Contains(fmt.Sprint(e), "private-secret") {
				t.Fatal("provider detail leaked")
			}
		})
	}
}

func TestDeleteBatchAdmissionCancellationAndRecovery(t *testing.T) {
	entered := make(chan struct{}, 32)
	release := make(chan struct{})
	var active, peak, calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		calls.Add(1)
		n := active.Add(1)
		defer active.Add(-1)
		for p := peak.Load(); n > p && !peak.CompareAndSwap(p, n); p = peak.Load() {
		}
		entered <- struct{}{}
		select {
		case <-release:
		case <-r.Context().Done():
			return
		}
		io.WriteString(w, `<DeleteResult><Deleted><Key>a</Key></Deleted></DeleteResult>`)
	}))
	defer s.Close()
	opts := fixtureOptions(s.URL)
	opts.Timeout = 5 * time.Second
	c, e := New(opts, fixtureCredentials(), nil)
	if e != nil {
		t.Fatal(e)
	}
	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, e := c.DeleteBatch(context.Background(), []domain.ObjectVersion{{Key: "a"}})
			if e != nil {
				t.Error(e)
			}
		}()
	}
	for i := 0; i < 2; i++ {
		select {
		case <-entered:
		case <-time.After(time.Second):
			t.Fatal("requests not admitted")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { _, e := c.DeleteBatch(ctx, []domain.ObjectVersion{{Key: "a"}}); done <- e }()
	cancel()
	if e := <-done; !errors.Is(e, context.Canceled) {
		t.Fatal("queued cancel", e)
	}
	if calls.Load() != 2 {
		t.Fatal("queued cancellation reached transport")
	}
	queued, expire := context.WithTimeout(context.Background(), 40*time.Millisecond)
	defer expire()
	if rows, e := c.DeleteBatch(queued, []domain.ObjectVersion{{Key: "a"}}); rows != nil || !errors.Is(e, context.DeadlineExceeded) || calls.Load() != 2 {
		t.Fatal("queue did not respect caller deadline", e)
	}
	close(release)
	wg.Wait()
	if peak.Load() != 2 {
		t.Fatal("concurrency not bounded")
	}
	if _, e = c.DeleteBatch(context.Background(), []domain.ObjectVersion{{Key: "a"}}); e != nil {
		t.Fatal("admission slot not released", e)
	}
}

func TestDeleteBatchRunningCancelAndTimeout(t *testing.T) {
	for _, deadline := range []bool{false, true} {
		t.Run(fmt.Sprint(deadline), func(t *testing.T) {
			entered := make(chan struct{}, 1)
			stopped := make(chan struct{}, 1)
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				io.Copy(io.Discard, r.Body)
				entered <- struct{}{}
				<-r.Context().Done()
				stopped <- struct{}{}
			}))
			defer s.Close()
			opts := fixtureOptions(s.URL)
			if deadline {
				opts.Timeout = 60 * time.Millisecond
			}
			c, e := New(opts, fixtureCredentials(), nil)
			if e != nil {
				t.Fatal(e)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			done := make(chan error, 1)
			go func() {
				rows, e := c.DeleteBatch(ctx, []domain.ObjectVersion{{Key: "a"}})
				if rows != nil {
					done <- errors.New("failed request returned rows")
					return
				}
				done <- e
			}()
			select {
			case <-entered:
			case <-time.After(time.Second):
				t.Fatal("request never reached provider")
			}
			want := context.Canceled
			if deadline {
				want = context.DeadlineExceeded
			} else {
				cancel()
			}
			if e := <-done; !errors.Is(e, want) {
				t.Fatal("running request cancellation", e)
			}
			select {
			case <-stopped:
			case <-time.After(time.Second):
				t.Fatal("provider connection not cancelled")
			}
			if len(c.slots) != 0 {
				t.Fatal("admission leaked")
			}
		})
	}
}

func TestDeleteBatchErrorCodesAreBounded(t *testing.T) {
	for _, code := range []string{"NoSuchKey", "NoSuchVersion", "AccessDenied", "SlowDown", "InternalError", "RequestTimeout", "InvalidRequest", "private-provider-secret"} {
		t.Run(code, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				fmt.Fprintf(w, `<DeleteResult><Error><Key>a</Key><Code>%s</Code><Message>private-message</Message></Error></DeleteResult>`, code)
			}))
			defer s.Close()
			c, e := New(fixtureOptions(s.URL), fixtureCredentials(), nil)
			if e != nil {
				t.Fatal(e)
			}
			rows, e := c.DeleteBatch(context.Background(), []domain.ObjectVersion{{Key: "a"}})
			want := "rejected"
			switch code {
			case "NoSuchKey", "NoSuchVersion":
				want = "deleted"
			case "AccessDenied":
				want = "access_denied"
			case "SlowDown", "InternalError", "RequestTimeout":
				want = "unavailable"
			}
			if e != nil || len(rows) != 1 || rows[0].Code != want || rows[0].Deleted != (want == "deleted") {
				t.Fatal("provider classification", rows, e)
			}
		})
	}
}

func TestDeleteClientConfiguration(t *testing.T) {
	for _, mutate := range []func(*Options){
		func(o *Options) { o.Endpoint = "https://secret@example.test" }, func(o *Options) { o.Endpoint = "http://host/?secret=1" }, func(o *Options) { o.Endpoint = "ftp://host" }, func(o *Options) { o.Endpoint = "" },
		func(o *Options) { o.Region = "" }, func(o *Options) { o.Bucket = "" }, func(o *Options) { o.Timeout = 0 }, func(o *Options) { o.MaxConcurrent = 0 }, func(o *Options) { o.MaxConcurrent = 33 }, func(o *Options) { o.MaxResponseBytes = 0 }, func(o *Options) { o.MaxResponseBytes = 16<<20 + 1 },
	} {
		opts := fixtureOptions("https://example.test")
		mutate(&opts)
		if client, e := New(opts, fixtureCredentials(), nil); client != nil || !errors.Is(e, domain.ErrObjectStoreConfiguration) {
			t.Fatal("invalid constructor", e)
		}
	}
	if client, e := New(fixtureOptions("https://example.test"), nil, nil); client != nil || !errors.Is(e, domain.ErrObjectStoreConfiguration) {
		t.Fatal("missing credentials", e)
	}
}

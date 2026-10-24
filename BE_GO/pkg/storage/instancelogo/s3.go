package instancelogo

import (
	"context"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/aws/ratelimit"
	"github.com/aws/aws-sdk-go-v2/aws/retry"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/smithy-go/logging"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3trust"
)

type S3Options struct {
	IOTimeout                          time.Duration
	Endpoint, Region, Bucket, Location string
	Credentials                        func(context.Context) (aws.CredentialsProvider, error)
	Trust                              func(context.Context) (s3trust.Policy, error)
	Transport                          http.RoundTripper
	TempDir                            string
}

func (S3Options) String() string     { return "instance logo S3 options (redacted)" }
func (o S3Options) GoString() string { return o.String() }

type S3 struct {
	options        S3Options
	admission      admission
	initialization chan struct{}
	api            *s3.Client
}

// NewS3 does not inspect credential files or initialize a provider. Logo storage
// is lazy in Django; a broken backend must not break unrelated server startup.
func NewS3(options S3Options) *S3 {
	return &S3{options: options, admission: newAdmission(4), initialization: make(chan struct{}, 1)}
}
func (s *S3) client(ctx context.Context) (*s3.Client, error) {
	select {
	case s.initialization <- struct{}{}:
		defer func() { <-s.initialization }()
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if s.api != nil {
		return s.api, nil
	}
	endpoint, err := url.Parse(s.options.Endpoint)
	if err != nil || endpoint.Hostname() == "" || (endpoint.Scheme != "http" && endpoint.Scheme != "https") || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" || s.options.Bucket == "" || s.options.Credentials == nil || strings.HasPrefix(s.options.Location, "/") {
		return nil, ErrStorage
	}
	credentials, err := s.options.Credentials(ctx)
	if err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	transport := s.options.Transport
	if transport == nil {
		timeout := s.options.IOTimeout
		if timeout == 0 {
			timeout = 60 * time.Second
		}
		if timeout < 0 {
			return nil, ErrStorage
		}
		standard, ok := http.DefaultTransport.(*http.Transport)
		if !ok {
			return nil, ErrStorage
		}
		copy := standard.Clone()
		copy.DisableCompression = true
		copy.MaxConnsPerHost = 4
		copy.MaxIdleConnsPerHost = 4
		copy.ResponseHeaderTimeout = timeout
		copy.TLSHandshakeTimeout = timeout
		// Botocore uses HTTP/1.1. Per-connection idle deadlines must not be
		// shared across unrelated HTTP/2 streams.
		copy.ForceAttemptHTTP2 = false
		copy.TLSNextProto = map[string]func(string, *tls.Conn) http.RoundTripper{}
		dialer := &net.Dialer{Timeout: timeout, KeepAlive: 30 * time.Second}
		copy.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
			connection, err := dialer.DialContext(ctx, network, address)
			if err != nil {
				return nil, err
			}
			return &idleConnection{Conn: connection, timeout: timeout}, nil
		}
		transport = copy
		if s.options.Trust != nil {
			trust, err := s.options.Trust(ctx)
			if err != nil {
				return nil, err
			}
			transport = trust.Wrap(copy)
		}
	}
	client := &http.Client{Transport: rawReadTransport{transport}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	s.api = s3.New(s3.Options{Region: s.options.Region, BaseEndpoint: aws.String(s.options.Endpoint), Credentials: credentials, HTTPClient: client,
		UsePathStyle: true, DisableS3ExpressSessionAuth: aws.Bool(true), Retryer: retry.NewStandard(func(o *retry.StandardOptions) { o.MaxAttempts = 5; o.RateLimiter = ratelimit.None }), Logger: logging.Nop{}})
	return s.api, nil
}
func (s *S3) key(name string) (string, error) {
	base := strings.TrimRight(s.options.Location, "/")
	key := path.Clean(base + "/" + name)
	if strings.HasPrefix(s.options.Location, "/") || strings.ContainsRune(key, 0) {
		return "", ErrStorage
	}
	if !strings.HasPrefix(key, base+"/") {
		return "", domain.ErrInstanceLogoPath
	}
	return strings.TrimLeft(key, "/"), nil
}
func (s *S3) Exists(ctx context.Context, name string) (bool, error) {
	release, err := s.admission.acquire(ctx)
	if err != nil {
		return false, err
	}
	defer release()
	key, err := s.key(name)
	if err != nil {
		return false, err
	}
	client, err := s.client(ctx)
	if err != nil {
		return false, err
	}
	_, err = client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key)})
	if err == nil {
		return true, nil
	}
	if ctx.Err() != nil {
		return false, ctx.Err()
	}
	var response *smithyhttp.ResponseError
	if errors.As(err, &response) && response.HTTPStatusCode() == 404 {
		return false, nil
	}
	return false, err
}
func (s *S3) Open(ctx context.Context, name string) (domain.InstanceLogoFile, error) {
	release, err := s.admission.acquire(ctx)
	if err != nil {
		return domain.InstanceLogoFile{}, err
	}
	transferred := false
	defer func() {
		if !transferred {
			release()
		}
	}()
	key, err := s.key(name)
	if err != nil {
		return domain.InstanceLogoFile{}, err
	}
	client, err := s.client(ctx)
	if err != nil {
		return domain.InstanceLogoFile{}, err
	}
	// Django's S3File construction loads object metadata before its seekable file
	// is prepared. Nothing is streamed to the HTTP client until preparation ends.
	if _, err = client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key)}); err != nil {
		return domain.InstanceLogoFile{}, err
	}
	file, err := os.CreateTemp(s.options.TempDir, "assozeta-instance-logo-")
	if err != nil {
		return domain.InstanceLogoFile{}, err
	}
	// Unlink the empty 0600 file before fetching any remote payload. Only the
	// open descriptor owns its data, so SIGKILL cannot leave private named files.
	// If unlink fails, no provider body is requested or consumed.
	if err = os.Remove(file.Name()); err != nil {
		file.Close()
		os.Remove(file.Name())
		return domain.InstanceLogoFile{}, err
	}
	defer func() {
		if !transferred {
			file.Close()
		}
	}()
	size, err := s.download(ctx, client, key, file)
	if err != nil {
		return domain.InstanceLogoFile{}, err
	}
	if err = ctx.Err(); err != nil {
		return domain.InstanceLogoFile{}, err
	}
	if _, err = file.Seek(0, io.SeekStart); err != nil {
		return domain.InstanceLogoFile{}, err
	}
	transferred = true
	return domain.InstanceLogoFile{Body: ownFile(ctx, file, "", release), Size: size}, nil
}

// Botocore retries transient operation errors; s3transfer separately retries
// interrupted successful-response bodies up to five download attempts. A failed
// local spool write is not a retriable transport error.
func (s *S3) download(ctx context.Context, client *s3.Client, key string, file *os.File) (int64, error) {
	for attempt := 0; attempt < 5; attempt++ {
		response, err := client.GetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key)})
		if err != nil {
			return 0, err
		}
		size, err := io.CopyBuffer(file, response.Body, make([]byte, 64<<10))
		response.Body.Close()
		if ctx.Err() != nil {
			return 0, ctx.Err()
		}
		if err == nil {
			return size, nil
		}
		var local *os.PathError
		if errors.As(err, &local) || attempt == 4 {
			return 0, err
		}
		if err = file.Truncate(0); err != nil {
			return 0, err
		}
		if _, err = file.Seek(0, io.SeekStart); err != nil {
			return 0, err
		}
	}
	return 0, ErrStorage
}

// Each progressing read/write refreshes the socket deadline, matching the
// storage SDK's idle-I/O bound without a whole-object duration or size cap.
type idleConnection struct {
	net.Conn
	timeout time.Duration
}

func (c *idleConnection) Read(p []byte) (int, error) {
	if err := c.Conn.SetReadDeadline(time.Now().Add(c.timeout)); err != nil {
		return 0, err
	}
	return c.Conn.Read(p)
}
func (c *idleConnection) Write(p []byte) (int, error) {
	if err := c.Conn.SetWriteDeadline(time.Now().Add(c.timeout)); err != nil {
		return 0, err
	}
	return c.Conn.Write(p)
}

type rawReadTransport struct{ next http.RoundTripper }

func (t rawReadTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	copy := request.Clone(request.Context())
	copy.Header = request.Header.Clone()
	copy.Header.Set("Accept-Encoding", "identity")
	response, err := t.next.RoundTrip(copy)
	if err == nil && response != nil && response.Body != nil && response.StatusCode >= 300 {
		response.Body = http.MaxBytesReader(nil, response.Body, 64<<10)
	}
	return response, err
}

var _ domain.InstanceLogoStorage = (*S3)(nil)

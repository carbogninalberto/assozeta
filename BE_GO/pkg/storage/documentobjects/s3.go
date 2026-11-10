// Package documentobjects reads stored documents from the S3 default storage
// the way PrintingService does: default_storage.exists (HeadObject, 404 means
// absent) and a direct GetObject stream, and stores uploads the way
// S3Storage._save does (one PutObject). Delete is S3Storage.delete (the export
// cleanup task, the signature upload, the data-restore cleanup); PutACL
// (acl.go) is the signature's object ACL; List is one S3Storage.listdir listing.
package documentobjects

import (
	"bytes"
	"context"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/aws/ratelimit"
	"github.com/aws/aws-sdk-go-v2/aws/retry"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/aws/smithy-go/logging"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3trust"
)

var ErrStorage = errors.New("document storage is not configured")

type Options struct {
	IOTimeout                          time.Duration
	Endpoint, Region, Bucket, Location string
	Credentials                        func(context.Context) (aws.CredentialsProvider, error)
	Trust                              func(context.Context) (s3trust.Policy, error)
}

func (Options) String() string     { return "document object storage options (redacted)" }
func (o Options) GoString() string { return o.String() }

// Object is one GetObject response: the body is streamed unchanged.
type Object struct {
	Body            io.ReadCloser
	ContentLength   *int64
	ContentType     *string
	ContentEncoding *string
}

type S3 struct {
	options Options
	mu      sync.Mutex
	api     *s3.Client
}

// New does not contact the provider or load credentials: like Django's lazy
// default_storage, a broken backend only fails the requests that use it.
func New(options Options) *S3 { return &S3{options: options} }

// Location is AWS_LOCATION, the prefix of every key.
func (s *S3) Location() string { return s.options.Location }

func (s *S3) client(ctx context.Context) (*s3.Client, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.api != nil {
		return s.api, nil
	}
	endpoint, err := url.Parse(s.options.Endpoint)
	if err != nil || endpoint.Hostname() == "" || (endpoint.Scheme != "http" && endpoint.Scheme != "https") || endpoint.User != nil ||
		endpoint.RawQuery != "" || endpoint.Fragment != "" || s.options.Bucket == "" || s.options.Credentials == nil ||
		strings.HasPrefix(s.options.Location, "/") {
		return nil, ErrStorage
	}
	credentials, err := s.options.Credentials(ctx)
	if err != nil {
		return nil, err
	}
	timeout := s.options.IOTimeout
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	standard, ok := http.DefaultTransport.(*http.Transport)
	if !ok {
		return nil, ErrStorage
	}
	transport := standard.Clone()
	transport.DisableCompression = true
	transport.ResponseHeaderTimeout = timeout
	transport.TLSHandshakeTimeout = timeout
	transport.ForceAttemptHTTP2 = false
	transport.TLSNextProto = map[string]func(string, *tls.Conn) http.RoundTripper{}
	transport.DialContext = (&net.Dialer{Timeout: timeout, KeepAlive: 30 * time.Second}).DialContext
	var roundTripper http.RoundTripper = transport
	if s.options.Trust != nil {
		trust, err := s.options.Trust(ctx)
		if err != nil {
			return nil, err
		}
		roundTripper = trust.Wrap(transport)
	}
	client := &http.Client{Transport: identityTransport{roundTripper}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	s.api = s3.New(s3.Options{Region: s.options.Region, BaseEndpoint: aws.String(s.options.Endpoint), Credentials: credentials, HTTPClient: client,
		UsePathStyle: true, DisableS3ExpressSessionAuth: aws.Bool(true),
		Retryer: retry.NewStandard(func(o *retry.StandardOptions) { o.MaxAttempts = 5; o.RateLimiter = ratelimit.None }), Logger: logging.Nop{}})
	return s.api, nil
}

// Exists is S3Storage.exists: HeadObject, false only for an HTTP 404.
func (s *S3) Exists(ctx context.Context, key string) (bool, error) {
	client, err := s.client(ctx)
	if err != nil {
		return false, err
	}
	_, err = client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key)})
	if err == nil {
		return true, nil
	}
	var response *smithyhttp.ResponseError
	if ctx.Err() == nil && errors.As(err, &response) && response.HTTPStatusCode() == 404 {
		return false, nil
	}
	if ctx.Err() == nil && errors.As(err, &response) {
		if reason, known := headReasons[response.HTTPStatusCode()]; known {
			return false, &HeadStatusError{Status: response.HTTPStatusCode(), Reason: reason}
		}
	}
	return false, err
}

// HeadStatusError is a HeadObject answer botocore raises without retrying: a
// client-error status other than 404 (a HEAD answer has no body, so the code
// is the status and the message its reason phrase). Error() is the text of
// botocore's ClientError.
type HeadStatusError struct {
	Status int
	Reason string
}

func (e *HeadStatusError) Error() string {
	return "An error occurred (" + strconv.Itoa(e.Status) + ") when calling the HeadObject operation: " + e.Reason
}

// headReasons holds http.client.responses for the statuses HeadStatusError covers.
var headReasons = map[int]string{400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 405: "Method Not Allowed",
	409: "Conflict", 410: "Gone", 411: "Length Required", 412: "Precondition Failed"}

// Size is S3Storage.size(name): the object's ContentLength (HeadObject).
func (s *S3) Size(ctx context.Context, key string) (int64, error) {
	client, err := s.client(ctx)
	if err != nil {
		return 0, err
	}
	output, err := client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key)})
	if err != nil {
		return 0, err
	}
	if output.ContentLength == nil {
		return 0, errors.New("object without a content length")
	}
	return *output.ContentLength, nil
}

// Open is the GetObject of PrintingService._stream_file_response.
// NoSuchKeyError is GetObject's NoSuchKey answer. Error() is the text of
// botocore's ClientError for it.
type NoSuchKeyError struct{ Message string }

func (e *NoSuchKeyError) Error() string {
	return "An error occurred (NoSuchKey) when calling the GetObject operation: " + e.Message
}

func (s *S3) Open(ctx context.Context, key string) (*Object, error) {
	client, err := s.client(ctx)
	if err != nil {
		return nil, err
	}
	output, err := client.GetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key)})
	var missing *types.NoSuchKey
	if ctx.Err() == nil && errors.As(err, &missing) {
		return nil, &NoSuchKeyError{Message: missing.ErrorMessage()}
	}
	if err != nil {
		return nil, err
	}
	return &Object{Body: output.Body, ContentLength: output.ContentLength, ContentType: output.ContentType, ContentEncoding: output.ContentEncoding}, nil
}

// Upload is the ExtraArgs S3Storage._get_write_parameters passes to
// upload_fileobj: ContentType and ContentEncoding from mimetypes.guess_type,
// AWS_S3_OBJECT_PARAMETERS' CacheControl and AWS_DEFAULT_ACL ("" for unset).
type Upload struct {
	ContentType, ContentEncoding, CacheControl, ACL string
}

// Put stores body under key in one PutObject (uploads below boto3's 8 MiB
// multipart threshold; larger bodies are stored the same way, in one request).
func (s *S3) Put(ctx context.Context, key string, body []byte, upload Upload) error {
	client, err := s.client(ctx)
	if err != nil {
		return err
	}
	input := &s3.PutObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key), Body: bytes.NewReader(body),
		ContentLength: aws.Int64(int64(len(body))), ContentType: aws.String(upload.ContentType)}
	if upload.ContentEncoding != "" {
		input.ContentEncoding = aws.String(upload.ContentEncoding)
	}
	if upload.CacheControl != "" {
		input.CacheControl = aws.String(upload.CacheControl)
	}
	if upload.ACL != "" {
		input.ACL = types.ObjectCannedACL(upload.ACL)
	}
	handoff.MarkEffect(ctx, "object storage write")
	_, err = client.PutObject(ctx, input)
	return err
}

// Delete is S3Storage.delete: one DeleteObject; an HTTP 404 is not an error.
func (s *S3) Delete(ctx context.Context, key string) error {
	client, err := s.client(ctx)
	if err != nil {
		return err
	}
	handoff.MarkEffect(ctx, "object storage write")
	_, err = client.DeleteObject(ctx, &s3.DeleteObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key)})
	var response *smithyhttp.ResponseError
	if err != nil && ctx.Err() == nil && errors.As(err, &response) && response.HTTPStatusCode() == 404 {
		return nil
	}
	return err
}

// List is one listing of S3Storage.listdir: the list_objects paginator
// (Marker = NextMarker, or the last key) with Delimiter, returning the
// CommonPrefixes and the object keys under prefix, in the provider's order.
func (s *S3) List(ctx context.Context, prefix, delimiter string) ([]string, []string, error) {
	client, err := s.client(ctx)
	if err != nil {
		return nil, nil, err
	}
	var prefixes, keys []string
	var marker *string
	for {
		output, err := client.ListObjects(ctx, &s3.ListObjectsInput{Bucket: aws.String(s.options.Bucket), Prefix: aws.String(prefix),
			Delimiter: aws.String(delimiter), Marker: marker})
		if err != nil {
			return nil, nil, err
		}
		for _, common := range output.CommonPrefixes {
			prefixes = append(prefixes, aws.ToString(common.Prefix))
		}
		for _, object := range output.Contents {
			keys = append(keys, aws.ToString(object.Key))
		}
		if !aws.ToBool(output.IsTruncated) {
			return prefixes, keys, nil
		}
		next := output.NextMarker
		if next == nil && len(output.Contents) > 0 {
			next = output.Contents[len(output.Contents)-1].Key
		}
		if next == nil {
			return prefixes, keys, nil
		}
		marker = next
	}
}

// identityTransport keeps stored bytes unchanged (botocore never asks for a
// transfer encoding) and bounds error bodies.
type identityTransport struct{ next http.RoundTripper }

func (t identityTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	copy := request.Clone(request.Context())
	copy.Header = request.Header.Clone()
	copy.Header.Set("Accept-Encoding", "identity")
	response, err := t.next.RoundTrip(copy)
	if err == nil && response != nil && response.Body != nil && response.StatusCode >= 300 {
		response.Body = http.MaxBytesReader(nil, response.Body, 64<<10)
	}
	return response, err
}

// TotalBytes sums the size of every object in the bucket, whatever the
// location (instance/backups.py object_bytes: list_objects_v2 pages).
func (s *S3) TotalBytes(ctx context.Context) (int64, error) {
	client, err := s.client(ctx)
	if err != nil {
		return 0, err
	}
	var total int64
	pages := s3.NewListObjectsV2Paginator(client, &s3.ListObjectsV2Input{Bucket: aws.String(s.options.Bucket)})
	for pages.HasMorePages() {
		page, err := pages.NextPage(ctx)
		if err != nil {
			return 0, err
		}
		for _, object := range page.Contents {
			total += aws.ToInt64(object.Size)
		}
	}
	return total, nil
}

// Package s3objects supplies bounded S3 operations behind domain interfaces.
package s3objects

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/url"
	"time"
	"unicode/utf8"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/aws/smithy-go"
	"github.com/aws/smithy-go/logging"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type Options struct {
	Endpoint, Region, Bucket string
	PathStyle                bool
	Timeout                  time.Duration
	MaxConcurrent            int
	MaxResponseBytes         int64
}
type Client struct {
	api     *s3.Client
	bucket  string
	timeout time.Duration
	slots   chan struct{}
}

// New accepts explicit credentials and transport; it never loads an environment,
// shared profile or instance metadata. A composition root owns that decision.
func New(o Options, credentials aws.CredentialsProvider, transport http.RoundTripper) (*Client, error) {
	endpoint, err := url.Parse(o.Endpoint)
	if err != nil || endpoint.Hostname() == "" || (endpoint.Scheme != "https" && endpoint.Scheme != "http") || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" ||
		o.Region == "" || o.Bucket == "" || credentials == nil || o.Timeout <= 0 || o.MaxConcurrent < 1 || o.MaxConcurrent > 32 || o.MaxResponseBytes < 1 || o.MaxResponseBytes > 16<<20 {
		return nil, domain.ErrObjectStoreConfiguration
	}
	if transport == nil {
		transport = http.DefaultTransport
		if standard, ok := transport.(*http.Transport); ok {
			t := standard.Clone()
			t.MaxConnsPerHost = o.MaxConcurrent
			t.MaxIdleConnsPerHost = o.MaxConcurrent
			transport = t
		}
	}
	client := &http.Client{Transport: boundedTransport{transport, o.MaxResponseBytes}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	api := s3.New(s3.Options{Region: o.Region, BaseEndpoint: aws.String(o.Endpoint), Credentials: credentials, HTTPClient: client, UsePathStyle: o.PathStyle,
		// No implicit CreateSession requests; credential lifecycle belongs to the
		// explicitly supplied provider. Directory-bucket support is not certified.
		DisableS3ExpressSessionAuth: aws.Bool(true), Retryer: aws.NopRetryer{}, Logger: logging.Nop{}})
	return &Client{api: api, bucket: o.Bucket, timeout: o.Timeout, slots: make(chan struct{}, o.MaxConcurrent)}, nil
}

func (c *Client) DeleteBatch(parent context.Context, objects []domain.ObjectVersion) ([]domain.ObjectDeleteResult, error) {
	if err := parent.Err(); err != nil {
		return nil, err
	}
	if len(objects) > 1000 {
		return nil, domain.ErrObjectDeleteInput
	}
	if len(objects) == 0 {
		return []domain.ObjectDeleteResult{}, nil
	}
	indexes := make(map[domain.ObjectVersion]int, len(objects))
	input := make([]types.ObjectIdentifier, len(objects))
	for i, obj := range objects {
		if obj.Key == "" || len(obj.Key) > 1024 || len(obj.VersionID) > 1024 || !validXML(obj.Key) || !validXML(obj.VersionID) {
			return nil, domain.ErrObjectDeleteInput
		}
		if _, exists := indexes[obj]; exists {
			return nil, domain.ErrObjectDeleteInput
		}
		indexes[obj] = i
		input[i] = types.ObjectIdentifier{Key: aws.String(obj.Key)}
		if obj.VersionID != "" {
			input[i].VersionId = aws.String(obj.VersionID)
		}
	}
	ctx, cancel := context.WithTimeout(parent, c.timeout)
	defer cancel()
	select {
	case c.slots <- struct{}{}:
		defer func() { <-c.slots }()
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	out, err := c.api.DeleteObjects(ctx, &s3.DeleteObjectsInput{Bucket: aws.String(c.bucket), Delete: &types.Delete{Objects: input, Quiet: aws.Bool(false)}})
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if errors.Is(err, errResponseLimit) {
			return nil, domain.ErrObjectStoreResponse
		}
		var response *smithyhttp.ResponseError
		if errors.As(err, &response) {
			switch status := response.HTTPStatusCode(); {
			case status == 401 || status == 403:
				return nil, domain.ErrObjectStoreDenied
			case status == 404:
				var apiError smithy.APIError
				if errors.As(err, &apiError) && apiError.ErrorCode() == "NoSuchBucket" {
					return nil, domain.ErrObjectStoreMissingBucket
				}
				return nil, domain.ErrObjectStoreRejected
			case status == 429 || status >= 500:
				return nil, domain.ErrObjectStoreUnavailable
			case status >= 300:
				return nil, domain.ErrObjectStoreRejected
			default:
				return nil, domain.ErrObjectStoreResponse
			}
		}
		return nil, domain.ErrObjectStoreUnavailable
	}
	if len(out.Deleted)+len(out.Errors) != len(objects) {
		return nil, domain.ErrObjectStoreResponse
	}
	results := make([]domain.ObjectDeleteResult, len(objects))
	seen := make([]bool, len(objects))
	set := func(obj domain.ObjectVersion, deleted bool, code string, isMarker *bool, marker string) bool {
		index, ok := indexes[obj]
		if !ok || seen[index] {
			return false
		}
		seen[index] = true
		results[index] = domain.ObjectDeleteResult{Object: obj, Deleted: deleted, Code: code, DeleteMarker: isMarker, DeleteMarkerID: marker}
		return true
	}
	for _, deleted := range out.Deleted {
		obj := domain.ObjectVersion{Key: aws.ToString(deleted.Key), VersionID: aws.ToString(deleted.VersionId)}
		isMarker, marker := deleted.DeleteMarker, aws.ToString(deleted.DeleteMarkerVersionId)
		if aws.ToBool(isMarker) && obj.VersionID == "" && marker == "" {
			return nil, domain.ErrObjectStoreResponse
		}
		if !set(obj, true, "deleted", isMarker, marker) {
			return nil, domain.ErrObjectStoreResponse
		}
	}
	for _, failure := range out.Errors {
		code := "rejected"
		deleted := false
		switch aws.ToString(failure.Code) {
		case "AccessDenied":
			code = "access_denied"
		case "InternalError", "ServiceUnavailable", "SlowDown", "RequestTimeout":
			code = "unavailable"
		case "NoSuchKey", "NoSuchVersion":
			code = "deleted"
			deleted = true
		}
		if !set(domain.ObjectVersion{Key: aws.ToString(failure.Key), VersionID: aws.ToString(failure.VersionId)}, deleted, code, nil, "") {
			return nil, domain.ErrObjectStoreResponse
		}
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return results, nil
}

func validXML(s string) bool {
	if !utf8.ValidString(s) {
		return false
	}
	for _, r := range s {
		if r != 9 && r != 10 && r != 13 && (r < 32 || r == 0xfffe || r == 0xffff) {
			return false
		}
	}
	return true
}

var errResponseLimit = errors.New("object store response exceeds limit")

type boundedTransport struct {
	transport http.RoundTripper
	limit     int64
}

func (t boundedTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	response, err := t.transport.RoundTrip(r)
	if err == nil && response != nil && response.Body != nil {
		response.Body = &boundedBody{ReadCloser: response.Body, remaining: t.limit}
	}
	return response, err
}

type boundedBody struct {
	io.ReadCloser
	remaining int64
}

func (b *boundedBody) Read(p []byte) (int, error) {
	if b.remaining < 0 {
		return 0, errResponseLimit
	}
	if int64(len(p)) > b.remaining+1 {
		p = p[:b.remaining+1]
	}
	n, err := b.ReadCloser.Read(p)
	if int64(n) > b.remaining {
		b.remaining = -1
		return 0, errResponseLimit
	}
	b.remaining -= int64(n)
	return n, err
}

var _ domain.ObjectDeleter = (*Client)(nil)

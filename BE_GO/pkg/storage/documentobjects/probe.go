package documentobjects

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"net/http"
	"net/url"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/aws/smithy-go/logging"
)

// ProbeOptions are the settings the storage diagnostic passes to boto3
// directly (AWS_S3_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
// AWS_S3_REGION_NAME, AWS_STORAGE_BUCKET_NAME, AWS_LOCATION).
type ProbeOptions struct {
	Endpoint, AccessKey, SecretKey, Region, Bucket, Location string
}

func (ProbeOptions) String() string     { return "storage probe options (redacted)" }
func (o ProbeOptions) GoString() string { return o.String() }

// probeConn applies botocore's socket timeouts: each read and write has its
// own deadline.
type probeConn struct {
	net.Conn
	timeout time.Duration
}

func (c *probeConn) Read(b []byte) (int, error) {
	_ = c.Conn.SetReadDeadline(time.Now().Add(c.timeout))
	return c.Conn.Read(b)
}

func (c *probeConn) Write(b []byte) (int, error) {
	_ = c.Conn.SetWriteDeadline(time.Now().Add(c.timeout))
	return c.Conn.Write(b)
}

// ListProbe is diagnostic_probe's storage check:
// boto3.client('s3', endpoint_url=..., aws_access_key_id=..., aws_secret_access_key=...,
// region_name=..., config=Config(connect_timeout=3, read_timeout=3, retries={'max_attempts': 0}))
// .list_objects_v2(Bucket=bucket, Prefix=location, MaxKeys=1): one signed,
// path-style GET (botocore uses path style for a custom endpoint and adds
// encoding-type=url), no retry, the process proxy environment. Any error
// (an endpoint botocore rejects, a connection failure, a non-2xx answer or an
// unreadable listing) fails the check.
func ListProbe(ctx context.Context, options ProbeOptions) error {
	endpoint, err := url.Parse(options.Endpoint)
	if err != nil || endpoint.Hostname() == "" || (endpoint.Scheme != "http" && endpoint.Scheme != "https") {
		return ErrStorage
	}
	dialer := &net.Dialer{Timeout: 3 * time.Second}
	transport := &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			conn, err := dialer.DialContext(ctx, network, address)
			if err != nil {
				return nil, err
			}
			return &probeConn{Conn: conn, timeout: 3 * time.Second}, nil
		},
		TLSClientConfig:     &tls.Config{MinVersion: tls.VersionTLS12},
		TLSHandshakeTimeout: 6 * time.Second,
		TLSNextProto:        map[string]func(string, *tls.Conn) http.RoundTripper{},
		DisableCompression:  true,
	}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: identityTransport{transport}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	credentials := aws.CredentialsProviderFunc(func(context.Context) (aws.Credentials, error) {
		return aws.Credentials{AccessKeyID: options.AccessKey, SecretAccessKey: options.SecretKey, Source: "settings"}, nil
	})
	api := s3.New(s3.Options{Region: options.Region, BaseEndpoint: aws.String(options.Endpoint), Credentials: credentials, HTTPClient: client,
		UsePathStyle: true, DisableS3ExpressSessionAuth: aws.Bool(true), RetryMaxAttempts: 1, Logger: logging.Nop{}})
	if options.Bucket == "" {
		return errors.New("documentobjects: no bucket")
	}
	_, err = api.ListObjectsV2(ctx, &s3.ListObjectsV2Input{Bucket: aws.String(options.Bucket), Prefix: aws.String(options.Location),
		MaxKeys: aws.Int32(1), EncodingType: types.EncodingTypeUrl})
	return err
}

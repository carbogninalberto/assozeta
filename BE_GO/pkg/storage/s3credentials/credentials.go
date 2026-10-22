// Package s3credentials constructs storage credentials at an explicit startup
// boundary. It preserves django-storages selection over the AWS SDK providers.
package s3credentials

import (
	"context"
	"errors"
	"net/http"
	"os"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	sdkconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/credentials/endpointcreds"
	"github.com/aws/aws-sdk-go-v2/credentials/processcreds"
	"github.com/aws/smithy-go/logging"
	"github.com/aws/smithy-go/middleware"
)

var (
	ErrConfiguration = errors.New("invalid storage credential configuration")
	ErrCredentials   = errors.New("storage credentials unavailable")
)

type Options struct {
	AccessKey    string           `json:"-"`
	SecretKey    string           `json:"-"`
	SessionToken string           `json:"-"`
	Profile      string           `json:"-"`
	MFAToken     MFATokenProvider `json:"-"`
}

// MFATokenProvider supplies a code for a trusted configured MFA device. The
// callback must honor ctx, bound its own work and never log the code. New never
// reads console input. Each role invokes this callback at most once per provider;
// expiration or a failed attempt requires constructing a new provider, as in Boto3.
type MFATokenProvider func(ctx context.Context, serial string) (string, error)

func (o Options) String() string   { return "storage credential options (redacted)" }
func (o Options) GoString() string { return o.String() }

type HTTPClient interface {
	Do(*http.Request) (*http.Response, error)
}

type boundedHTTP struct{ inner HTTPClient }

func (c boundedHTTP) Do(r *http.Request) (*http.Response, error) {
	response, err := c.inner.Do(r)
	if err == nil && response != nil && response.Body != nil {
		response.Body = http.MaxBytesReader(nil, response.Body, 1<<20)
	}
	return response, err
}

// New reads the existing AWS process environment and shared files without
// mutating either. A caller loading .env separately must supply that environment
// at process startup before constructing the provider. Options carry the parsed
// django-storages keys/token/profile, whose precedence differs from SDK defaults.
// Shared-file commands/role endpoints are trusted deployment configuration.
func New(ctx context.Context, o Options, client HTTPClient) (aws.CredentialsProvider, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if o.Profile != "" && (o.AccessKey != "" || o.SecretKey != "") {
		return nil, ErrConfiguration
	}
	if o.Profile == "" && (o.AccessKey != "" || o.SecretKey != "" || o.SessionToken != "") && (o.AccessKey == "" || o.SecretKey == "") {
		return nil, ErrConfiguration
	}
	configFile, err := sharedFile("AWS_CONFIG_FILE", "config")
	if err != nil {
		return nil, err
	}
	credentialsFile, err := sharedFile("AWS_SHARED_CREDENTIALS_FILE", "credentials")
	if err != nil {
		return nil, err
	}
	profiles, aliases, err := readRoleProfiles(configFile, credentialsFile)
	if err != nil {
		return nil, err
	}
	if client == nil {
		transport := http.DefaultTransport
		if t, ok := transport.(*http.Transport); ok {
			copy := t.Clone()
			copy.MaxConnsPerHost = 4
			copy.MaxIdleConnsPerHost = 4
			transport = copy
		}
		client = &http.Client{Transport: transport, Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	}
	client = boundedHTTP{client}
	options := []func(*sdkconfig.LoadOptions) error{
		sdkconfig.WithAPIOptions([]func(*middleware.Stack) error{preserveMetadataPath}),
		sdkconfig.WithSharedConfigFiles([]string{configFile}), sdkconfig.WithSharedCredentialsFiles([]string{credentialsFile}),
		sdkconfig.WithHTTPClient(client), sdkconfig.WithLogger(logging.Nop{}),
		// Preserve actual expiry in returned credentials; SDK endpoint defaults
		// subtract five minutes, making valid short-lived credentials look expired.
		sdkconfig.WithCredentialsCacheOptions(func(o *aws.CredentialsCacheOptions) { o.ExpiryWindow = 0 }),
		sdkconfig.WithRetryer(func() aws.Retryer { return aws.NopRetryer{} }),
		sdkconfig.WithEndpointCredentialOptions(func(p *endpointcreds.Options) {
			p.HTTPClient = client
			p.Retryer = aws.NopRetryer{}
		}),
		sdkconfig.WithProcessCredentialOptions(func(p *processcreds.Options) { p.Timeout = 10 * time.Second }),
	}

	profile := o.Profile
	if profile == "" {
		// Boto3 gives AWS_DEFAULT_PROFILE precedence, including explicit emptiness.
		value, present := os.LookupEnv("AWS_DEFAULT_PROFILE")
		if !present {
			value, present = os.LookupEnv("AWS_PROFILE")
		}
		if present {
			if value == "" {
				return nil, ErrConfiguration
			}
			profile = value
		}
	}
	if profile != "" {
		profile = sdkProfileName(profiles, aliases, profile)
		options = append(options, sdkconfig.WithSharedConfigProfile(profile))
	}
	hasStatic := false
	if o.Profile == "" {
		access, secret, token := o.AccessKey, o.SecretKey, o.SessionToken
		if access == "" && secret == "" && token == "" {
			access, secret = os.Getenv("AWS_ACCESS_KEY_ID"), os.Getenv("AWS_SECRET_ACCESS_KEY")
			token = os.Getenv("AWS_SECURITY_TOKEN")
			if token == "" {
				token = os.Getenv("AWS_SESSION_TOKEN")
			}
			if access != "" && secret == "" {
				return nil, ErrConfiguration
			}
		}
		if access != "" && secret != "" {
			hasStatic = true
			options = append(options, sdkconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(access, secret, token)))
		}
	}
	var web *webIdentityConfig
	if !hasStatic {
		selected := profile
		if selected == "" {
			selected = "default"
		}
		web, err = selectWebIdentity(profiles[selected], o.Profile == "")
		if err != nil {
			return nil, err
		}
		if web != nil {
			// Preserve Boto3 precedence even when the SDK would select shared
			// static keys or treat an environment-selected profile as explicit.
			options = append(options, sdkconfig.WithCredentialsProvider(aws.AnonymousCredentials{}))
		} else if profile == "" {
			_, role := profiles[selected]["role_arn"]
			_, webFile := profiles[selected]["web_identity_token_file"]
			if role && !webFile {
				// Boto3 resolves the default ordinary role before considering
				// web-identity environment variables. The SDK reverses this.
				options = append(options, sdkconfig.WithSharedConfigProfile(selected))
			}
		}
	}
	cfg, roleSource, err := loadProviders(ctx, options)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, ErrConfiguration
	}
	if cfg.Credentials == nil {
		return nil, ErrConfiguration
	}
	if web != nil {
		cfg.Credentials = web.provider(cfg)
	} else {
		cfg, err = adaptProviders(ctx, cfg, options, profiles, roleSource, o.MFAToken, 0)
	}
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, ErrConfiguration
	}
	return &provider{inner: cfg.Credentials}, nil
}

type provider struct{ inner aws.CredentialsProvider }

func (*provider) String() string     { return "storage credential provider (redacted)" }
func (p *provider) GoString() string { return p.String() }
func (p *provider) Retrieve(parent context.Context) (aws.Credentials, error) {
	if err := parent.Err(); err != nil {
		return aws.Credentials{}, err
	}
	ctx, cancel := context.WithTimeout(parent, 10*time.Second)
	defer cancel()
	value, err := p.inner.Retrieve(ctx)
	if err != nil {
		if ctx.Err() != nil {
			return aws.Credentials{}, ctx.Err()
		}
		return aws.Credentials{}, ErrCredentials
	}
	if !value.HasKeys() || value.Expired() {
		return aws.Credentials{}, ErrCredentials
	}
	return value, nil
}

package s3credentials

import (
	"context"
	"io"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials/stscreds"
	"github.com/aws/aws-sdk-go-v2/service/sts"
	"github.com/aws/aws-sdk-go-v2/service/sts/types"
)

type webIdentityConfig struct{ path, role, session string }

func selectWebIdentity(values map[string]string, allowEnvironment bool) (*webIdentityConfig, error) {
	// Botocore's ordinary assume-role provider precedes web identity, but skips
	// profiles declaring a web-identity token file (including an empty value).
	if _, role := values["role_arn"]; role {
		if _, web := values["web_identity_token_file"]; !web {
			return nil, nil
		}
	}
	value := func(key, environment string) string {
		if allowEnvironment {
			if v, present := os.LookupEnv(environment); present {
				return v
			}
		}
		return values[key]
	}
	selected := &webIdentityConfig{
		path:    value("web_identity_token_file", "AWS_WEB_IDENTITY_TOKEN_FILE"),
		role:    value("role_arn", "AWS_ROLE_ARN"),
		session: value("role_session_name", "AWS_ROLE_SESSION_NAME"),
	}
	if selected.path == "" {
		return nil, nil
	}
	if selected.role == "" {
		return nil, ErrConfiguration
	}
	if selected.session == "" {
		selected.session = "botocore-session-" + strconv.FormatInt(time.Now().Unix(), 10)
	}
	return selected, nil
}

func (w *webIdentityConfig) provider(cfg aws.Config) aws.CredentialsProvider {
	cfg.Credentials = aws.AnonymousCredentials{}
	client := &webIdentityClient{inner: sts.NewFromConfig(cfg)}
	return aws.NewCredentialsCache(stscreds.NewWebIdentityRoleProvider(client, w.role, identityTokenFile(w.path), func(o *stscreds.WebIdentityRoleOptions) { o.RoleSessionName = w.session }))
}

type identityTokenFile string

func (path identityTokenFile) GetIdentityToken() ([]byte, error) {
	// Follow projected-token symlinks, but never block opening a FIFO/device.
	// The file is reopened on each refresh so atomic token rotation is observed.
	file, err := os.OpenFile(string(path), os.O_RDONLY|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, ErrCredentials
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 1<<20 {
		return nil, ErrCredentials
	}
	data, err := io.ReadAll(io.LimitReader(file, (1<<20)+1))
	if err != nil || len(data) > 1<<20 || !utf8.Valid(data) {
		return nil, ErrCredentials
	}
	// Python's text reader preserves whitespace but normalizes universal newlines.
	normalized := strings.ReplaceAll(strings.ReplaceAll(string(data), "\r\n", "\n"), "\r", "\n")
	return []byte(normalized), nil
}

type webIdentityClient struct {
	inner stscreds.AssumeRoleWithWebIdentityAPIClient
}

func (w *webIdentityClient) AssumeRoleWithWebIdentity(parent context.Context, in *sts.AssumeRoleWithWebIdentityInput, options ...func(*sts.Options)) (*sts.AssumeRoleWithWebIdentityOutput, error) {
	if utf8.RuneCountInString(aws.ToString(in.RoleArn)) < 20 || utf8.RuneCountInString(aws.ToString(in.RoleSessionName)) < 2 || utf8.RuneCountInString(aws.ToString(in.WebIdentityToken)) < 4 {
		return nil, ErrCredentials
	}
	ctx, cancel := context.WithTimeout(parent, 10*time.Second)
	defer cancel()
	// SDK web-identity adds its own retry wrapper. Retain the constructor's
	// explicit single-attempt bound even for InvalidIdentityToken responses.
	configured := append([]func(*sts.Options){}, options...)
	configured = append(configured, func(o *sts.Options) { o.Retryer = aws.NopRetryer{} })
	out, err := w.inner.AssumeRoleWithWebIdentity(ctx, in, configured...)
	if err != nil || out == nil || !validSTSCredentials(out.Credentials) {
		return nil, ErrCredentials
	}
	return out, nil
}

func validSTSCredentials(c *types.Credentials) bool {
	return c != nil && c.AccessKeyId != nil && c.SecretAccessKey != nil && c.SessionToken != nil && c.Expiration != nil && *c.AccessKeyId != "" && *c.SecretAccessKey != "" && *c.SessionToken != "" && c.Expiration.After(time.Now())
}

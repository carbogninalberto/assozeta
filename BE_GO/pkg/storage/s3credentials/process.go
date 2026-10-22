package s3credentials

import (
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"syscall"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	sdkconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/credentials/processcreds"
	"github.com/aws/aws-sdk-go-v2/credentials/stscreds"
	"github.com/aws/aws-sdk-go-v2/service/sts"
)

// Backend images and development fixtures use Linux/macOS. Credential commands
// run in their own process group, with no interactive input or console output.
func credentialCommand(ctx context.Context, command string) (*exec.Cmd, error) {
	cmd, err := (processcreds.DefaultNewCommandBuilder{Args: []string{command}}).NewCommand(ctx)
	if err != nil {
		return nil, err
	}
	cmd.Stdin = nil
	cmd.Stderr = io.Discard
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	stop := func() error {
		if cmd.Process == nil {
			return os.ErrProcessDone
		}
		err := syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
		if errors.Is(err, syscall.ESRCH) {
			return os.ErrProcessDone
		}
		return err
	}
	cmd.Cancel = stop
	cmd.WaitDelay = time.Second
	cmd.Stdout = &outputLimit{remaining: 1 << 20, stop: stop}
	return cmd, nil
}

type outputLimit struct {
	remaining int
	stop      func() error
}

func (w *outputLimit) Write(p []byte) (int, error) {
	if len(p) > w.remaining {
		_ = w.stop()
		return 0, ErrCredentials
	}
	w.remaining -= len(p)
	return len(p), nil
}

// The SDK exposes a custom process command builder but not through its config
// loader. Replace selected process providers, including source_profile chains,
// before any Retrieve. Other provider choices stay with the official loader.
func adaptProviders(ctx context.Context, cfg aws.Config, options []func(*sdkconfig.LoadOptions) error, profiles map[string]map[string]string, roleSource aws.CredentialsProvider, token MFATokenProvider, depth int) (aws.Config, error) {
	if depth > 20 {
		return aws.Config{}, ErrConfiguration
	}
	var shared sdkconfig.SharedConfig
	for _, source := range cfg.ConfigSources {
		if s, ok := source.(sdkconfig.SharedConfig); ok {
			shared = s
			break
		}
	}
	if depth > 0 {
		web, err := selectWebIdentity(profiles[shared.Profile], false)
		if err != nil {
			return aws.Config{}, err
		}
		if web != nil {
			cfg.Credentials = web.provider(cfg)
			return cfg, nil
		}
	}
	if aws.IsCredentialsProvider(cfg.Credentials, &processcreds.Provider{}) {
		if shared.CredentialProcess == "" {
			return aws.Config{}, ErrConfiguration
		}
		builder := processcreds.NewCommandBuilderFunc(func(ctx context.Context) (*exec.Cmd, error) { return credentialCommand(ctx, shared.CredentialProcess) })
		cfg.Credentials = aws.NewCredentialsCache(processcreds.NewProviderCommand(builder, func(o *processcreds.Options) { o.Timeout = 10 * time.Second }))
		return cfg, nil
	}
	if aws.IsCredentialsProvider(cfg.Credentials, &stscreds.AssumeRoleProvider{}) {
		source := aws.Config{Credentials: roleSource}
		var err error
		if shared.Source != nil {
			nested := append([]func(*sdkconfig.LoadOptions) error{}, options...)
			nested = append(nested, sdkconfig.WithSharedConfigProfile(shared.Source.Profile))
			var nestedSource aws.CredentialsProvider
			source, nestedSource, err = loadProviders(ctx, nested)
			if err != nil {
				return aws.Config{}, ErrConfiguration
			}
			source, err = adaptProviders(ctx, source, nested, profiles, nestedSource, token, depth+1)
			if err != nil {
				return aws.Config{}, err
			}
		} else if shared.CredentialSource == "Environment" {
			access, secret, token := os.Getenv("AWS_ACCESS_KEY_ID"), os.Getenv("AWS_SECRET_ACCESS_KEY"), os.Getenv("AWS_SECURITY_TOKEN")
			if token == "" {
				token = os.Getenv("AWS_SESSION_TOKEN")
			}
			if access == "" || secret == "" {
				return aws.Config{}, ErrConfiguration
			}
			source.Credentials = credentials.NewStaticCredentialsProvider(access, secret, token)
		}
		if source.Credentials == nil {
			return aws.Config{}, ErrConfiguration
		}
		signer := cfg
		signer.Credentials = &provider{inner: source.Credentials}
		role, err := newRoleClient(sts.NewFromConfig(signer), profiles[shared.Profile], token)
		if err != nil {
			return aws.Config{}, err
		}
		cfg.Credentials = aws.NewCredentialsCache(stscreds.NewAssumeRoleProvider(role, shared.RoleARN, func(o *stscreds.AssumeRoleOptions) {
			o.RoleSessionName = role.session
		}))
	}
	return cfg, nil
}

// The SDK builds ECS/IMDS source providers internally. Capture that provider
// through its public AssumeRole option hook, so adaptation preserves the SDK's
// endpoint, token-file and metadata configuration without reading another file
// or falling back to a different identity. Each load has its own capture.
func loadProviders(ctx context.Context, options []func(*sdkconfig.LoadOptions) error) (aws.Config, aws.CredentialsProvider, error) {
	var source aws.CredentialsProvider
	configured := append([]func(*sdkconfig.LoadOptions) error{}, options...)
	configured = append(configured, sdkconfig.WithAssumeRoleCredentialOptions(func(o *stscreds.AssumeRoleOptions) {
		// The loader also invokes this hook once for validation without a client.
		// Actual nested providers are constructed inner-first, with the selected
		// outer provider last. We retain that outer provider's source.
		if client, ok := o.Client.(*sts.Client); ok {
			source = client.Options().Credentials
		}
		// Defer prompting to the context-aware adapter. If a future SDK chain is
		// not adapted, its fallback must fail rather than acquire an MFA code itself.
		o.TokenProvider = func() (string, error) { return "", ErrCredentials }
	}))
	cfg, err := sdkconfig.LoadDefaultConfig(ctx, configured...)
	return cfg, source, err
}

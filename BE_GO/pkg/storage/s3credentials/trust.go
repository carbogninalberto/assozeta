package s3credentials

import (
	"context"
	"os"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3trust"
)

// LoadTrust reads selection only, never a certificate bundle. Call it at the
// lazy storage-client boundary with the same explicit profile as New. Shared
// profile parsing and alias/credential-file precedence remain in this package.
func LoadTrust(ctx context.Context, profile string) (s3trust.Policy, error) {
	if err := ctx.Err(); err != nil {
		return s3trust.Policy{}, err
	}
	config, err := sharedFile("AWS_CONFIG_FILE", "config")
	if err != nil {
		return s3trust.Policy{}, err
	}
	credentials, err := sharedFile("AWS_SHARED_CREDENTIALS_FILE", "credentials")
	if err != nil {
		return s3trust.Policy{}, err
	}
	profiles, aliases, err := readRoleProfiles(config, credentials)
	if err != nil {
		return s3trust.Policy{}, err
	}
	return trustFromProfile(profile, profiles, aliases)
}

func trustFromProfile(profile string, profiles map[string]map[string]string, aliases map[string]string) (s3trust.Policy, error) {
	if profile == "" {
		value, present := os.LookupEnv("AWS_DEFAULT_PROFILE")
		if !present {
			value, present = os.LookupEnv("AWS_PROFILE")
		}
		if present {
			if value == "" {
				return s3trust.Policy{}, ErrConfiguration
			}
			profile = value
		}
	}
	if profile == "" {
		profile = "default"
	}
	profile = sdkProfileName(profiles, aliases, profile)
	var shared *string
	if value, present := profiles[profile]["ca_bundle"]; present {
		shared = &value
	}
	return s3trust.Select(shared), nil
}

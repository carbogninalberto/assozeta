package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// StripeEnvironment is the Stripe settings Django reads when the instance has
// no override: only presence matters for availability.
type StripeEnvironment struct {
	PublicKey, Secret, Webhook bool
}

type StripeAvailability struct {
	reader      domain.SocialConfigurationReader
	environment StripeEnvironment
	secrets     []string
}

func NewStripeAvailability(reader domain.SocialConfigurationReader, environment StripeEnvironment, secretKey string) *StripeAvailability {
	return &StripeAvailability{reader: reader, environment: environment, secrets: []string{secretKey}}
}

// DirectCredentialsConfigured is stripe_direct_credentials_configured():
// effective_integration('stripe') is the self-hosted instance override when its
// record has settings, else the environment (enabled = any value set); an
// undecryptable stored secret disables payments. Values that Django cannot
// even read (a non-object record, a non-string secret) are errors.
func (s *StripeAvailability) DirectCredentialsConfigured(ctx context.Context) (bool, error) {
	row, err := s.reader.SocialConfiguration(ctx)
	if err != nil {
		return false, err
	}
	var settings map[string]any
	if row != nil && row.SelfHosted {
		integrations, err := socialJSON(row.Integrations)
		if err != nil {
			return false, err
		}
		if dto.PythonTruthy(integrations) {
			all, ok := integrations.(map[string]any)
			if !ok {
				return false, ErrSocialConfiguration
			}
			if record, present := all["stripe"]; present {
				fields, ok := record.(map[string]any)
				if !ok {
					return false, ErrSocialConfiguration
				}
				if value, present := fields["settings"]; present && value != nil {
					if settings, ok = value.(map[string]any); !ok {
						return false, ErrSocialConfiguration
					}
				}
			}
		}
	}
	if settings == nil {
		env := s.environment
		return (env.PublicKey || env.Secret || env.Webhook) && env.Secret && env.PublicKey, nil
	}
	values := map[string]any{}
	for key, value := range settings {
		values[key] = value
	}
	for _, key := range []string{"secret_key", "webhook_secret"} {
		value := values[key]
		if !dto.PythonTruthy(value) {
			values[key] = ""
			continue
		}
		text, ok := value.(string)
		if !ok {
			return false, ErrSocialConfiguration
		}
		plain, err := DecryptIntegrationSecret(text, s.secrets)
		if err != nil {
			return false, nil
		}
		values[key] = plain
	}
	// value['enabled'] and value['secret_key'] and value['public_key']: a
	// missing key that is reached raises KeyError.
	for _, key := range []string{"enabled", "secret_key", "public_key"} {
		value, present := values[key]
		if !present {
			return false, ErrSocialConfiguration
		}
		if !dto.PythonTruthy(value) {
			return false, nil
		}
	}
	return true, nil
}

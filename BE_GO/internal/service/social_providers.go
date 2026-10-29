package service

import "context"

type SocialClientIDReader interface {
	ClientID(context.Context, string) (any, error)
}
type SocialProviderClient interface {
	Verify(context.Context, any, any) (map[string]any, bool, error)
}

// SocialProviders binds the current instance configuration to explicit provider
// clients. It implements SocialTokenVerifier for SocialLogin. No provider or
// database connection is stored in a package-global registry.
type SocialProviders struct {
	ids           SocialClientIDReader
	google, apple SocialProviderClient
}

func NewSocialProviders(ids SocialClientIDReader, google, apple SocialProviderClient) *SocialProviders {
	return &SocialProviders{ids: ids, google: google, apple: apple}
}

func (s *SocialProviders) Verify(ctx context.Context, provider string, token any) (SocialIdentity, bool, error) {
	if err := ctx.Err(); err != nil {
		return SocialIdentity{}, false, err
	}
	var client SocialProviderClient
	switch provider {
	case "google":
		client = s.google
	case "apple":
		client = s.apple
	default:
		return SocialIdentity{}, false, ErrSocialConfiguration
	}
	if client == nil {
		return SocialIdentity{}, false, ErrSocialConfiguration
	}
	audience, err := s.ids.ClientID(ctx, provider)
	if err != nil {
		return SocialIdentity{}, false, err
	}
	if err = ctx.Err(); err != nil {
		return SocialIdentity{}, false, err
	}
	if !socialConfigTruthy(audience) {
		return SocialIdentity{}, false, nil
	}
	info, valid, err := client.Verify(ctx, token, audience)
	if err != nil {
		return SocialIdentity{}, false, err
	}
	if err = ctx.Err(); err != nil {
		return SocialIdentity{}, false, err
	}
	if !valid {
		return SocialIdentity{}, false, nil
	}
	return SocialIdentity{Email: info["email"]}, true, nil
}

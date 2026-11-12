package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var ErrSocialConfiguration = errors.New("invalid social integration configuration")

type SocialClientIDs struct {
	reader        domain.SocialConfigurationReader
	google, apple string
}

func NewSocialClientIDs(reader domain.SocialConfigurationReader, google, apple string) *SocialClientIDs {
	return &SocialClientIDs{reader: reader, google: google, apple: apple}
}

// ClientID reads the current override for each new verification operation. The
// returned value deliberately retains JSON type, including a list audience or
// null; provider clients must apply their verifier's audience rules themselves.
func (s *SocialClientIDs) ClientID(ctx context.Context, provider string) (any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	environment := ""
	switch provider {
	case "google":
		environment = s.google
	case "apple":
		environment = s.apple
	default:
		return nil, ErrSocialConfiguration
	}
	row, err := s.reader.SocialConfiguration(ctx)
	if err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if row == nil || !row.SelfHosted {
		return environment, nil
	}
	integrations, err := socialJSON(row.Integrations)
	if err != nil {
		return nil, err
	}
	if !socialConfigTruthy(integrations) {
		integrations = map[string]any{}
	}
	settings, ok := integrations.(map[string]any)
	if !ok {
		return nil, ErrSocialConfiguration
	}
	var record = map[string]any{}
	if value, present := settings[provider]; present {
		record, ok = value.(map[string]any)
		if !ok {
			return nil, ErrSocialConfiguration
		}
	}
	if value := record["settings"]; value != nil {
		override, err := socialSettingsDictionary(value)
		if err != nil {
			return nil, err
		}
		enabled, present := override["enabled"]
		if !present {
			return nil, ErrSocialConfiguration
		}
		if !socialConfigTruthy(enabled) {
			return "", nil
		}
		client, present := override["client_id"]
		if !present {
			return nil, ErrSocialConfiguration
		}
		return client, nil
	}
	// Only Google's environment fallback observes legacy login visibility. An
	// explicit instance override, Apple and hosted installations bypass it.
	if provider != "google" || environment == "" {
		return environment, nil
	}
	display, err := socialJSON(row.Display)
	if err != nil {
		return nil, err
	}
	if !socialConfigTruthy(display) {
		return environment, nil
	}
	displayMap, ok := display.(map[string]any)
	if !ok {
		return nil, ErrSocialConfiguration
	}
	login, present := displayMap["login"]
	if !present {
		return environment, nil
	}
	loginMap, ok := login.(map[string]any)
	if !ok {
		return nil, ErrSocialConfiguration
	}
	allow, present := loginMap["allowOauthLogin"]
	if present && !socialConfigTruthy(allow) {
		return "", nil
	}
	return environment, nil
}

func socialJSON(data []byte) (any, error) {
	if len(data) == 0 {
		return nil, nil
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	var value any
	if err := d.Decode(&value); err != nil {
		return nil, ErrSocialConfiguration
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return nil, ErrSocialConfiguration
	}
	return value, nil
}

func socialConfigTruthy(value any) bool {
	if object, ok := value.(map[string]any); ok {
		return len(object) > 0
	}
	return truthyReference(value)
}

// Python dict(value) also accepts an array of pairs. Preserve valid historical
// JSON settings of that shape; malformed data must not silently enable fallback.
func socialSettingsDictionary(value any) (map[string]any, error) {
	if object, ok := value.(map[string]any); ok {
		return object, nil
	}
	if value == "" {
		return map[string]any{}, nil
	}
	items, ok := value.([]any)
	if !ok {
		return nil, ErrSocialConfiguration
	}
	object := map[string]any{}
	for _, item := range items {
		pair, ok := item.([]any)
		if !ok || len(pair) != 2 {
			return nil, ErrSocialConfiguration
		}
		key, ok := pair[0].(string)
		if !ok {
			return nil, ErrSocialConfiguration
		}
		object[key] = pair[1]
	}
	return object, nil
}

package service

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type InstanceBootstrapOptions struct {
	Version                string
	AIEnabled              bool
	StripeEnabled          bool
	StripePublicKey        string
	GoogleClientID         string
	AppleClientID          string
	FamilyRegistration     bool
	FederationRegistration bool
	MultipleRegistration   bool
}
type InstanceBootstrap struct {
	store   domain.InstanceBootstrapStore
	manual  domain.ManualAvailability
	options InstanceBootstrapOptions
}

func NewInstanceBootstrap(store domain.InstanceBootstrapStore, manual domain.ManualAvailability, options InstanceBootstrapOptions) *InstanceBootstrap {
	return &InstanceBootstrap{store: store, manual: manual, options: options}
}
func (s *InstanceBootstrap) Status(ctx context.Context) (*dto.InstanceStatus, error) {
	config, err := s.store.FirstInstance(ctx)
	if err != nil {
		return nil, err
	}
	ai, _, err := s.integration(config, "ai")
	if err != nil {
		return nil, err
	}
	manual, err := s.manual.Available(ctx)
	if err != nil {
		return nil, err
	}
	value := &dto.InstanceStatus{AIEnabled: ai["enabled"], ManualEnabled: manual, Configured: config != nil, Version: s.options.Version, SupportedFeatures: []string{"import", "export", "oauth"}}
	if config != nil {
		value.InstanceName = &config.Name
	}
	return value, nil
}
func (s *InstanceBootstrap) Access(ctx context.Context, actor domain.Account) (*dto.InstanceAccess, error) {
	config, err := s.store.FirstInstance(ctx)
	if err != nil {
		return nil, err
	}
	value := &dto.InstanceAccess{}
	if config == nil || !config.SelfHosted {
		return value, nil
	}
	value.IsOwner = config.OwnerID != nil && *config.OwnerID == actor.ID
	value.IsAdministrator = actor.Active && !actor.Deleted && (actor.Superuser || value.IsOwner)
	if value.IsAdministrator && config.AssociationID != nil && config.AssociationName != nil {
		value.DataAssociation = &dto.InstanceDataAssociation{ID: *config.AssociationID, Name: *config.AssociationName}
	}
	return value, nil
}
func (s *InstanceBootstrap) Config(ctx context.Context) (*dto.InstanceConfig, error) {
	config, err := s.store.FirstInstance(ctx)
	if err != nil {
		return nil, err
	}
	if config == nil {
		return nil, domain.ErrInstanceNotConfigured
	}
	display, err := instanceDisplay(config.DisplaySettings)
	if err != nil {
		return nil, err
	}
	google, googleSource, err := s.integration(config, "google")
	if err != nil {
		return nil, err
	}
	apple, appleSource, err := s.integration(config, "apple")
	if err != nil {
		return nil, err
	}
	stripe, _, err := s.integration(config, "stripe")
	if err != nil {
		return nil, err
	}
	ai, _, err := s.integration(config, "ai")
	if err != nil {
		return nil, err
	}
	googleID, err := instanceClientID(google, googleSource, config.GoogleClientID)
	if err != nil {
		return nil, err
	}
	appleID, err := instanceClientID(apple, appleSource, config.AppleClientID)
	if err != nil {
		return nil, err
	}
	googleEnabled := google["enabled"]
	if instanceTruthy(googleEnabled) {
		if googleSource {
			googleEnabled = true
		} else {
			googleEnabled, err = instanceAllowOAuth(display)
			if err != nil {
				return nil, err
			}
		}
	}
	var publicKey any
	if instanceTruthy(stripe["enabled"]) {
		var ok bool
		publicKey, ok = stripe["public_key"]
		if !ok {
			return nil, domain.ErrInstanceConfigurationInvalid
		}
		if !instanceTruthy(publicKey) {
			publicKey = nil
		}
	}
	manual, err := s.manual.Available(ctx)
	if err != nil {
		return nil, err
	}
	logo := config.LogoPath
	if logo == "" {
		logo = "/oem/assozeta/brand/logo.svg"
	}
	if logo == "/api/instance/logo.png" {
		logo += "?v=" + config.UpdatedAt.UTC().Format("20060102150405") + fmt.Sprintf("%06d", config.UpdatedAt.Nanosecond()/1000)
	}
	title := config.MetaTitle
	if title == "" {
		title = config.Name
	}
	description := config.MetaDescription
	if description == "" {
		description = "Gestionale per associazioni sportive"
	}
	return &dto.InstanceConfig{
		OEM:    dto.InstanceOEM{Name: config.Name, Abbreviation: config.Abbreviation, Logo: logo, SupportEmail: config.SupportEmail, PrimaryColor: config.PrimaryColor, DisplaySettings: display, ManualURL: "/#/manuale"},
		OAuth:  dto.InstanceOAuth{GoogleClientID: googleID, AppleClientID: appleID, GoogleEnabled: googleEnabled},
		Stripe: dto.InstanceStripe{PublicKey: publicKey}, Meta: dto.InstanceMeta{Title: title, Description: description, Manifest: "/api/instance/manifest.json"},
		Features: dto.InstanceFeatures{AIEnabled: ai["enabled"], ManualEnabled: manual, IsReseller: config.IsReseller, SelfHosted: config.SelfHosted, SupportMultipleAssociations: config.SupportMultipleAssociations, ShowCourseAthletes: config.ShowCourseAthletes, ShowMinorExemption: config.ShowMinorExemption, ShowTutorial: config.ShowTutorial, FamilyRegistration: s.options.FamilyRegistration, FederationRegistration: s.options.FederationRegistration, MultipleRegistration: s.options.MultipleRegistration},
		Setup:    dto.InstanceSetup{Provenance: config.SetupProvenance, OnboardingComplete: config.OnboardingCompletedAt != nil},
	}, nil
}

func instanceClientID(value map[string]any, source bool, legacy string) (any, error) {
	if !instanceTruthy(value["enabled"]) {
		return nil, nil
	}
	if !source && legacy != "" {
		return legacy, nil
	}
	id, ok := value["client_id"]
	if !ok {
		return nil, domain.ErrInstanceConfigurationInvalid
	}
	if !instanceTruthy(id) {
		return nil, nil
	}
	return id, nil
}
func instanceJSON(raw json.RawMessage) (any, error) {
	var value any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return nil, domain.ErrInstanceConfigurationInvalid
	}
	return value, nil
}
func instanceTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		n, _ := strconv.ParseFloat(string(v), 64)
		return n != 0
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	default:
		return true
	}
}
func instanceAllowOAuth(display map[string]any) (any, error) {
	login, ok := display["login"].(map[string]any)
	if !ok {
		return nil, domain.ErrInstanceConfigurationInvalid
	}
	enabled, ok := login["allowOauthLogin"]
	if !ok {
		return false, nil
	}
	return enabled, nil
}
func (s *InstanceBootstrap) integration(config *domain.InstanceBootstrapSnapshot, provider string) (map[string]any, bool, error) {
	if config != nil && config.SelfHosted && !config.IntegrationsEmpty {
		if config.IntegrationKind == nil || *config.IntegrationKind != "object" {
			return nil, false, domain.ErrInstanceConfigurationInvalid
		}
		raw, err := instanceJSON(config.PublicIntegrations)
		if err != nil {
			return nil, false, err
		}
		all, ok := raw.(map[string]any)
		if !ok {
			return nil, false, domain.ErrInstanceConfigurationInvalid
		}
		metadata, ok := all[provider].(map[string]any)
		if !ok {
			return nil, false, domain.ErrInstanceConfigurationInvalid
		}
		if metadata["present"] == true {
			if metadata["kind"] != "object" {
				return nil, false, domain.ErrInstanceConfigurationInvalid
			}
			if settings := metadata["settings"]; settings != nil {
				value, ok := settings.(map[string]any)
				// dict(list-of-pairs) is a historical accepted JSON shape in Django.
				if !ok {
					if pairs, valid := settings.([]any); valid {
						value = map[string]any{}
						ok = true
						for _, item := range pairs {
							pair, valid := item.([]any)
							if !valid || len(pair) != 2 {
								ok = false
								break
							}
							key, valid := pair[0].(string)
							if !valid {
								ok = false
								break
							}
							value[key] = pair[1]
						}
					}
				}
				if !ok {
					return nil, false, domain.ErrInstanceConfigurationInvalid
				}
				if _, exists := value["enabled"]; !exists {
					return nil, false, domain.ErrInstanceConfigurationInvalid
				}
				return value, true, nil
			}
		}
	}
	value := map[string]any{}
	switch provider {
	case "ai":
		value["enabled"] = s.options.AIEnabled
	case "stripe":
		value["enabled"] = s.options.StripeEnabled
		value["public_key"] = s.options.StripePublicKey
	case "apple":
		value["enabled"] = s.options.AppleClientID != ""
		value["client_id"] = s.options.AppleClientID
	case "google":
		var enabled any = s.options.GoogleClientID != ""
		if instanceTruthy(enabled) && config != nil && config.SelfHosted {
			display, err := instanceDisplay(config.DisplaySettings)
			if err != nil {
				return nil, false, err
			}
			enabled, err = instanceAllowOAuth(display)
			if err != nil {
				return nil, false, err
			}
		}
		value["enabled"] = enabled
		value["client_id"] = s.options.GoogleClientID
	default:
		return nil, false, domain.ErrInstanceConfigurationInvalid
	}
	return value, false, nil
}

const instanceDisplayDefaults = `{"general":{"searchBar":true,"cookieConsent":true,"showDemoButton":false},"login":{"showSportAssociationCreation":false,"allowOauthLogin":true,"showFooterInfo":true,"allowOnlyAthletes":false,"showTestimonials":false,"termsAndConditionsUrl":""},"navbar":{"showAffiliateLink":false,"showReview":false,"showNotifications":true},"sidebar":{"showPlanUpgrades":false,"showPrivacyPolicy":true,"showTermsOfService":true,"showPreferences":true}}`

func instanceDisplay(raw json.RawMessage) (map[string]any, error) {
	defaults, _ := instanceJSON(json.RawMessage(instanceDisplayDefaults))
	result := defaults.(map[string]any)
	value, err := instanceJSON(raw)
	if err != nil {
		return nil, err
	}
	if !instanceTruthy(value) {
		return result, nil
	}
	entries, ok := value.(map[string]any)
	if !ok {
		return nil, domain.ErrInstanceConfigurationInvalid
	}
	for key, value := range entries {
		existing, left := result[key].(map[string]any)
		incoming, right := value.(map[string]any)
		if left && right {
			for k, v := range incoming {
				existing[k] = v
			}
		} else {
			result[key] = value
		}
	}
	return result, nil
}

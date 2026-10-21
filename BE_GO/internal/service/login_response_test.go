package service

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
)

type responseProfiles struct {
	profile *dto.UserAuthProfile
	fail    bool
	calls   int
}

func (s *responseProfiles) Read(context.Context, string) (*dto.UserAuthProfile, error) {
	s.calls++
	if s.fail {
		return nil, errors.New("profile failed")
	}
	return s.profile, nil
}

type responseAssociations struct {
	profile *dto.AssociationProfile
	owner   string
	fail    bool
	calls   int
}

func (s *responseAssociations) Read(_ context.Context, owner string) (*dto.AssociationProfile, error) {
	s.owner = owner
	s.calls++
	if s.fail {
		return nil, errors.New("association failed")
	}
	return s.profile, nil
}

type responseSettings struct {
	row        domain.LoginResponseSettings
	fail       bool
	instructor bool
	calls      int
}

func (s *responseSettings) LoginResponseSettings(_ context.Context, _ string, instructor bool) (domain.LoginResponseSettings, error) {
	s.instructor = instructor
	s.calls++
	if s.fail {
		return domain.LoginResponseSettings{}, errors.New("settings failed")
	}
	return s.row, nil
}

type responseCards struct {
	calls int
	fail  bool
}

func (s *responseCards) ForLogin(context.Context, domain.Account, string, domain.LoginAuditContext) (*dto.MembershipCardConfiguration, error) {
	s.calls++
	if s.fail {
		return nil, errors.New("card failed")
	}
	return &dto.MembershipCardConfiguration{CustomizedTemplate: json.RawMessage("null")}, nil
}

type responseImages struct {
	inputs []string
	fail   bool
}

func (s *responseImages) CompressLoginImage(_ context.Context, value string) (string, error) {
	s.inputs = append(s.inputs, value)
	if s.fail {
		return "", errors.New("image failed")
	}
	return "converted:" + value, nil
}
func responseFixtures() (*responseProfiles, *responseAssociations, *responseSettings, *responseCards) {
	return &responseProfiles{profile: &dto.UserAuthProfile{UserID: "target"}}, &responseAssociations{profile: &dto.AssociationProfile{ID: "association"}}, &responseSettings{row: domain.LoginResponseSettings{UserID: "target", TemporaryInvoiceDeletion: true, TablesSettings: json.RawMessage("null")}}, &responseCards{}
}
func TestLoginResponseRolesAndOmittedFields(t *testing.T) {
	for _, tc := range []struct {
		role               int
		admin              bool
		label              string
		augmented, welcome bool
	}{{0, false, "association", false, false}, {1, false, "association", true, false}, {2, false, "athlete", false, false}, {3, false, "association", true, true}, {1, true, "administrator", false, true}, {3, true, "administrator", false, true}} {
		p, a, s, c := responseFixtures()
		owner := "owner"
		result, err := NewLoginResponses(p, a, s, c, nil, domain.RegistrationChoices{}).Build(context.Background(), domain.Account{ID: "target", Role: tc.role, Superuser: tc.admin, ConnectedUserID: &owner}, jwt.Pair{AccessToken: "access"}, domain.LoginAuditContext{})
		if err != nil || result == nil || result.Role != tc.label || result.AccessToken != "access" {
			t.Fatal("role response differs", err)
		}
		encoded, err := json.Marshal(result)
		if err != nil {
			t.Fatal(err)
		}
		var obj map[string]json.RawMessage
		if json.Unmarshal(encoded, &obj) != nil {
			t.Fatal("invalid JSON")
		}
		if (obj["requires_welcome"] != nil) != tc.welcome || (obj["tables_settings"] != nil) != tc.augmented {
			t.Fatal("wrong top-level key presence")
		}
		if tc.augmented {
			if a.calls != 1 || s.calls != 1 || c.calls != 1 || result.UserData.PreviewAndCustomFeatures == nil || len(*result.UserData.PreviewAndCustomFeatures) != 0 {
				t.Fatal("association augmentation missing")
			}
			if tc.role == 3 && (a.owner != "owner" || !s.instructor) {
				t.Fatal("collaborator ownership incorrect")
			}
		} else if a.calls+s.calls+c.calls != 0 {
			t.Fatal("excluded role read/wrote association state")
		}
		if (result.UserData.IsSuperuser != nil) != tc.admin {
			t.Fatal("superuser key presence differs")
		}
	}
}
func TestLoginResponseImageDispatchAndFeatureOrder(t *testing.T) {
	for _, empty := range []bool{false, true} {
		p, a, s, c := responseFixtures()
		avatar, logo, section := "avatar", "logo", "present"
		p.profile.AvatarImage = &avatar
		a.profile.Logo = &logo
		a.profile.Regulation = &section
		a.profile.Demand = &section
		if empty {
			a.profile.Demand = nil
		}
		images := &responseImages{}
		result, err := NewLoginResponses(p, a, s, c, images, domain.RegistrationChoices{Family: true, Federation: true, Multiple: true}).Build(context.Background(), domain.Account{ID: "target", Role: 1}, jwt.Pair{}, domain.LoginAuditContext{})
		if err != nil {
			t.Fatal(err)
		}
		want := 1
		if empty {
			want = 2
		}
		if len(images.inputs) != want || *result.UserData.AvatarImage != "converted:avatar" || (result.UserData.SportAssociation.EmptySections != nil) != empty {
			t.Fatal("image dispatch differs")
		}
		if *p.profile.AvatarImage != "avatar" || *a.profile.Logo != "logo" {
			t.Fatal("builder mutated upstream profile")
		}
		features := *result.UserData.PreviewAndCustomFeatures
		for i, name := range []string{"Iscrizione per famiglia", "Iscrizioni per Enti e Federazioni", "Iscrizioni multiple per federazioni"} {
			if features[i].Name != name || features[i].Description != "" {
				t.Fatal("feature order/name changed")
			}
		}
	}
}
func TestLoginResponseFailureDoesNotReturnPartialTokens(t *testing.T) {
	for _, failure := range []string{"role", "profile", "foreign_profile", "association", "settings", "foreign_settings", "card", "image", "missing_image_processor", "owner", "cancel"} {
		t.Run(failure, func(t *testing.T) {
			p, a, s, c := responseFixtures()
			account := domain.Account{ID: "target", Role: 1}
			images := &responseImages{}
			var processor domain.LoginImageProcessor = images
			switch failure {
			case "role":
				account.Role = 4
			case "profile":
				p.fail = true
			case "foreign_profile":
				p.profile.UserID = "foreign"
			case "association":
				a.fail = true
			case "settings":
				s.fail = true
			case "foreign_settings":
				s.row.UserID = "foreign"
			case "card":
				c.fail = true
			case "image", "missing_image_processor":
				v := "image"
				p.profile.AvatarImage = &v
				images.fail = true
				if failure == "missing_image_processor" {
					processor = nil
				}
			case "owner":
				account.Role = 3
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if failure == "cancel" {
				cancel()
			}
			result, err := NewLoginResponses(p, a, s, c, processor, domain.RegistrationChoices{}).Build(ctx, account, jwt.Pair{AccessToken: "must-not-escape"}, domain.LoginAuditContext{})
			if err == nil || result != nil {
				t.Fatal("failure returned partial response")
			}
			if failure != "card" && c.calls != 0 {
				t.Fatal("failure occurred after unintended configuration writes")
			}
		})
	}
}

type cancellingResponseProfile struct{ cancel context.CancelFunc }

func (p cancellingResponseProfile) Read(context.Context, string) (*dto.UserAuthProfile, error) {
	p.cancel()
	return &dto.UserAuthProfile{UserID: "target"}, nil
}
func TestLoginResponseCancellationAfterBaseReadReturnsNothing(t *testing.T) {
	for _, account := range []domain.Account{{ID: "target", Role: 0}, {ID: "target", Role: 2}, {ID: "target", Role: 1, Superuser: true}} {
		ctx, cancel := context.WithCancel(context.Background())
		_, a, s, c := responseFixtures()
		result, err := NewLoginResponses(cancellingResponseProfile{cancel}, a, s, c, nil, domain.RegistrationChoices{}).Build(ctx, account, jwt.Pair{AccessToken: "private"}, domain.LoginAuditContext{})
		if result != nil || !errors.Is(err, context.Canceled) {
			t.Fatal("cancelled early return leaked response", err)
		}
	}
}

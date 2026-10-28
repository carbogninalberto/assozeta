package service

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrSearchProfileNotFound is NotFound("user not found.").
var ErrSearchProfileNotFound = errors.New("search profile user not found")

type searchProfileAssociations interface {
	Read(ctx context.Context, owner string) (*dto.AssociationProfile, error)
}

type searchProfileStripe interface {
	DirectCredentialsConfigured(ctx context.Context) (bool, error)
}

type SearchProfiles struct {
	store        domain.SearchProfileStore
	associations searchProfileAssociations
	images       domain.LoginImageProcessor
	stripe       searchProfileStripe
	choices      domain.RegistrationChoices
	now          func() time.Time
}

func NewSearchProfiles(store domain.SearchProfileStore, associations searchProfileAssociations, images domain.LoginImageProcessor,
	stripe searchProfileStripe, choices domain.RegistrationChoices, now func() time.Time) *SearchProfiles {
	return &SearchProfiles{store: store, associations: associations, images: images, stripe: stripe, choices: choices, now: now}
}

// searchProfileKeys is SportAssociationSearchProfileSerializer's projection of
// the association serializer.
var searchProfileKeys = []string{"denomination", "tax_code", "address", "address_cap", "address_city", "subscription_fee",
	"multiple_subscription_fee", "subscription_fee_plans", "membership_fee", "multiple_membership_fee", "membership_fee_plans",
	"custom_subscription_form_data", "configuration", "enabled_for", "enable_quotes_management", "checkout_info", "additional_fields",
	"stripe_available_methods", "invoice_template", "subscription_template", "extra_text_invoices"}

// user is UserSerializerSearch; association users' online_payments needs the
// owner's flag and configured Stripe credentials.
func (s *SearchProfiles) user(ctx context.Context, row domain.SearchProfileUser, hasAssociation bool) (map[string]any, error) {
	features := []string{}
	for _, choice := range []struct {
		on   bool
		name string
	}{{s.choices.Family, "Iscrizione per famiglia"}, {s.choices.Federation, "Iscrizioni per Enti e Federazioni"}, {s.choices.Multiple, "Iscrizioni multiple per federazioni"}} {
		if choice.on {
			features = append(features, choice.name)
		}
	}
	online := false
	if row.Role != domain.RoleAthlete && hasAssociation && row.OnlinePayments {
		configured, err := s.stripe.DirectCredentialsConfigured(ctx)
		if err != nil {
			return nil, err
		}
		online = configured
	}
	return map[string]any{"first_name": row.FirstName, "last_name": row.LastName, "username": row.Username, "avatar_image": row.Avatar,
		"preview_and_custom_features": features, "disable_account_creation": row.DisableCreation,
		"force_account_creation": row.ForceCreation, "online_payments": online}, nil
}

func (s *SearchProfiles) avatar(ctx context.Context, value *string) (*string, error) {
	if value == nil {
		return nil, nil
	}
	text, err := s.images.CompressLoginImage(ctx, *value)
	return &text, err
}

func (s *SearchProfiles) association(ctx context.Context, owner string) (map[string]any, error) {
	profile, err := s.associations.Read(ctx, owner)
	if err != nil {
		return nil, err
	}
	raw, err := json.Marshal(profile)
	if err != nil {
		return nil, err
	}
	var all map[string]json.RawMessage
	if err = json.Unmarshal(raw, &all); err != nil {
		return nil, err
	}
	block := make(map[string]any, len(searchProfileKeys))
	for _, key := range searchProfileKeys {
		block[key] = all[key]
	}
	return block, nil
}

// SearchProfilePreregistration is the view's season window: open from 120
// days before the next season start until the day before; an impossible
// start date closes it.
func SearchProfilePreregistration(today time.Time, month, day int) (bool, int) {
	valid := func(year int) (time.Time, bool) {
		if month < 1 || month > 12 || day < 1 {
			return time.Time{}, false
		}
		date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
		return date, date.Day() == day
	}
	today = time.Date(today.Year(), today.Month(), today.Day(), 0, 0, 0, 0, time.UTC)
	start, ok := valid(today.Year())
	if ok && !start.After(today) {
		start, ok = valid(today.Year() + 1)
	}
	if !ok {
		return true, 0
	}
	days := int(start.Sub(today).Hours() / 24)
	if days >= 1 && days <= 120 {
		return false, days
	}
	return true, 0
}

// Profile follows search_profile.
func (s *SearchProfiles) Profile(ctx context.Context, username string, moduleInfo bool) (map[string]any, error) {
	row, err := s.store.User(ctx, username)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrSearchProfileNotFound
	}
	if row.Role == domain.RoleAthlete {
		user, err := s.user(ctx, *row, false)
		if err != nil {
			return nil, err
		}
		user["is_athlete"] = true
		return map[string]any{"data": map[string]any{"user": user}}, nil
	}
	association, err := s.store.OwnerAssociation(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	user, err := s.user(ctx, *row, true)
	if err != nil {
		return nil, err
	}
	if user["avatar_image"], err = s.avatar(ctx, row.Avatar); err != nil {
		return nil, err
	}
	block, err := s.association(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	user["sport_association"] = block
	data := map[string]any{"user": user, "sport_association_id": association}
	if moduleInfo {
		module, err := s.store.Module(ctx, association)
		if err != nil {
			return nil, err
		}
		block["demand"], block["regulation"] = module.Demand, module.Regulation
		block["additional_sections"] = json.RawMessage(module.AdditionalSections)
		block["show_regulation_to_members"], block["show_regulation_to_both"] = module.ShowRegulationToMembers, module.ShowRegulationToBoth
		block["show_regulation_to_athletes"], block["show_demand_to_members"] = module.ShowRegulationToAthletes, module.ShowDemandToMembers
		block["show_demand_to_both"], block["show_demand_to_athletes"] = module.ShowDemandToBoth, module.ShowDemandToAthletes
		block["preregistration_module_closed"], block["remaining_preregistration_days"] = SearchProfilePreregistration(s.now().UTC(), row.StartMonth, row.StartDay)
		return map[string]any{"data": data}, nil
	}
	courses := map[bool][]dto.SearchProfileCourse{}
	for _, pinned := range []bool{false, true} {
		rows, err := s.store.Courses(ctx, association, pinned)
		if err != nil {
			return nil, err
		}
		items := make([]dto.SearchProfileCourse, 0, len(rows))
		for _, course := range rows {
			item, err := dto.SearchProfileCourseFrom(course)
			if err != nil {
				return nil, err
			}
			items = append(items, item)
		}
		courses[pinned] = items
	}
	carnets, err := s.store.Carnets(ctx, association)
	if err != nil {
		return nil, err
	}
	infos := make([]map[string]any, 0, len(carnets))
	for _, carnet := range carnets {
		item := dto.CarnetListItemFrom(carnet)
		infos = append(infos, map[string]any{"carnet_id": item.ID, "title": item.Title, "description": item.Description,
			"lessons_number": item.LessonsNumber, "creation_date": item.CreationDate, "fee": item.Fee, "public": item.Public})
	}
	tags, err := s.store.Tags(ctx, association)
	if err != nil {
		return nil, err
	}
	labels := make([]map[string]any, 0, len(tags))
	for _, tag := range tags {
		labels = append(labels, map[string]any{"tag_id": tag.ID, "tag_name": tag.Name, "active": true})
	}
	user["is_athlete"] = false
	data["courses"], data["courses_pinned"], data["carnets"], data["courses_tags"] = courses[false], courses[true], infos, labels
	return map[string]any{"data": data}, nil
}

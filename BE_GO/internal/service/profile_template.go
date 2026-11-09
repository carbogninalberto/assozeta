package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrProfileTemplateServer is an exception of profile_update_subscription_template:
// the 500 page. Exceptions raised inside save() keep auditlog's pre_save entry.
var ErrProfileTemplateServer = errors.New("profile template server error")

// ProfileTemplate implements profile_update_subscription_template.
type ProfileTemplate struct {
	store        domain.ProfileTemplateStore
	profiles     LoginResponseProfiles
	associations LoginResponseAssociations
	images       domain.ProfileTemplateImages
	choices      domain.RegistrationChoices
	now          func() time.Time
}

func NewProfileTemplate(store domain.ProfileTemplateStore, profiles LoginResponseProfiles, associations LoginResponseAssociations,
	images domain.ProfileTemplateImages, choices domain.RegistrationChoices, now func() time.Time) *ProfileTemplate {
	return &ProfileTemplate{store, profiles, associations, images, choices, now}
}

// Update is the view after authorization and method dispatch.
func (s *ProfileTemplate) Update(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (*dto.ProfileUpdateResponse, error) {
	if dto.ProfileSettingsHasUpload(body) {
		return nil, dto.ErrProfileTemplateNotPorted
	}
	user, role, err := profileMiscRequestUser(identity)
	if err != nil {
		return nil, err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, ErrProfileTemplateServer // request.data['sport_association'] on a list or string
	}
	data, present := object.Lookup("sport_association")
	if !present {
		return nil, ErrProfileTemplateServer // KeyError
	}
	if data != nil {
		fields, ok := data.(domain.RequestObject)
		if !ok {
			return nil, ErrProfileTemplateServer // initial_data['additional_sections'] on a non-dict
		}
		if identity.Actor == nil {
			return nil, domain.ErrInvalidScopedRequest
		}
		if err := s.save(ctx, identity, user, fields, paymentCategoryAuditContext(identity, audit)); err != nil {
			return nil, err
		}
	}
	return s.response(ctx, identity, user, role)
}

func (s *ProfileTemplate) save(ctx context.Context, identity domain.ResolvedIdentity, owner string, data domain.RequestObject, audit domain.LoginAuditContext) error {
	plan, err := dto.PlanProfileTemplate(data)
	if errors.Is(err, dto.ErrProfileTemplatePython) {
		return ErrProfileTemplateServer
	}
	if err != nil {
		return err
	}
	if s.images == nil {
		return ErrLoginImageProcessorUnavailable
	}
	var logo *string
	if plan.Logo != nil {
		compressed, err := s.images.CompressTemplateLogo(ctx, *plan.Logo)
		if err != nil {
			return err
		}
		// The response compresses the stored logo again: an image the processor
		// cannot handle is refused before any write.
		if _, err := s.images.CompressTemplateLogo(ctx, compressed); err != nil {
			return err
		}
		logo = &compressed
	}
	fields := append(plan.Fields, dto.ProfileTemplateField{Name: "logo", Kind: dto.ProfileTemplateText})
	if logo != nil {
		fields[len(fields)-1].Value = *logo
	}
	if err := s.checkAvatar(ctx, owner); err != nil {
		return err
	}

	tx, err := s.store.BeginProfileTemplate(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stored, err := tx.Association(ctx, owner, identity.ImpersonationAssociationID)
	if err != nil {
		return err
	}
	if stored == nil {
		return ErrProfileTemplateServer // SportAssociation.DoesNotExist
	}
	changes := map[string][2]string{}
	for _, field := range fields {
		old, err := profileTemplateStoredAudit(stored, field)
		if err != nil {
			return err
		}
		if next := dto.ProfileTemplateAudit(field); next != old {
			changes[field.Name] = [2]string{old, next}
		}
	}
	if len(changes) > 0 {
		// auditlog's pre_save receiver: written before save() converts values.
		id := stored.ID
		if err := tx.AppendAudit(ctx, domain.ProfileMiscAudit{Model: "sportassociation", Key: id,
			Representation: "SportAssociation object (" + id + ")", Changes: changes, Context: audit, AssociationID: &id,
			ResolutionPath: "self", At: s.now()}); err != nil {
			return err
		}
	}
	columns, convertible := profileTemplateColumns(fields)
	if convertible {
		err = tx.Save(ctx, stored.ID, columns)
		if err != nil && !errors.Is(err, domain.ErrProfileTemplateSave) {
			return err
		}
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	if !convertible || err != nil {
		// A ValidationError or database error inside save(); the request is
		// not atomic, so the audit entry stays.
		return ErrProfileTemplateServer
	}
	return nil
}

// checkAvatar refuses, before any write, an avatar the response could not compress.
func (s *ProfileTemplate) checkAvatar(ctx context.Context, user string) error {
	profile, err := s.profiles.Read(ctx, user)
	if err != nil {
		return err
	}
	if profile != nil && profile.AvatarImage != nil {
		_, err = s.images.CompressLoginImage(ctx, *profile.AvatarImage)
	}
	return err
}

func profileTemplateStoredAudit(stored *domain.ProfileTemplateAssociation, field dto.ProfileTemplateField) (string, error) {
	text := func(value *string) string {
		if value == nil {
			return "None"
		}
		return *value
	}
	switch field.Kind {
	case dto.ProfileTemplateJSON:
		raw := stored.JSON[field.Name]
		if raw == nil {
			return "null", nil
		}
		value, err := dto.ParseProfileTablesJSON([]byte(*raw))
		if err != nil {
			return "", err
		}
		return string(dto.PythonJSONDumpsSorted(value)), nil
	case dto.ProfileTemplateBoolean:
		return dto.ProfileTemplateStr(stored.Boolean[field.Name]), nil
	case dto.ProfileTemplateDecimal:
		return text(stored.Decimal[field.Name]), nil
	}
	return text(stored.Text[field.Name]), nil
}

// profileTemplateColumns converts the in-memory values as save() does; false
// when a field's to_python raises ValidationError.
func profileTemplateColumns(fields []dto.ProfileTemplateField) ([]domain.ProfileTemplateColumn, bool) {
	columns := make([]domain.ProfileTemplateColumn, 0, len(fields))
	for _, field := range fields {
		var value any
		switch field.Kind {
		case dto.ProfileTemplateJSON:
			if field.Value != nil {
				text := string(dto.PythonJSONDumps(field.Value))
				value = &text
			} else {
				value = (*string)(nil)
			}
		case dto.ProfileTemplateBoolean:
			flag, ok := dto.ProfileTemplateBooleanValue(field.Value)
			if !ok {
				return nil, false
			}
			value = flag
		case dto.ProfileTemplateDecimal:
			if field.Value == nil {
				value = (*string)(nil)
				break
			}
			literal, ok := dto.ProfileTemplateDecimalLiteral(field.Value)
			if !ok {
				return nil, false
			}
			value = &literal
		default:
			if field.Value == nil {
				value = (*string)(nil)
			} else {
				text := dto.ProfileTemplateStr(field.Value)
				value = &text
			}
		}
		columns = append(columns, domain.ProfileTemplateColumn{Name: field.Name, Value: value})
	}
	return columns, true
}

// response is the view's content: role, UserAuthSerializer(request.user) with
// the registration features and the compressed avatar, and for an association
// owner the re-read association with its logo compressed at 590x130.
func (s *ProfileTemplate) response(ctx context.Context, identity domain.ResolvedIdentity, user string, role int) (*dto.ProfileUpdateResponse, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	name := "association"
	switch role {
	case -2, 0, 1, 3:
	case -1, 2:
		name = "athlete"
	default:
		return nil, ErrProfileTemplateServer // ROLE_CHOICES[user.role - 1] IndexError
	}
	profile, err := s.profiles.Read(ctx, user)
	if err != nil {
		return nil, err
	}
	if profile == nil || profile.UserID != user {
		return nil, domain.ErrAccountNotFound
	}
	if s.images == nil {
		return nil, ErrLoginImageProcessorUnavailable
	}
	result := &dto.ProfileUpdateResponse{Role: name, UserData: dto.ProfileUpdateUser{UserAuthProfile: *profile}}
	features := make([]dto.RegistrationFeature, 0, 3)
	for _, choice := range []struct {
		enabled bool
		name    string
	}{{s.choices.Family, "Iscrizione per famiglia"}, {s.choices.Federation, "Iscrizioni per Enti e Federazioni"}, {s.choices.Multiple, "Iscrizioni multiple per federazioni"}} {
		if choice.enabled {
			features = append(features, dto.RegistrationFeature{Name: choice.name, Description: ""})
		}
	}
	result.UserData.PreviewAndCustomFeatures = &features
	if avatar := result.UserData.AvatarImage; avatar != nil {
		compressed, err := s.images.CompressLoginImage(ctx, *avatar)
		if err != nil {
			return nil, err
		}
		result.UserData.AvatarImage = &compressed
	}
	if role == domain.RoleAssociation {
		association, err := s.associations.Read(ctx, user)
		if errors.Is(err, domain.ErrAssociationNotFound) {
			return nil, ErrProfileTemplateServer
		}
		if err != nil {
			return nil, err
		}
		if association == nil || association.ID == "" ||
			identity.ImpersonationAssociationID != "" && association.ID != identity.ImpersonationAssociationID {
			return nil, ErrProfileTemplateServer // SportAssociation.DoesNotExist
		}
		copied := *association
		if copied.Logo != nil {
			compressed, err := s.images.CompressTemplateLogo(ctx, *copied.Logo)
			if err != nil {
				return nil, err
			}
			copied.Logo = &compressed
		}
		result.UserData.SportAssociation = &copied
	}
	return result, ctx.Err()
}

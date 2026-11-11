package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ModulesWrites follows modules_add, modules_update, modules_duplicate and
// modules_delete (BE/application/views/modules_views.py).
type ModulesWrites struct {
	store    domain.ModulesWriteStore
	profiles modulesReadsProfiles
	now      func() time.Time
	ids      func() string
}

func NewModulesWrites(store domain.ModulesWriteStore, profiles modulesReadsProfiles, now func() time.Time, ids func() string) *ModulesWrites {
	return &ModulesWrites{store: store, profiles: profiles, now: now, ids: ids}
}

// ModuleValidationError is the serializer's field errors: add renders DRF's
// 400 map, update the {'error': str(ValidationError)} text.
type ModuleValidationError struct {
	Errors dto.ModuleWriteErrors
	// Nested are the nested association serializer's field errors, in its field order.
	Nested []dto.DRFFieldError
}

func (e *ModuleValidationError) Error() string { return "invalid module input" }

// ErrModuleBody is request.data['sport_association'] = ... on a body that is
// not a dict (a list, a scalar or an immutable QueryDict): a 500.
var ErrModuleBody = errors.New("module request data is not a mutable dict")

var errModuleNested = &domain.ModuleUpdateFailure{Status: 500, Text: "The `.update()` method does not support writable nested fields by " +
	"default.\nWrite an explicit `.update()` method for serializer `application.serializers.modules_serializers.ModulesSerializer`, " +
	"or set `read_only=True` on nested serializer fields."}

// association is request.user.sport_association: athletes are the DRF
// PermissionDenied, an owner without an active association DoesNotExist.
func (s *ModulesWrites) association(ctx context.Context, tx domain.ModulesWriteTransaction, identity domain.ResolvedIdentity) (string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return "", &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	return tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
}

func (s *ModulesWrites) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// unique applies the module_id and custom_link UniqueValidators to fields
// that passed their own validation.
func (s *ModulesWrites) unique(ctx context.Context, tx domain.ModulesWriteTransaction, write dto.ModuleWrite, invalid dto.ModuleWriteErrors,
	exclude *string) (dto.ModuleWriteErrors, error) {
	if invalid == nil {
		invalid = dto.ModuleWriteErrors{}
	}
	if write.ID != nil {
		taken, err := tx.Taken(ctx, "module_id", *write.ID, exclude)
		if err != nil {
			return nil, err
		}
		if taken {
			invalid["module_id"] = []string{dto.ModuleUniqueID}
		}
	}
	if link, present := write.Text["custom_link"]; present && link != nil {
		taken, err := tx.Taken(ctx, "custom_link", *link, exclude)
		if err != nil {
			return nil, err
		}
		if taken {
			invalid["custom_link"] = []string{dto.ModuleUniqueLink}
		}
	}
	if len(invalid) == 0 {
		return nil, nil
	}
	return invalid, nil
}

// apply sets the validated fields on the instance (setattr in field order).
func applyModuleWrite(row *domain.ModuleRow, write dto.ModuleWrite) {
	if write.ID != nil {
		row.ID = *write.ID
	}
	texts := map[string]**string{"custom_link": &row.CustomLink, "title": &row.Title, "response_message": &row.ResponseMessage}
	for name, value := range write.Text {
		*texts[name] = value
	}
	if write.Created != nil {
		row.CreationDate = *write.Created
	}
	flags := map[string]*bool{"require_approval": &row.RequireApproval, "always_active": &row.AlwaysActive, "queue_mode": &row.QueueMode,
		"only_users": &row.OnlyUsers, "payment_required": &row.PaymentRequired, "allow_attachments": &row.AllowAttachments}
	for name, value := range write.Booleans {
		*flags[name] = value
	}
	dates := map[string]**string{"start_date": &row.StartDate, "end_date": &row.EndDate}
	for name, value := range write.Dates {
		*dates[name] = value
	}
	if write.MaxPresent {
		row.MaxResponses = write.Max
	}
	documents := map[string]*string{"payment_data": &row.PaymentData, "elements": &row.Elements}
	for name, value := range write.JSON {
		*documents[name] = dto.ModuleJSONText(value)
	}
}

// serialized is ModulesSerializer(module).data with the nested association.
func (s *ModulesWrites) serialized(ctx context.Context, identity domain.ResolvedIdentity, row domain.ModuleRow) (map[string]any, error) {
	profile, err := s.profiles.Read(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	if row.AssociationID == nil || profile.ID != *row.AssociationID {
		return nil, domain.ErrAssociationNotFound
	}
	data := (&ModulesReads{now: s.now}).moduleFields(row)
	data["sport_association"] = profile
	return data, nil
}

// Add follows modules_add: 200 {'msg': 'module created'}.
func (s *ModulesWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) error {
	tx, err := s.store.BeginModulesWrite(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return err
	}
	if _, ok := body.(domain.RequestObject); !ok || form {
		return ErrModuleBody
	}
	write, invalid := dto.ParseModuleWrite(body, false, false)
	if invalid, err = s.unique(ctx, tx, write, invalid, nil); err != nil {
		return err
	}
	if invalid != nil {
		return &ModuleValidationError{Errors: invalid}
	}
	row := domain.ModuleRow{ID: s.ids(), AssociationID: &association, CreationDate: s.at(), PaymentData: "{}", Elements: "{}"}
	applyModuleWrite(&row, write)
	if err = tx.CreateModule(ctx, row, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
		return err
	}
	return tx.Commit()
}

// Update follows modules_update (partial ModulesSerializer).
func (s *ModulesWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool,
	audit domain.LoginAuditContext) (map[string]any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	tx, err := s.store.BeginModulesWrite(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	row, err := tx.Module(ctx, id.String(), nil)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrModuleUpdateNotFound
	}
	// The debug log's extra: module.sport_association.sport_association_id,
	// then request.user.sport_association.sport_association_id.
	if row.AssociationID == nil {
		return nil, &domain.ModuleUpdateFailure{Status: 500, Text: "'NoneType' object has no attribute 'sport_association_id'"}
	}
	association, err := s.association(ctx, tx, identity)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, &domain.ModuleUpdateFailure{Status: 500, Text: "SportAssociation matching query does not exist."}
	}
	if err != nil {
		return nil, err
	}
	if *row.AssociationID != association {
		return nil, &domain.PermissionDenied{Detail: "You are not allowed to update this camp or retreat"}
	}
	write, invalid := dto.ParseModuleWrite(body, form, true)
	var nested []dto.DRFFieldError
	if write.Nested == dto.ModuleNestedFields || write.Nested == dto.ModuleNestedForm {
		if nested, err = dto.ParseModuleNested(write.NestedObject, func(id string) (bool, error) {
			return tx.Taken(ctx, "sport_association_id", id, nil)
		}, write.Nested == dto.ModuleNestedForm); err != nil {
			return nil, err
		}
	}
	if invalid, err = s.unique(ctx, tx, write, invalid, &row.ID); err != nil {
		return nil, err
	}
	if invalid != nil || nested != nil {
		return nil, &ModuleValidationError{Errors: invalid, Nested: nested}
	}
	if write.Nested != dto.ModuleNestedAbsent {
		return nil, errModuleNested
	}
	if write.ID != nil && *write.ID != row.ID {
		// Model.save() under a key no row has inserts a copy with the validated
		// values; the module the path names stays.
		applyModuleWrite(row, write)
		conflict, err := tx.CopyModule(ctx, *row, paymentCategoryAuditContext(identity, audit), s.at())
		if err != nil {
			return nil, err
		}
		if conflict != "" {
			// The view answers str(e) of the database error; the first entry stays.
			if err = tx.Commit(); err != nil {
				return nil, err
			}
			return nil, &domain.ModuleUpdateFailure{Status: 500, Text: conflict}
		}
		data, err := s.serialized(ctx, identity, *row)
		if err != nil {
			return nil, err
		}
		if err = tx.Commit(); err != nil {
			return nil, err
		}
		return map[string]any{"message": "Camp or retreat updated successfully", "camp_and_retreat": data}, nil
	}
	applyModuleWrite(row, write)
	if err = tx.SaveModule(ctx, *row, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
		return nil, err
	}
	data, err := s.serialized(ctx, identity, *row)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"message": "Camp or retreat updated successfully", "camp_and_retreat": data}, nil
}

// Duplicate follows modules_duplicate: 201 {'data': ModulesSerializer(clone)}.
func (s *ModulesWrites) Duplicate(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) (map[string]any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	tx, err := s.store.BeginModulesWrite(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return nil, err
	}
	source, err := tx.Module(ctx, id.String(), &association)
	if err != nil {
		return nil, err
	}
	if source == nil {
		return nil, domain.ErrModuleNotFound
	}
	clone := *source
	title := "None"
	if source.Title != nil {
		title = *source.Title
	}
	title += " (copia)"
	link := s.ids()
	link = link[0:8] + link[9:13]
	clone.ID, clone.Title, clone.CustomLink, clone.CreationDate = s.ids(), &title, &link, s.at()
	if err = tx.CreateModule(ctx, clone, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
		return nil, err
	}
	data, err := s.serialized(ctx, identity, clone)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"data": data}, nil
}

// Delete follows modules_delete: no UUID check before the filter.
func (s *ModulesWrites) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	tx, err := s.store.BeginModulesWrite(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return err
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		// UUIDField.to_python's ValidationError is not handled by DRF (500).
		return errModuleLookup
	}
	row, err := tx.Module(ctx, id.String(), &association)
	if err != nil {
		return err
	}
	if row == nil {
		return domain.ErrModuleNotFound
	}
	if err = tx.DeleteModule(ctx, row.ID, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
		return err
	}
	return tx.Commit()
}

var errModuleLookup = errors.New("module lookup value is not a UUID")

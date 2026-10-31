package service

import (
	"context"
	"encoding/json"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type modulesReadsStore interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	Modules(ctx context.Context, association string) ([]domain.ModuleRow, error)
	Module(ctx context.Context, id, association string) (*domain.ModuleRow, error)
	Responses(ctx context.Context, module string) ([]domain.ModuleResponseRow, error)
	LinkModule(ctx context.Context, link string) (*string, error)
}

type modulesReadsProfiles interface {
	Read(ctx context.Context, owner string) (*dto.AssociationProfile, error)
}

type ModulesReads struct {
	store    modulesReadsStore
	profiles modulesReadsProfiles
	now      func() time.Time
}

func NewModulesReads(store modulesReadsStore, profiles modulesReadsProfiles, now func() time.Time) *ModulesReads {
	return &ModulesReads{store: store, profiles: profiles, now: now}
}

// ErrModuleLinkInvalid is modules_check_link's 400 {'valid': False, ...}.
var ErrModuleLinkInvalid = &moduleLinkInvalid{}

type moduleLinkInvalid struct{}

func (*moduleLinkInvalid) Error() string { return "invalid payload." }

// ErrModuleLinkMissing is query_params['custom_link'] on a missing key (a 500).
var ErrModuleLinkMissing = &moduleLinkMissing{}

type moduleLinkMissing struct{}

func (*moduleLinkMissing) Error() string { return "custom_link missing" }

// association is request.user.sport_association: athletes are
// PermissionDenied, an owner without an active association a 500.
func (s *ModulesReads) association(ctx context.Context, identity domain.ResolvedIdentity) (string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return "", &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	return s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
}

// moduleFields is ModulesSerializer / ModulesSerializerWithResponses without
// sport_association and responses; 'enabled' is Module.enabled on today's UTC date.
func (s *ModulesReads) moduleFields(row domain.ModuleRow) map[string]any {
	enabled := row.AlwaysActive
	if !enabled && row.StartDate != nil && row.EndDate != nil {
		today := s.now().UTC().Format("2006-01-02")
		enabled = *row.StartDate <= today && today <= *row.EndDate
	}
	return map[string]any{
		"module_id": row.ID, "enabled": enabled, "custom_link": row.CustomLink, "creation_date": dto.DRFUTCDateTime(row.CreationDate),
		"title": row.Title, "require_approval": row.RequireApproval, "start_date": row.StartDate, "end_date": row.EndDate,
		"always_active": row.AlwaysActive, "max_responses": row.MaxResponses, "queue_mode": row.QueueMode, "only_users": row.OnlyUsers,
		"payment_required": row.PaymentRequired, "payment_data": json.RawMessage(row.PaymentData), "response_message": row.ResponseMessage,
		"allow_attachments": row.AllowAttachments, "elements": json.RawMessage(row.Elements),
	}
}

// List follows modules_list: the association's modules (unordered), each
// with the nested SportAssociationSerializer of that association.
func (s *ModulesReads) List(ctx context.Context, identity domain.ResolvedIdentity) (map[string]any, error) {
	association, err := s.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.Modules(ctx, association)
	if err != nil {
		return nil, err
	}
	data := make([]map[string]any, 0, len(rows))
	if len(rows) > 0 {
		profile, err := s.profiles.Read(ctx, identity.ScopeUser.ID)
		if err != nil {
			return nil, err
		}
		if profile.ID != association {
			return nil, domain.ErrAssociationNotFound
		}
		for _, row := range rows {
			module := s.moduleFields(row)
			module["sport_association"] = profile
			data = append(data, module)
		}
	}
	return map[string]any{"data": data}, nil
}

// Overview follows modules_overview: request.user.sport_association, then the
// module by id within it (a string the UUIDField rejects is a 500) and
// ModulesSerializerWithResponses.
func (s *ModulesReads) Overview(ctx context.Context, identity domain.ResolvedIdentity, raw string) (map[string]any, error) {
	association, err := s.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		// Django's UUIDField.to_python raises a ValidationError DRF does not handle.
		return nil, err
	}
	row, err := s.store.Module(ctx, id.String(), association)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrModuleNotFound
	}
	responses, err := s.store.Responses(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	items := make([]map[string]any, 0, len(responses))
	for _, response := range responses {
		items = append(items, map[string]any{
			"module_response_id": response.ID, "module": response.ModuleID, "creation_date": dto.DRFUTCDateTime(response.CreationDate),
			"progressive_response_number": response.ProgressiveNumber, "queue_position": response.QueuePosition,
			"response": json.RawMessage(response.Response), "approved": response.Approved, "user": response.UserID,
			"payment": response.PaymentID, "attachments": json.RawMessage(response.Attachments),
			"attachments_docs": json.RawMessage(response.AttachmentsDocs),
		})
	}
	module := s.moduleFields(*row)
	module["sport_association"] = row.AssociationID
	module["responses"] = items
	return map[string]any{"data": module}, nil
}

// CheckLink follows modules_check_link. request.user is never read.
func (s *ModulesReads) CheckLink(ctx context.Context, link *string, header *string) error {
	if link == nil {
		return ErrModuleLinkMissing
	}
	// len() counts code points.
	if utf8.RuneCountInString(*link) > 255 {
		return ErrModuleLinkInvalid
	}
	id, err := s.store.LinkModule(ctx, *link)
	if err != nil {
		return err
	}
	if id != nil && (header == nil || *header != *id) {
		return ErrModuleLinkInvalid
	}
	return nil
}

package service

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// CommunicationsPost ports configuration_send_post (communications/views.py).
type CommunicationsPost struct {
	store domain.MiscRoutesStore
	now   func() time.Time
	ids   func() string
}

func NewCommunicationsPost(store domain.MiscRoutesStore, now func() time.Time, ids func() string) *CommunicationsPost {
	return &CommunicationsPost{store: store, now: now, ids: ids}
}

func miscRoutesBadRequest(body any) error { return &domain.MiscRoutesAnswer{Status: 400, Body: body} }

// communicationsPostTruthy is Python truthiness of a parsed JSON or form value.
func communicationsPostTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		number, err := strconv.ParseFloat(string(v), 64)
		return err != nil || number != 0
	case domain.RequestObject:
		return len(v) > 0
	case []any:
		return len(v) > 0
	}
	return true
}

// communicationsPostMessage is PostSerializer's message CharField(max_length=10000):
// trimmed text, numbers as str(); the validated value or its error list.
func communicationsPostMessage(value any) (string, []string, error) {
	var text string
	switch v := value.(type) {
	case string:
		text = v
	case json.Number:
		raw := string(v)
		text = raw
		if strings.ContainsAny(raw, ".eE") {
			text = dto.PythonStr(v) // str(float): Python repr
		}
	default:
		return "", []string{"Not a valid string."}, nil
	}
	text = dto.PythonStrip(text)
	if text == "" {
		return "", []string{"This field may not be blank."}, nil
	}
	// The field's validators all run: max length, then null characters.
	var invalid []string
	if utf8.RuneCountInString(text) > 10000 {
		invalid = append(invalid, "Ensure this field has no more than 10000 characters.")
	}
	if strings.ContainsRune(text, 0) {
		invalid = append(invalid, "Null characters are not allowed.")
	}
	if invalid != nil {
		return "", invalid, nil
	}
	return text, nil, nil
}

// Post follows configuration_send_post. body is request.data (nil for an
// empty body, a RequestObject for JSON objects and forms).
func (s *CommunicationsPost) Post(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error) {
	data, isObject := body.(domain.RequestObject)
	if body == nil {
		data, isObject = domain.RequestObject{}, true
	}
	if !isObject {
		return nil, domain.ErrMiscRoutesServer // initial_data.get on a non-dict
	}
	raw, _ := data.Lookup("message")
	if !communicationsPostTruthy(raw) {
		return nil, miscRoutesBadRequest([]any{"Message is required"})
	}
	message, invalid, err := communicationsPostMessage(raw)
	if err != nil {
		return nil, err
	}
	if invalid != nil {
		messages := make([]any, len(invalid))
		for i, text := range invalid {
			messages[i] = text
		}
		return nil, miscRoutesBadRequest(domain.RequestObject{{Name: "message", Value: messages}})
	}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	tx, err := s.store.BeginMiscRoutes(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, domain.ErrMiscRoutesServer
	}
	if err != nil {
		return nil, err
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	if err = tx.CreatePost(ctx, s.ids(), s.ids(), association, message, paymentCategoryAuditContext(identity, audit), at); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return domain.RequestObject{{Name: "msg", Value: "post sent."}}, nil
}

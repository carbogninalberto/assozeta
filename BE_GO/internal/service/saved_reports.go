package service

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

var (
	ErrSavedReportRequired = errors.New("name and tool_name are required")
	// ErrSavedReportRaised stands for the views' uncaught exceptions (500).
	ErrSavedReportRaised = errors.New("saved report request raised")
)

// SavedReportUnknownTool is the 400 for a tool outside TOOL_FUNCTIONS.
type SavedReportUnknownTool struct{ Tool string }

func (e *SavedReportUnknownTool) Error() string { return "Unknown tool: " + e.Tool }

type SavedReports struct {
	store domain.SavedReportStore
	now   func() time.Time
}

func NewSavedReports(store domain.SavedReportStore, now func() time.Time) *SavedReports {
	return &SavedReports{store: store, now: now}
}

// scope is _get_sport_association plus created_by=request.user (the scope user).
func (s *SavedReports) scope(ctx context.Context, identity domain.ResolvedIdentity) (string, string, error) {
	association, err := s.store.ActiveAssociationForUser(ctx, identity.ScopeUser.ID)
	return association, identity.ScopeUser.ID, err
}

func (s *SavedReports) List(ctx context.Context, identity domain.ResolvedIdentity) ([]dto.SavedReportSummary, error) {
	association, creator, err := s.scope(ctx, identity)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.SavedReports(ctx, association, creator)
	if err != nil {
		return nil, err
	}
	items := make([]dto.SavedReportSummary, 0, len(rows))
	for _, row := range rows {
		items = append(items, dto.SavedReportSummaryFrom(row))
	}
	return items, nil
}

// find is SavedReport.objects.get(saved_report_id=pk, ...): a pk the UUID
// field rejects raises ValidationError (500), an empty pk (no URL pk) and a
// missing row are the 404.
func (s *SavedReports) find(ctx context.Context, identity domain.ResolvedIdentity, raw string) (domain.SavedReport, error) {
	association, creator, err := s.scope(ctx, identity)
	if err != nil {
		return domain.SavedReport{}, err
	}
	if raw == "" {
		return domain.SavedReport{}, domain.ErrSavedReportNotFound
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return domain.SavedReport{}, ErrSavedReportRaised
	}
	return s.store.SavedReport(ctx, id.String(), association, creator)
}

func (s *SavedReports) Info(ctx context.Context, identity domain.ResolvedIdentity, raw string) (dto.SavedReportDetail, error) {
	row, err := s.find(ctx, identity, raw)
	if err != nil {
		return dto.SavedReportDetail{}, err
	}
	return dto.SavedReportDetail{SavedReportSummary: dto.SavedReportSummaryFrom(row), Params: json.RawMessage(row.Params), UIConfig: json.RawMessage(row.UIConfig)}, nil
}

// Add follows SavedReportViewSet.add: .get(...).strip() on name and tool_name
// (non-strings raise), then raw description (str()), params and ui_config.
func (s *SavedReports) Add(ctx context.Context, identity domain.ResolvedIdentity, body any) (string, string, error) {
	association, creator, err := s.scope(ctx, identity)
	if err != nil {
		return "", "", err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return "", "", ErrSavedReportRaised
	}
	text := func(key string) (string, bool) {
		value, present := object.Lookup(key)
		if !present {
			return "", true
		}
		return dto.PythonStripString(value)
	}
	name, okName := text("name")
	tool, okTool := text("tool_name")
	if !okName || !okTool {
		return "", "", ErrSavedReportRaised
	}
	if name == "" || tool == "" {
		return "", "", ErrSavedReportRequired
	}
	if !dto.SavedReportTools[tool] {
		return "", "", &SavedReportUnknownTool{Tool: tool}
	}
	raw := func(key string, fallback any) (any, bool) {
		value, present := object.Lookup(key)
		if !present {
			return fallback, true
		}
		return value, value != nil
	}
	description, ok1 := raw("description", "")
	params, ok2 := raw("params", domain.RequestObject{})
	config, ok3 := raw("ui_config", domain.RequestObject{})
	if !ok1 || !ok2 || !ok3 {
		// NOT NULL columns reject None.
		return "", "", ErrSavedReportRaised
	}
	id := uuid.NewString()
	if err = s.store.InsertSavedReport(ctx, id, association, creator, domain.SavedReportWrite{Name: name, ToolName: tool,
		Description: dto.PythonStr(description), Params: string(dto.PythonJSONDumps(params)), UIConfig: string(dto.PythonJSONDumps(config)),
		At: s.now().UTC().Truncate(time.Microsecond)}); err != nil {
		return "", "", err
	}
	return id, name, nil
}

// Update follows SavedReportViewSet.update: 'key' in data then raw assignment
// and save(); the response echoes the in-memory (raw) name.
func (s *SavedReports) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any) (string, json.RawMessage, error) {
	row, err := s.find(ctx, identity, raw)
	if err != nil {
		return "", nil, err
	}
	change := domain.SavedReportChange{At: s.now().UTC().Truncate(time.Microsecond)}
	echo := json.RawMessage(dto.PythonJSONDumps(row.Name))
	for _, field := range []struct {
		key  string
		json bool
		into **string
	}{{"name", false, &change.Name}, {"description", false, &change.Description}, {"params", true, &change.Params}, {"ui_config", true, &change.UIConfig}} {
		present, err := dto.PythonContains(body, field.key)
		if err != nil {
			return "", nil, ErrSavedReportRaised
		}
		if !present {
			continue
		}
		value, err := dto.PythonIndex(body, field.key)
		if err != nil || value == nil {
			return "", nil, ErrSavedReportRaised
		}
		text := dto.PythonStr(value)
		if field.json {
			text = string(dto.PythonJSONDumps(value))
		}
		*field.into = &text
		if field.key == "name" {
			echo = json.RawMessage(dto.PythonJSONDumps(value))
		}
	}
	if err = s.store.UpdateSavedReport(ctx, row.ID, change); err != nil {
		return "", nil, err
	}
	return row.ID, echo, nil
}

func (s *SavedReports) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string) error {
	row, err := s.find(ctx, identity, raw)
	if err != nil {
		return err
	}
	return s.store.DeleteSavedReport(ctx, row.ID)
}

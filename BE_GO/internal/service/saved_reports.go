package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"strings"
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

// Run follows SavedReportViewSet.run: the report lookup, the TOOL_FUNCTIONS
// check, the merge of the stored params with request.data['overrides']
// (outside the try: TypeError/AttributeError are uncaught) and the tool call.
// It returns the 200 result; tool exceptions are dto.ErrReportFailed and a
// result with 'error' is *dto.ReportToolError.
func (s *SavedReports) Run(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any) (any, error) {
	association, _, err := s.scope(ctx, identity)
	if err != nil {
		return nil, err
	}
	row, err := s.find(ctx, identity, raw)
	if err != nil {
		return nil, err
	}
	if !dto.SavedReportTools[row.ToolName] {
		return nil, &SavedReportUnknownTool{Tool: row.ToolName}
	}
	overrides := domain.RequestObject{}
	switch data := body.(type) {
	case nil:
	case domain.RequestObject:
		if value, present := data.Lookup("overrides"); present {
			object, ok := value.(domain.RequestObject)
			if !ok {
				return nil, ErrSavedReportRaised // {**params, **overrides}: not a mapping
			}
			overrides = object
		}
	default:
		return nil, ErrSavedReportRaised // .get on a non-dict body
	}
	stored, err := dto.ParseReferenceJSON([]byte(row.Params))
	if err != nil {
		return nil, err
	}
	params, ok := stored.(domain.RequestObject)
	if !ok {
		return nil, ErrSavedReportRaised
	}
	// {**report.params, **overrides}: later keys replace values in place.
	merged := dto.PythonDict(append(append(domain.RequestObject{}, params...), overrides...))
	var timeout int64
	plan, err := dto.PlanReportTool(row.ToolName, merged, association, func() (dto.ReportSettings, error) {
		settings, err := s.reportSettings(ctx)
		timeout = settings.QueryTimeout * 1000
		return settings, err
	})
	if err != nil {
		return nil, err
	}
	if plan.Immediate != nil {
		return nil, plan.Immediate
	}
	if plan.Value != nil {
		return plan.Value, nil
	}
	results, err := s.store.RunReportStatements(ctx, timeout, plan.Statements)
	var failed *domain.ReportQueryError
	if errors.As(err, &failed) {
		return nil, dto.ErrReportFailed
	}
	if err != nil {
		return nil, err
	}
	return plan.Render(results)
}

// reportSettings is effective_integration('ai', decrypt=False): a
// self-hosted instance's stored AI settings, otherwise the environment.
func (s *SavedReports) reportSettings(ctx context.Context) (dto.ReportSettings, error) {
	return loadReportSettings(ctx, s.store)
}

// reportIntegrationStore reads the instance's stored integration settings.
type reportIntegrationStore interface {
	ReportIntegration(ctx context.Context) (bool, *string, error)
}

func loadReportSettings(ctx context.Context, store reportIntegrationStore) (dto.ReportSettings, error) {
	selfHosted, text, err := store.ReportIntegration(ctx)
	if err != nil {
		return dto.ReportSettings{}, err
	}
	if selfHosted && text != nil {
		parsed, err := dto.ParseReferenceJSON([]byte(*text))
		if err != nil {
			return dto.ReportSettings{}, err
		}
		root, ok := parsed.(domain.RequestObject)
		if !ok {
			return dto.ReportSettings{}, dto.ErrReportNotPorted
		}
		if record, present := root.Lookup("ai"); present {
			object, ok := record.(domain.RequestObject)
			if !ok {
				return dto.ReportSettings{}, dto.ErrReportNotPorted
			}
			if value, _ := object.Lookup("settings"); value != nil {
				settings, ok := value.(domain.RequestObject)
				if !ok {
					return dto.ReportSettings{}, dto.ErrReportNotPorted
				}
				number := func(key string) (int64, error) {
					value, present := settings.Lookup(key)
					if !present {
						return 0, dto.ErrReportFailed // KeyError
					}
					parsed, ok := value.(json.Number)
					count, err := strconv.ParseInt(string(parsed), 10, 64)
					if !ok || err != nil || count <= 0 {
						return 0, dto.ErrReportNotPorted
					}
					return count, nil
				}
				maximum, err := number("max_results")
				if err != nil {
					return dto.ReportSettings{}, err
				}
				timeout, err := number("query_timeout")
				return dto.ReportSettings{MaxResults: maximum, QueryTimeout: timeout}, err
			}
		}
	}
	environment := func(name string, fallback int64) (int64, error) {
		text, present := os.LookupEnv(name)
		if !present {
			return fallback, nil
		}
		value, err := strconv.ParseInt(strings.TrimSpace(text), 10, 64)
		if err != nil || value <= 0 {
			return 0, dto.ErrReportNotPorted
		}
		return value, nil
	}
	maximum, err := environment("MCP_AGENT_MAX_RESULTS", 5000)
	if err != nil {
		return dto.ReportSettings{}, err
	}
	timeout, err := environment("MCP_AGENT_QUERY_TIMEOUT", 10)
	return dto.ReportSettings{MaxResults: maximum, QueryTimeout: timeout}, err
}

func (s *SavedReports) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string) error {
	row, err := s.find(ctx, identity, raw)
	if err != nil {
		return err
	}
	return s.store.DeleteSavedReport(ctx, row.ID)
}

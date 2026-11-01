package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrAuditLogNotFound is audit_log_detail's 404 {"error": "Audit log not found"}.
var ErrAuditLogNotFound = errors.New("audit log not found")

type AuditLogs struct{ store domain.AuditLogStore }

func NewAuditLogs(store domain.AuditLogStore) *AuditLogs { return &AuditLogs{store: store} }

// List follows audit_log_list: the owner association (ErrAssociationNotFound),
// the noise-filtered indexed entries, the query[...] filters, sort and
// KTDatatablePagination (meta perpage is always the class page_size 10).
func (s *AuditLogs) List(ctx context.Context, identity domain.ResolvedIdentity, query domain.RequestObject, groupHeader string) (any, error) {
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	param := func(name string) (string, bool, error) {
		value, present := query.Lookup(name)
		text, _ := value.(string)
		if present && !utf8.ValidString(text) {
			// Django decodes malformed UTF-8 with replacement characters; not ported.
			return "", false, dto.ErrAuditNotPorted
		}
		return text, present, nil
	}
	filter := domain.AuditLogQuery{AssociationID: association, OrderColumn: "timestamp", Descending: true}
	empty := false
	values := map[string]string{}
	present := map[string]bool{}
	for _, name := range []string{"query[generalSearch]", "query[action]", "query[model]", "query[actor_id]", "query[object_id]",
		"query[date_from]", "query[date_to]", "sort[field]", "sort[sort]", "minimal", "pagination[page]", "pagination[perpage]"} {
		if values[name], present[name], err = param(name); err != nil {
			return nil, err
		}
	}
	if search := values["query[generalSearch]"]; search != "" {
		filter.Search = &search
	}
	if action := values["query[action]"]; action != "" {
		if number, ok := dto.PythonIntText(action); ok {
			// PositiveSmallIntegerField exact lookups outside 0..32767 are EmptyResultSet.
			if number.Sign() < 0 || number.Cmp(big.NewInt(32767)) > 0 {
				empty = true
			} else {
				value := number.Int64()
				filter.Action = &value
			}
		}
	}
	if models := values["query[model]"]; models != "" {
		for _, name := range strings.Split(models, ",") {
			filter.Models = append(filter.Models, dto.AuditPythonLower(dto.PythonStripText(name)))
		}
	}
	if actor := values["query[actor_id]"]; actor != "" {
		id, err := paymentInfoParseUUID(actor)
		if err != nil {
			return nil, dto.ErrAuditUnhandled // the UUID field's ValidationError is not handled by DRF
		}
		text := id.String()
		filter.ActorID = &text
	}
	if object := values["query[object_id]"]; object != "" {
		filter.ObjectPK = &object
	}
	if from := values["query[date_from]"]; from != "" {
		if moment, ok := dto.PythonStrptimeDate(from); ok {
			filter.From = &moment
		}
	}
	if to := values["query[date_to]"]; to != "" {
		if moment, ok := dto.PythonStrptimeDate(to); ok {
			moment = moment.Add(23*time.Hour + 59*time.Minute + 59*time.Second)
			filter.To = &moment
		}
	}
	sortField := "timestamp"
	if present["sort[field]"] {
		sortField = values["sort[field]"]
	}
	if column := map[string]string{"timestamp": "timestamp", "action": "action", "model": "model"}[sortField]; column != "" {
		filter.OrderColumn = column
	}
	filter.Descending = !(present["sort[sort]"] && values["sort[sort]"] == "asc")
	minimal := present["minimal"] && dto.AuditPythonLower(values["minimal"]) == "true"

	size := int64(10)
	if present["pagination[perpage]"] {
		if number, ok := dto.PythonIntText(values["pagination[perpage]"]); ok && number.Sign() > 0 {
			size = 1 << 62
			if number.IsInt64() && number.Int64() < size {
				size = number.Int64()
			}
		}
	}
	var count int64
	if !empty {
		if count, err = s.store.CountEntries(ctx, filter); err != nil {
			return nil, err
		}
	}
	pages := int64(1)
	if count > 0 {
		pages = (count-1)/size + 1
	}
	number := int64(1)
	if raw := values["pagination[page]"]; raw == "last" {
		number = pages
	} else if raw != "" {
		parsed, ok := dto.PythonIntText(raw)
		if !ok || !parsed.IsInt64() || parsed.Int64() < 1 || parsed.Int64() > pages {
			return nil, dto.ErrDRFInvalidPage
		}
		number = parsed.Int64()
	}
	rows := []domain.AuditLogRow{}
	if !empty {
		if rows, err = s.store.Entries(ctx, filter, size, (number-1)*size); err != nil {
			return nil, err
		}
	}
	resolver := s.resolver(ctx, groupHeader)
	data := make([]any, 0, len(rows))
	for _, row := range rows {
		var item any
		if minimal {
			item, err = s.minimal(row)
		} else {
			item, err = s.full(row, resolver)
		}
		if err != nil {
			return nil, err
		}
		data = append(data, item)
	}
	meta := domain.RequestObject{{Name: "total", Value: auditNumber(count)}, {Name: "page", Value: auditNumber(number)},
		{Name: "pages", Value: auditNumber(pages)}, {Name: "perpage", Value: auditNumber(10)}}
	return domain.RequestObject{{Name: "data", Value: data}, {Name: "meta", Value: meta}}, nil
}

// Detail follows audit_log_detail; rawID matched the URL's <int:log_id>.
func (s *AuditLogs) Detail(ctx context.Context, identity domain.ResolvedIdentity, rawID, groupHeader string) (any, error) {
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	id, ok := new(big.Int).SetString(rawID, 10)
	if !ok || !id.IsInt64() {
		// Out of the integer range Django's lookup is an EmptyResultSet.
		return nil, ErrAuditLogNotFound
	}
	row, err := s.store.IndexedEntry(ctx, association, id.Int64())
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrAuditLogNotFound
	}
	item, err := s.full(*row, s.resolver(ctx, groupHeader))
	if err != nil {
		return nil, err
	}
	return domain.RequestObject{{Name: "data", Value: item}}, nil
}

func auditNumber(value int64) json.Number { return json.Number(strconv.FormatInt(value, 10)) }

func auditOptional(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

func nullable(valid bool, text string) *string {
	if !valid {
		return nil
	}
	return &text
}

// auditLoad is the JSONField from_db_value of the row's JSON columns (Django
// loads every concrete field, serialized_data included).
func auditLoad(row domain.AuditLogRow) (changes, additional any, err error) {
	if changes, err = dto.AuditStoredJSON(nullable(row.Changes.Valid, row.Changes.String)); err != nil {
		return nil, nil, err
	}
	if additional, err = dto.AuditStoredJSON(nullable(row.AdditionalData.Valid, row.AdditionalData.String)); err != nil {
		return nil, nil, err
	}
	if _, err = dto.AuditStoredJSON(nullable(row.SerializedData.Valid, row.SerializedData.String)); err != nil {
		return nil, nil, err
	}
	return changes, additional, nil
}

func auditActorName(row domain.AuditLogRow) any {
	if !row.ActorID.Valid {
		return nil
	}
	if name := dto.PythonStripText(row.ActorFirstName.String + " " + row.ActorLastName.String); name != "" {
		return name
	}
	return row.ActorEmail.String
}

// minimal is AuditLogListMinimalSerializer.
func (s *AuditLogs) minimal(row domain.AuditLogRow) (any, error) {
	if _, _, err := auditLoad(row); err != nil {
		return nil, err
	}
	verbose, err := dto.AuditModelVerboseName(row.Model)
	if err != nil {
		return nil, err
	}
	return domain.RequestObject{{Name: "id", Value: auditNumber(row.ID)}, {Name: "action", Value: auditNumber(row.Action)},
		{Name: "action_label", Value: dto.AuditActionLabel(row.Action)}, {Name: "timestamp", Value: dto.DRFUTCDateTime(row.Timestamp)},
		{Name: "object_pk", Value: row.ObjectPK}, {Name: "object_repr", Value: row.ObjectRepr},
		{Name: "model_verbose_name", Value: verbose}, {Name: "actor_name", Value: auditActorName(row)}}, nil
}

// full is AuditLogEntrySerializer.
func (s *AuditLogs) full(row domain.AuditLogRow, resolver *auditResolver) (any, error) {
	changes, additional, err := auditLoad(row)
	if err != nil {
		return nil, err
	}
	verbose, err := dto.AuditModelVerboseName(row.Model)
	if err != nil {
		return nil, err
	}
	description, err := resolver.describe(row, verbose)
	if err != nil {
		return nil, err
	}
	var actorEmail any = auditOptional(nullable(row.EntryActorEmail.Valid, row.EntryActorEmail.String))
	if row.ActorID.Valid {
		actorEmail = row.ActorEmail.String
	}
	var enriched, display any
	if dto.PythonTruthy(changes) {
		if enriched, err = dto.AuditEnrichChanges(changes, resolver); err != nil {
			return nil, err
		}
		if display, err = dto.AuditDisplayChanges(changes, resolver); err != nil {
			return nil, err
		}
	}
	return domain.RequestObject{{Name: "id", Value: auditNumber(row.ID)}, {Name: "action", Value: auditNumber(row.Action)},
		{Name: "action_label", Value: dto.AuditActionLabel(row.Action)}, {Name: "timestamp", Value: dto.DRFUTCDateTime(row.Timestamp)},
		{Name: "object_pk", Value: row.ObjectPK}, {Name: "object_repr", Value: row.ObjectRepr},
		{Name: "object_description", Value: description}, {Name: "model_name", Value: row.Model},
		{Name: "model_verbose_name", Value: verbose}, {Name: "actor_name", Value: auditActorName(row)},
		{Name: "actor_email", Value: actorEmail},
		{Name: "remote_addr", Value: auditOptional(nullable(row.RemoteAddr.Valid, row.RemoteAddr.String))},
		{Name: "changes", Value: changes}, {Name: "enriched_changes", Value: enriched}, {Name: "display_changes", Value: display},
		{Name: "changes_summary", Value: auditOptional(dto.AuditChangesSummary(row.Action, changes))},
		{Name: "additional_data", Value: additional}}, nil
}

package service

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrBalanceSheetInvalid is {'msg': 'Invalid data.'} (400).
	ErrBalanceSheetInvalid = errors.New("invalid balance sheet data")
	// ErrBalanceSheetMissing is the POST's {'msg': 'No balance sheet found.'} (404).
	ErrBalanceSheetMissing = errors.New("balance sheet not found")
	// ErrBalanceSheetExportNotPorted refuses, before the generation writes,
	// the export inputs Go does not reproduce (xlsxexport's and pdfexport's).
	ErrBalanceSheetExportNotPorted = refusal.New("balance sheet export is not ported")
	// ErrBalanceAccountNotDeletable is {'msg': 'This account is not deletable.'} (403).
	ErrBalanceAccountNotDeletable = errors.New("custom account is not deletable")
	// ErrBalanceAccountPaymentsNotPorted refuses deleting an account that
	// payments reference: the cascade deletes them with their own signals
	// (not ported).
	ErrBalanceAccountPaymentsNotPorted = refusal.New("deleting an account with payments is not ported")
	// errBalanceSheetExportFailed is export_balance_sheet raising after the
	// sheet was saved (500; the request is not atomic).
	errBalanceSheetExportFailed = errors.New("balance sheet export failed after the save")
	// errBalanceSheetPython is an exception the view does not handle (500).
	errBalanceSheetPython = errors.New("balance sheet python error")
)

type BalanceSheetWrites struct {
	store    domain.BalanceSheetWriteStore
	profiles balanceProfiles
	now      func() time.Time
	ids      func() string
}

func NewBalanceSheetWrites(store domain.BalanceSheetWriteStore, profiles balanceProfiles, now func() time.Time, ids func() string) *BalanceSheetWrites {
	return &BalanceSheetWrites{store: store, profiles: profiles, now: now, ids: ids}
}

// BalanceSheetRequest is what balance_sheet reads from the request.
type BalanceSheetRequest struct {
	Method      string
	CurrentDate *string // the last ?currentDate value
	Body        any     // request.data
	Group       string  // X-Group-Id
	Audit       domain.LoginAuditContext
}

func balanceDefaultData() *pyDict {
	return &pyDict{fields: []pyField{
		{"incoming", &pyDict{fields: []pyField{{"generalIncome", &pyList{}}}}},
		{"outgoing", &pyDict{fields: []pyField{{"generalExpenses", &pyList{}}}}},
		{"cash", json.Number("0")}, {"bank", json.Number("0")}, {"other", json.Number("0")}, {"draft", true}, {"version", json.Number("1")},
	}}
}

// pyDumps is json.dumps(value) as Django's JSONField stores it; sorted is
// auditlog's json.dumps(value, sort_keys=True).
func pyDumps(value any, sorted bool) string {
	if sorted {
		return string(dto.PythonJSONDumpsSorted(pyToJSON(value)))
	}
	return string(dto.PythonJSONDumps(pyToJSON(value)))
}

func pyCompact(value any) json.RawMessage {
	return json.RawMessage(dto.PythonJSONDumpsCompact(pyToJSON(value)))
}

func storedSheetData(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	parsed, err := dto.ParseProfileTablesJSON([]byte(*text))
	if err != nil {
		return nil, err
	}
	return pyFromJSON(parsed), nil
}

// currentDate is request.query_params.get('currentDate', datetime.now())
// through strptime('%Y-%m-%d').
func (s *BalanceSheetWrites) currentDate(raw *string) (time.Time, error) {
	if raw == nil {
		now := s.now().UTC()
		return time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC), nil
	}
	value, ok := dto.PythonStrptimeDate(*raw)
	if !ok {
		return time.Time{}, errBalanceSheetPython
	}
	return value, nil
}

// Sheet follows balance_sheet.
func (s *BalanceSheetWrites) Sheet(ctx context.Context, identity domain.ResolvedIdentity, request BalanceSheetRequest) (any, error) {
	user := identity.ScopeUser
	switch user.Role {
	case domain.RoleAssociation:
	case domain.RoleCollaborator:
		return nil, ErrBalanceInfoUnavailable
	default:
		return nil, domain.ErrMessageReadForbidden
	}
	tx, err := s.store.BeginBalanceSheetWrite(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.SheetAssociation(ctx, user.ID)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, ErrBalanceInfoUnavailable
	}
	if err != nil {
		return nil, err
	}
	run := sheetRun{s: s, tx: tx, identity: identity, request: request, association: association,
		audit: paymentCategoryAuditContext(identity, request.Audit), at: s.now().UTC().Truncate(time.Microsecond)}
	var result any
	switch request.Method {
	case "GET", "POST":
		body, isDict := request.Body.(domain.RequestObject)
		if !isDict {
			return nil, errBalanceSheetPython // request.data.get on a non-dict
		}
		format, _ := body.Lookup("format")
		if request.Method == "GET" || format != nil {
			result, err = run.generated(ctx, format)
		} else {
			result, err = run.replaced(ctx, body)
		}
	case "DELETE":
		result, err = run.reset(ctx)
	}
	if err != nil {
		if errors.Is(err, domain.ErrBalanceSheetSaveFailed) || errors.Is(err, errBalanceSheetExportFailed) {
			// auditlog's pre_save entry stays: the request is not atomic.
			if commit := tx.Commit(); commit != nil {
				return nil, commit
			}
		}
		return nil, err
	}
	return result, tx.Commit()
}

type sheetRun struct {
	s           *BalanceSheetWrites
	tx          domain.BalanceSheetWriteTransaction
	identity    domain.ResolvedIdentity
	request     BalanceSheetRequest
	association string
	audit       domain.LoginAuditContext
	at          time.Time
}

func (r *sheetRun) yearRange(ctx context.Context, moment time.Time) (time.Time, time.Time, error) {
	day, month, err := r.tx.StartDate(ctx, r.identity.ScopeUser.ID)
	if err != nil {
		return time.Time{}, time.Time{}, err
	}
	from, to, ok := balanceSheetRange(moment, day, month)
	if !ok {
		return time.Time{}, time.Time{}, errBalanceSheetPython
	}
	return from, to, nil
}

func (r *sheetRun) profile(ctx context.Context) (*dto.AssociationProfile, error) {
	return r.s.profiles.Read(ctx, r.identity.ScopeUser.ID)
}

func pyInt(value int) json.Number { return json.Number(strconv.Itoa(value)) }

func sheetView(data json.RawMessage, created time.Time, year, draft any) map[string]any {
	return map[string]any{"data": data, "creation_date": dto.DRFUTCDateTime(created), "year": year, "draft": draft}
}

// save is Model.save(): auditlog's changed fields, then every column.
func (r *sheetRun) save(ctx context.Context, row domain.BalanceSheetRecord, oldData *string, data any, year any, status int) error {
	changes := map[string][2]string{}
	old, err := storedSheetData(oldData)
	if err != nil {
		return err
	}
	if before, after := pyDumps(old, true), pyDumps(data, true); before != after {
		changes["data"] = [2]string{before, after}
	}
	if before, after := strconv.Itoa(row.StatusFlag), strconv.Itoa(status); before != after {
		changes["status_flag"] = [2]string{before, after}
	}
	if before, after := strconv.Itoa(row.Year), pyStr(year); before != after {
		changes["year"] = [2]string{before, after}
	}
	row.StatusFlag = status
	text := pyDumps(data, false)
	row.Data = &text
	prepared, prepErr := dto.PythonIntPrep(pyToJSON(year))
	if prepErr == nil {
		row.Year = int(prepared)
		if prepared != int64(row.Year) {
			prepErr = errBalanceSheetPython
		}
	}
	if prepErr != nil {
		// IntegerField.get_prep_value raises after the pre_save entry.
		if err = r.tx.AuditSheetUpdate(ctx, row, changes, r.audit, r.at); err != nil {
			return err
		}
		return errors.Join(domain.ErrBalanceSheetSaveFailed, prepErr)
	}
	// json.dumps writes NaN/Infinity, which PostgreSQL rejects: the UPDATE
	// fails after the entry like Django's.
	return r.tx.SaveSheet(ctx, row, changes, r.audit, r.at)
}

// generated is the GET branch (also POST with a non-null format).
func (r *sheetRun) generated(ctx context.Context, format any) (any, error) {
	current, err := r.s.currentDate(r.request.CurrentDate)
	if err != nil {
		return nil, err
	}
	from, to, err := r.yearRange(ctx, current)
	if err != nil {
		return nil, err
	}
	today := r.s.now().UTC()
	lastFrom, _, err := r.yearRange(ctx, time.Date(today.Year(), today.Month(), today.Day(), 0, 0, 0, 0, time.UTC))
	if err != nil {
		return nil, err
	}
	years := []map[string]any{}
	for offset := 5; offset >= 0; offset-- {
		year := lastFrom.Year() - offset
		start := time.Date(year, lastFrom.Month(), lastFrom.Day(), 0, 0, 0, 0, time.UTC)
		if start.Day() != lastFrom.Day() {
			return nil, errBalanceSheetPython // datetime.replace(year=...) on 29 February
		}
		years = append(years, map[string]any{"year": year, "start_date": dto.DRFUTCDateTime(start)})
	}
	sheet, err := r.tx.SheetOfYear(ctx, r.association, from.Year())
	if err != nil {
		return nil, err
	}
	if sheet != nil && sheet.StatusFlag == 2 {
		stored, err := storedSheetData(sheet.Data)
		if err != nil {
			return nil, err
		}
		profile, err := r.profile(ctx)
		if err != nil {
			return nil, err
		}
		return map[string]any{"data": map[string]any{"available_years": years,
			"balance_sheet": sheetView(pyCompact(stored), sheet.CreationDate, sheet.Year, false), "sport_association": profile}}, nil
	}
	exportFormat := ""
	if text, ok := format.(string); ok && (text == "pdf" || text == "excel") {
		exportFormat = text
	}
	var data any = balanceDefaultData()
	if sheet != nil {
		if data, err = storedSheetData(sheet.Data); err != nil {
			return nil, err
		}
	}
	group := supplierGroup(r.request.Group)
	if err = r.generate(ctx, data, from, to.Add(23*time.Hour+59*time.Minute+59*time.Second), group); err != nil {
		return nil, err
	}
	dict, ok := data.(*pyDict)
	if !ok {
		return nil, errBalanceSheetPython // default_bs['bank'] = 0 on a non-dict
	}
	sums := map[string]any{}
	for _, kind := range []struct {
		key  string
		code int
	}{{"bank", 2}, {"cash", 1}, {"other", 3}} {
		balances, err := r.tx.AccountBalances(ctx, r.association, kind.code, to, group)
		if err != nil {
			return nil, err
		}
		var sum any = json.Number("0")
		for _, balance := range balances {
			value, err := strconv.ParseFloat(balance, 64)
			if err != nil {
				return nil, err
			}
			if sum, err = pyAdd(sum, value); err != nil {
				return nil, err
			}
		}
		dict.set(kind.key, sum)
		sums[kind.key] = sum
	}
	var total any = json.Number("0")
	for _, key := range []string{"bank", "cash", "other"} {
		if value, isFloat := sums[key].(float64); isFloat {
			if total, err = pyAdd(total, value); err != nil {
				return nil, err
			}
		}
	}
	dict.set("total", total)
	// export_balance_sheet runs after the save on the same in-memory data:
	// the file is built first so an input Go does not reproduce is
	// refused before any write; a Python error is raised after the save.
	var export *balanceSheetExport
	if exportFormat != "" {
		if export, err = r.export(ctx, dict, from.Year(), exportFormat); err != nil {
			return nil, err
		}
	}
	created := r.at
	status := 1
	if sheet == nil {
		if !pyFinite(data) {
			return nil, errBalanceSheetPython // the INSERT's json is rejected
		}
		text := pyDumps(data, false)
		row := domain.BalanceSheetRecord{ID: r.s.ids(), AssociationID: &r.association, StatusFlag: 1, CreationDate: r.at, Year: from.Year(),
			Data: &text}
		stored, err := r.tx.InsertSheet(ctx, row, r.audit, r.at)
		if err != nil {
			return nil, err
		}
		// The view's save() right after create(): auditlog compares the
		// reloaded row with the in-memory data.
		if err = r.save(ctx, row, stored, data, pyInt(from.Year()), 1); err != nil {
			return nil, err
		}
	} else {
		created, status = sheet.CreationDate, sheet.StatusFlag
		if err = r.save(ctx, *sheet, sheet.Data, data, pyInt(from.Year()), sheet.StatusFlag); err != nil {
			return nil, err
		}
	}
	if export != nil {
		if export.failed {
			return nil, errBalanceSheetExportFailed
		}
		return export.body, nil
	}
	profile, err := r.profile(ctx)
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": map[string]any{"available_years": years,
		"balance_sheet": sheetView(pyCompact(data), created, from.Year(), status == 1), "sport_association": profile}}, nil
}

// generate runs generate_balance_sheet over data in place; its exceptions
// are swallowed by the view.
func (r *sheetRun) generate(ctx context.Context, data any, from, to time.Time, group *string) error {
	payments, err := r.tx.SheetPayments(ctx, r.association, from, to, group)
	if err != nil {
		return err
	}
	items := make([]balanceSheetItem, 0, len(payments))
	for _, payment := range payments {
		item, err := balanceSheetItemOf(payment)
		if err != nil {
			return err
		}
		items = append(items, item)
	}
	generator := &balanceSheetGenerator{data: data, category: func(id string) (*domain.BalanceSheetCategory, error) {
		return r.tx.Category(ctx, id)
	}}
	if err = generator.generate(items); err != nil && !errors.Is(err, errPython) {
		return err
	}
	return generator.failure
}

// replaced is the POST branch without a format.
func (r *sheetRun) replaced(ctx context.Context, body domain.RequestObject) (any, error) {
	if len(body) != 1 || body[0].Name != "balance_sheet" {
		return nil, ErrBalanceSheetInvalid
	}
	submitted := pyFromJSON(body[0].Value)
	draft, err := pyIndex(submitted, "draft")
	if err != nil {
		return nil, errBalanceSheetPython
	}
	sheet, err := r.tx.NewestSheet(ctx, r.association)
	if err != nil {
		return nil, err
	}
	if sheet == nil {
		return nil, ErrBalanceSheetMissing
	}
	year, err := pyIndex(submitted, "year")
	if err != nil {
		return nil, errBalanceSheetPython
	}
	status := 2
	if dto.PythonTruthy(pyToJSON(draft)) {
		status = 1
	}
	if err = r.save(ctx, *sheet, sheet.Data, submitted, year, status); err != nil {
		return nil, err
	}
	profile, err := r.profile(ctx)
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": map[string]any{"balance_sheet": sheetView(pyCompact(submitted), sheet.CreationDate, pyCompact(year), pyCompact(draft)),
		"sport_association": profile}}, nil
}

// reset is the DELETE branch.
func (r *sheetRun) reset(ctx context.Context) (any, error) {
	current, err := r.s.currentDate(r.request.CurrentDate)
	if err != nil {
		return nil, err
	}
	from, _, err := r.yearRange(ctx, current)
	if err != nil {
		return nil, err
	}
	sheet, err := r.tx.SheetOfYear(ctx, r.association, from.Year())
	if err != nil {
		return nil, err
	}
	if sheet == nil {
		return map[string]string{"msg": "No balance sheet found."}, nil
	}
	if err = r.save(ctx, *sheet, sheet.Data, balanceDefaultData(), pyInt(sheet.Year), sheet.StatusFlag); err != nil {
		return nil, err
	}
	return map[string]string{"msg": "Balance sheet refreshed."}, nil
}

type BalanceAccountDeletes struct {
	store    domain.BalanceAccountDeleteStore
	accounts *BalanceAccounts
	now      func() time.Time
}

func NewBalanceAccountDeletes(store domain.BalanceAccountDeleteStore, accounts *BalanceAccounts, now func() time.Time) *BalanceAccountDeletes {
	return &BalanceAccountDeletes{store: store, accounts: accounts, now: now}
}

// Delete follows balance_sheet_accounts_delete.
func (s *BalanceAccountDeletes) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	if _, err := s.accounts.association(ctx, identity); err != nil {
		return err
	}
	tx, err := s.store.BeginBalanceAccountDelete(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return err
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return ErrBalanceAccountMissing
	}
	row, err := tx.AccountForUpdate(ctx, id.String(), association)
	if err != nil {
		return err
	}
	if row == nil {
		return ErrBalanceAccountMissing
	}
	if !row.Editable {
		return ErrBalanceAccountNotDeletable
	}
	paid, err := tx.AccountHasPayments(ctx, row.ID)
	if err != nil {
		return err
	}
	if paid {
		return ErrBalanceAccountPaymentsNotPorted
	}
	if err = tx.DeleteAccount(ctx, row.ID, paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return tx.Commit()
}

package service

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pdfrender"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// ExportAllData ports export_all_data (profile_views.py): a plain openpyxl
// workbook of str() of the association's subscriptions and payments with
// their related rows.
type ExportAllData struct {
	store domain.ExportAllDataStore
	now   func() time.Time
}

func NewExportAllData(store domain.ExportAllDataStore, now func() time.Time) *ExportAllData {
	return &ExportAllData{store: store, now: now}
}

// ExportAllDataResult is the HttpResponse: the workbook and its
// Content-Disposition value (raw header bytes).
type ExportAllDataResult struct {
	Workbook    []byte
	Disposition string
}

// ErrExportAllDataAthlete is request.user.sport_association's
// PermissionDenied('User is not a sport association') for athletes.
var ErrExportAllDataAthlete = errors.New("User is not a sport association")

// Export follows export_all_data: the first association access
// (request.user.sport_association) decides the identity outcome.
func (s *ExportAllData) Export(ctx context.Context, identity domain.ResolvedIdentity, groupHeader string) (*ExportAllDataResult, error) {
	user := identity.ScopeUser
	if user.Role == domain.RoleAthlete {
		return nil, ErrExportAllDataAthlete
	}
	tx, err := s.store.BeginExportAllData(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	owner := user.ID
	if user.Role == domain.RoleCollaborator && user.ConnectedUserID != nil {
		owner = *user.ConnectedUserID
	}
	association, err := tx.Association(ctx, owner)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, domain.ErrExportAllDataServer // SportAssociation.DoesNotExist
	}
	if err != nil {
		return nil, err
	}
	group, scope := supplierGroup(groupHeader), paymentReadsScope(identity)
	subscriptions, err := s.subscriptionSheet(ctx, tx, association.ID, group, scope)
	if err != nil {
		return nil, err
	}
	payments, err := s.paymentSheet(ctx, tx, association.ID, group, scope)
	if err != nil {
		return nil, err
	}
	denomination := "None"
	if association.Denomination != nil {
		denomination = *association.Denomination
	}
	disposition := "attachment; filename=export_" + denomination + ".xlsx"
	if !pdfrender.HeaderSafe(disposition) {
		return nil, domain.ErrExportAllDataServer // BadHeaderError and the values the ASGI server refuses
	}
	workbook, err := xlsxexport.OpenpyxlRowsResponse([]string{"Iscrizioni", "Pagamenti"}, [][][]string{subscriptions, payments}, s.now())
	switch {
	case errors.Is(err, xlsxexport.ErrNotPorted):
		return nil, domain.ErrExportAllDataNotPorted
	case err != nil:
		return nil, domain.ErrExportAllDataServer // IllegalCharacterError in sheet.append
	}
	return &ExportAllDataResult{Workbook: workbook, Disposition: pdfrender.HeaderBytes(disposition)}, nil
}

func exportAllDataHeaders(prefix string, columns []domain.ExportAllDataColumn) []string {
	out := make([]string, len(columns))
	for i, column := range columns {
		out[i] = prefix + column.Name
	}
	return out
}

// exportAllDataLabels is the label list or map of a choice kind.
func exportAllDataLabels(kind string) []string {
	_, labels, _ := strings.Cut(kind, ":")
	return strings.Split(labels, "|")
}

// exportAllDataCell is one column's cell: '-' for None, the choice labels the
// view substitutes, str() of a decoded JSON value, otherwise the stored text.
func exportAllDataCell(column domain.ExportAllDataColumn, value *string) (string, error) {
	switch {
	case strings.HasPrefix(column.Kind, "choice-"):
		if value == nil {
			return "", domain.ErrExportAllDataServer // None - 1: TypeError
		}
		number, err := strconv.ParseInt(*value, 10, 64)
		if err != nil {
			return "", domain.ErrExportAllDataServer
		}
		labels := exportAllDataLabels(column.Kind)
		index := number
		if strings.HasPrefix(column.Kind, "choice-1:") {
			index = number - 1
		}
		if index < 0 {
			index += int64(len(labels)) // Python negative indexing
		}
		if index < 0 || index >= int64(len(labels)) {
			return "", domain.ErrExportAllDataServer // IndexError
		}
		return labels[index], nil
	case strings.HasPrefix(column.Kind, "map:"):
		if value != nil {
			for _, pair := range exportAllDataLabels(column.Kind) {
				if key, label, _ := strings.Cut(pair, "="); key == *value {
					return label, nil
				}
			}
		}
		return "-", nil
	}
	if value == nil {
		return "-", nil
	}
	if column.Kind == "json" {
		decoded, err := dto.ParseReferenceJSON([]byte(*value))
		if err != nil {
			return "", err
		}
		if decoded == nil {
			return "-", nil
		}
		return dto.PythonStr(decoded), nil
	}
	return *value, nil
}

func exportAllDataCells(columns []domain.ExportAllDataColumn, values []*string, row *[]string) error {
	for i, column := range columns {
		cell, err := exportAllDataCell(column, values[i])
		if err != nil {
			return err
		}
		*row = append(*row, cell)
	}
	return nil
}

func exportAllDataDashes(columns []domain.ExportAllDataColumn, row *[]string) {
	for range columns {
		*row = append(*row, "-")
	}
}

func (s *ExportAllData) subscriptionSheet(ctx context.Context, tx domain.ExportAllDataTransaction, association string, group,
	scope *string) ([][]string, error) {
	header := exportAllDataHeaders("", domain.ExportAllDataSubscriptionColumns)
	header = append(header, exportAllDataHeaders("", domain.ExportAllDataMedicalColumns)...)
	header = append(header, exportAllDataHeaders("associate__", domain.ExportAllDataAssociateColumns)...)
	header = append(header, exportAllDataHeaders("tutor__", domain.ExportAllDataAssociateColumns)...)
	sheet := [][]string{header}
	subscriptions, err := tx.Subscriptions(ctx, association, group, scope)
	if err != nil {
		return nil, err
	}
	associates := []string{}
	for _, subscription := range subscriptions {
		if subscription.AssociateID != nil {
			associates = append(associates, *subscription.AssociateID)
		}
	}
	tutors, err := tx.Tutors(ctx, associates, group)
	if err != nil {
		return nil, err
	}
	for _, subscription := range subscriptions {
		row := []string{}
		if err := exportAllDataCells(domain.ExportAllDataSubscriptionColumns, subscription.Subscription, &row); err != nil {
			return nil, err
		}
		if subscription.HasMedical {
			if err := exportAllDataCells(domain.ExportAllDataMedicalColumns, subscription.Medical, &row); err != nil {
				return nil, err
			}
		} else {
			exportAllDataDashes(domain.ExportAllDataMedicalColumns, &row)
		}
		if subscription.AssociateID == nil {
			return nil, domain.ErrExportAllDataServer // getattr(None, field): AttributeError
		}
		if err := exportAllDataCells(domain.ExportAllDataAssociateColumns, subscription.Associate, &row); err != nil {
			return nil, err
		}
		for _, tutor := range tutors[*subscription.AssociateID] {
			if err := exportAllDataCells(domain.ExportAllDataAssociateColumns, tutor, &row); err != nil {
				return nil, err
			}
		}
		sheet = append(sheet, row)
	}
	return sheet, nil
}

var exportAllDataSections = []struct {
	prefix  string
	columns []domain.ExportAllDataColumn
	choices bool
}{
	{"payment_category__", domain.ExportAllDataCategoryColumns, false},
	{"custom_accounts__", domain.ExportAllDataAccountColumns, true},
	{"associate__", domain.ExportAllDataAssociateColumns, false},
	{"supplier__", domain.ExportAllDataSupplierColumns, false},
	{"instructor__", domain.ExportAllDataInstructorColumns, false},
	{"invoice__", domain.ExportAllDataInvoiceColumns, false},
}

func (s *ExportAllData) paymentSheet(ctx context.Context, tx domain.ExportAllDataTransaction, association string, group,
	scope *string) ([][]string, error) {
	header := append(exportAllDataHeaders("", domain.ExportAllDataPaymentColumns), "course_name")
	for _, section := range exportAllDataSections {
		header = append(header, exportAllDataHeaders(section.prefix, section.columns)...)
	}
	sheet := [][]string{header}
	payments, err := tx.Payments(ctx, association, group, scope)
	if err != nil {
		return nil, err
	}
	for _, payment := range payments {
		row := []string{}
		if err := exportAllDataCells(domain.ExportAllDataPaymentColumns, payment.Payment, &row); err != nil {
			return nil, err
		}
		name, err := s.courseName(ctx, tx, payment, group, scope)
		if err != nil {
			return nil, err
		}
		row = append(row, name)
		for i, section := range exportAllDataSections {
			if payment.Related[i] == nil {
				exportAllDataDashes(section.columns, &row)
				continue
			}
			if err := exportAllDataCells(section.columns, payment.Related[i], &row); err != nil {
				return nil, err
			}
		}
		sheet = append(sheet, row)
	}
	return sheet, nil
}

// courseName is the view's get_course: the scoped live course subscription
// (.get), else the scoped live group-aware installment (.get), else
// course['label'] when present; '-' for None, str() otherwise.
func (s *ExportAllData) courseName(ctx context.Context, tx domain.ExportAllDataTransaction, payment domain.ExportAllDataPayment, group,
	scope *string) (string, error) {
	if payment.Subject != 2 {
		return "-", nil
	}
	title := func(course domain.ExportAllDataCourse) string {
		if course.Title == nil {
			return "-"
		}
		return *course.Title
	}
	course, err := tx.CourseSubscription(ctx, payment.ID, scope)
	if err != nil {
		return "", err
	}
	if course.Matches > 1 {
		return "", domain.ErrExportAllDataServer // MultipleObjectsReturned
	}
	if course.Matches == 1 {
		return title(course), nil
	}
	if course, err = tx.CourseInstallment(ctx, payment.ID, group, scope); err != nil {
		return "", err
	}
	if course.Matches > 1 {
		return "", domain.ErrExportAllDataServer
	}
	if course.Matches == 1 {
		return title(course), nil
	}
	if payment.Course == nil {
		return "-", nil
	}
	value, err := dto.ParseReferenceJSON([]byte(*payment.Course))
	if err != nil {
		return "", err
	}
	if !dto.PythonTruthy(value) {
		return "-", nil
	}
	found, err := dto.PythonContains(value, "label")
	if err != nil {
		return "", domain.ErrExportAllDataServer // `in` on a number: TypeError
	}
	if !found {
		return "-", nil
	}
	label, err := dto.PythonIndex(value, "label")
	if err != nil {
		return "", domain.ErrExportAllDataServer // a list or str indexed by 'label': TypeError
	}
	if label == nil {
		return "-", nil
	}
	return dto.PythonStr(label), nil
}

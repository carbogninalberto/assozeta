package service

import (
	"context"
	"errors"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrSupplierForbidden is the views' bare 403 for athletes.
	ErrSupplierForbidden = errors.New("supplier access forbidden")
	// ErrSupplierOrdering is the FieldError 500 for an ordering Django cannot
	// resolve; reverse relations and the owner account's paths are not ported
	// and fail the same way.
	ErrSupplierOrdering = refusal.New("supplier ordering unsupported")
)

// supplierOrderColumns maps the model's field names (and attnames) to columns.
var supplierOrderColumns = map[string]string{
	"supplier_id": "supplier_id", "pk": "supplier_id", "name": "name", "address": "address", "tax_code": "tax_code",
	"sport_association": "sport_association_id", "sport_association_id": "sport_association_id", "vat_number": "vat_number",
	"type": "type", "email": "email", "phone_number": "phone_number", "city": "city", "cap": "cap", "state": "state",
	"country": "country", "nationality": "nationality", "note": "note", "group": "group_id", "group_id": "group_id",
	"deleted": "deleted",
}

// supplierRelationColumns are the columns of the related tables a forward
// path may end on (`group__name`, `sport_association__denomination`). The
// owner account behind `sport_association__user` is not offered: its columns
// are left out on purpose, so those paths stay refused.
var supplierRelationColumns = map[string]map[string]string{
	"group": {"group_id": "group_id", "pk": "group_id", "name": "name", "description": "description",
		"sport_association": "sport_association_id", "sport_association_id": "sport_association_id"},
	"sport_association": {"sport_association_id": "sport_association_id", "pk": "sport_association_id", "user": "user_id", "user_id": "user_id",
		"denomination": "denomination", "address_city": "address_city", "address": "address", "address_cap": "address_cap",
		"tax_code": "tax_code", "email": "email", "phone": "phone", "regulation": "regulation", "demand": "demand",
		"additional_sections": "additional_sections", "logo": "logo", "subscription_fee": "subscription_fee",
		"multiple_subscription_fee": "multiple_subscription_fee", "subscription_fee_plans": "subscription_fee_plans",
		"membership_fee": "membership_fee", "multiple_membership_fee": "multiple_membership_fee",
		"membership_fee_plans": "membership_fee_plans", "custom_subscription_form_data": "custom_subscription_form_data",
		"document_header": "document_header", "invoice_footer": "invoice_footer", "enable_quotes_management": "enable_quotes_management",
		"configuration": "configuration", "reviewed": "reviewed", "deleted": "deleted", "review_url": "review_url",
		"review_url_enabled": "review_url_enabled", "president_first_name": "president_first_name",
		"president_last_name": "president_last_name", "enroll_number": "enroll_number", "federation": "federation", "sport": "sport",
		"enabled_for": "enabled_for", "checkout_info": "checkout_info", "additional_fields": "additional_fields",
		"stripe_available_methods": "stripe_available_methods", "iban": "iban", "website": "website", "abbreviated": "abbreviated",
		"vat_number": "vat_number", "whatsapp": "whatsapp", "imported_from_associami": "imported_from_associami",
		"extra_text_invoices": "extra_text_invoices", "show_regulation_to_members": "show_regulation_to_members",
		"show_regulation_to_both": "show_regulation_to_both", "show_regulation_to_athletes": "show_regulation_to_athletes",
		"show_demand_to_members": "show_demand_to_members", "show_demand_to_both": "show_demand_to_both",
		"show_demand_to_athletes": "show_demand_to_athletes", "invoice_template": "invoice_template",
		"subscription_template": "subscription_template", "notes": "notes"},
}

var supplierRelationTables = map[string][2]string{
	"group":             {"application_group", "group_id"},
	"sport_association": {"application_sportassociation", "sport_association_id"},
}

// supplierRelationOrder resolves `relation__field` to the expression Django's
// join would order by: the related row's column, NULL without a related row.
func supplierRelationOrder(name string) (string, bool) {
	relation, field, found := strings.Cut(name, "__")
	columns, ok := supplierRelationColumns[relation]
	if !found || !ok {
		return "", false
	}
	column, ok := columns[field]
	if !ok {
		return "", false
	}
	table := supplierRelationTables[relation]
	return "(SELECT r." + column + " FROM " + table[0] + " r WHERE r." + table[1] + "=application_supplierandcustomers." + relation + "_id)", true
}

type Suppliers struct{ store domain.SupplierStore }

func NewSuppliers(store domain.SupplierStore) *Suppliers { return &Suppliers{store: store} }

// SupplierListRequest carries the query parameters supplier_list reads.
type SupplierListRequest struct {
	Query       domain.RequestObject
	GroupHeader string
}

func supplierGroup(header string) *string {
	if header == "" {
		return nil
	}
	id, err := paymentInfoParseUUID(header)
	if err != nil {
		return nil
	}
	text := id.String()
	return &text
}

func supplierParam(query domain.RequestObject, name string) (any, bool) { return query.Lookup(name) }

func supplierText(query domain.RequestObject, name string) string {
	value, _ := query.Lookup(name)
	text, _ := value.(string)
	return text
}

// List follows supplier_list: athletes are refused, then the scope user's
// association; `all` returns every live row, otherwise the search, ordering
// and PageNumberPagination apply.
func (s *Suppliers) List(ctx context.Context, identity domain.ResolvedIdentity, request SupplierListRequest) (map[string]any, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, ErrSupplierForbidden
	}
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	query := domain.SupplierQuery{AssociationID: association, GroupID: supplierGroup(request.GroupHeader)}
	if supplierText(request.Query, "all") != "" {
		rows, err := s.store.Suppliers(ctx, query, -1, 0)
		if err != nil {
			return nil, err
		}
		return map[string]any{"data": supplierItems(rows)}, nil
	}
	query.Search = supplierText(request.Query, "query[generalSearch]")
	if field := supplierText(request.Query, "sort[field]"); field != "" {
		if supplierText(request.Query, "sort[sort]") == "desc" {
			field = "-" + field
		}
		if field == "?" {
			query.Random = true
		} else {
			name, descending := strings.CutPrefix(field, "-")
			column, ok := supplierOrderColumns[name]
			if !ok {
				column, ok = supplierRelationOrder(name)
			}
			if !ok {
				return nil, ErrSupplierOrdering
			}
			query.OrderColumn, query.Descending = column, descending
		}
	}
	count, err := s.store.CountSuppliers(ctx, query)
	if err != nil {
		return nil, err
	}
	size, present := supplierParam(request.Query, "pagination[perpage]")
	number, numberPresent := supplierParam(request.Query, "pagination[page]")
	page, err := dto.DRFPageNumber(number, numberPresent, count, dto.DRFPageSize(size, present, 10))
	if err != nil {
		return nil, err
	}
	rows, err := s.store.Suppliers(ctx, query, page.Size, page.Offset())
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": supplierItems(rows),
		"meta": dto.SupplierPageMeta{Total: page.Count, Page: page.Number, Pages: page.Pages, PerPage: page.Size}}, nil
}

// Info follows supplier_info: is_valid_uuid precedes the role check.
func (s *Suppliers) Info(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader string) (map[string]any, error) {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, ErrSupplierForbidden
	}
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	row, err := s.store.Supplier(ctx, association, id, supplierGroup(groupHeader))
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": dto.SupplierItemFrom(row)}, nil
}

func supplierItems(rows []domain.Supplier) []dto.SupplierItem {
	items := make([]dto.SupplierItem, 0, len(rows))
	for _, row := range rows {
		items = append(items, dto.SupplierItemFrom(row))
	}
	return items
}

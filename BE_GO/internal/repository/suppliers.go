package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type Suppliers struct{ db *sqlx.DB }

func NewSuppliers(db *sqlx.DB) *Suppliers { return &Suppliers{db: db} }

var _ domain.SupplierStore = (*Suppliers)(nil)

const supplierColumns = `supplier_id, name, address, tax_code, sport_association_id, vat_number, type, email, phone_number,
 city, cap, state, country, nationality, note, group_id, deleted`

// supplierSearchColumns are the model's CharField/TextField columns, the
// fields filter_by_all_fields searches.
var supplierSearchColumns = []string{"name", "address", "tax_code", "vat_number", "type", "email", "phone_number", "city", "cap",
	"state", "country", "nationality", "note"}

func (r *Suppliers) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, owner, nil, false)
}

// supplierWhere is SoftDeleteGroupAwareManager plus the association and, for
// a search, Django's icontains: UPPER(column::text) LIKE UPPER(pattern) with
// the pattern's backslash, percent and underscore escaped.
func supplierWhere(query domain.SupplierQuery) (string, []any) {
	where := ` FROM application_supplierandcustomers WHERE sport_association_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)`
	args := []any{query.AssociationID, query.GroupID}
	if query.Search != "" {
		escaped := strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(query.Search)
		args = append(args, "%"+escaped+"%")
		terms := make([]string, len(supplierSearchColumns))
		for i, column := range supplierSearchColumns {
			terms[i] = "UPPER(" + column + "::text) LIKE UPPER($3)"
		}
		where += " AND (" + strings.Join(terms, " OR ") + ")"
	}
	return where, args
}

func (r *Suppliers) CountSuppliers(ctx context.Context, query domain.SupplierQuery) (int64, error) {
	where, args := supplierWhere(query)
	var count int64
	err := r.db.GetContext(ctx, &count, "SELECT count(*)"+where, args...)
	return count, err
}

// Suppliers orders only by a column the service resolved from the model's
// fields; the name is never caller text.
func (r *Suppliers) Suppliers(ctx context.Context, query domain.SupplierQuery, limit, offset int64) ([]domain.Supplier, error) {
	where, args := supplierWhere(query)
	statement := "SELECT " + supplierColumns + where
	switch {
	case query.Random:
		statement += " ORDER BY random()"
	case query.OrderColumn != "":
		statement += " ORDER BY " + query.OrderColumn
		if query.Descending {
			statement += " DESC"
		}
	}
	if limit >= 0 {
		statement += " LIMIT " + strconv.FormatInt(limit, 10) + " OFFSET " + strconv.FormatInt(offset, 10)
	}
	rows := []domain.Supplier{}
	err := r.db.SelectContext(ctx, &rows, statement, args...)
	return rows, err
}

func (r *Suppliers) Supplier(ctx context.Context, association, id string, group *string) (domain.Supplier, error) {
	var row domain.Supplier
	err := r.db.GetContext(ctx, &row, "SELECT "+supplierColumns+` FROM application_supplierandcustomers
 WHERE supplier_id=$1 AND sport_association_id=$2 AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3::uuid)`, id, association, group)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrSupplierNotFound
	}
	return row, err
}

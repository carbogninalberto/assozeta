package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// InvoiceViews reads the rows of document_invoice_view and
// document_einvoice_view. Related rows are read as Django's foreign-key
// descriptors read them (the base manager), managers as the view names them.
type InvoiceViews struct {
	MemberDocuments
	db *sqlx.DB
}

func NewInvoiceViews(db *sqlx.DB) *InvoiceViews {
	return &InvoiceViews{MemberDocuments: NewMemberDocuments(db), db: db}
}

var _ domain.InvoiceViewStore = (*InvoiceViews)(nil)

func invoiceViewsFirst(err error) error {
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ErrViewNotFound
	}
	return err
}

func (r *InvoiceViews) Invoice(ctx context.Context, id string, scope domain.RenderScope) (*domain.InvoiceViewInvoice, error) {
	prefix, filter := renderScopeQuery(scope, "i", ` AND i.invoice_id IN (SELECT invoice_id FROM visible_payments WHERE invoice_id IS NOT NULL)`)
	var row struct {
		ID            string    `db:"id"`
		Association   string    `db:"association"`
		ActivityFee   string    `db:"activity_fee"`
		MembershipFee string    `db:"membership_fee"`
		Description   *string   `db:"description"`
		Number        *int64    `db:"number"`
		Cancelled     bool      `db:"cancelled"`
		SelectedTutor *string   `db:"selected_tutor"`
		CreationDate  time.Time `db:"creation_date"`
	}
	err := r.db.GetContext(ctx, &row, prefix+`
 SELECT i.creation_date, i.invoice_id::text AS id, i.sport_association_id::text AS association, i.activity_fee::text AS activity_fee,
 i.membership_fee::text AS membership_fee, i.description, i.number, i.cancelled, i.selected_tutor_id::text AS selected_tutor
 FROM application_invoice i WHERE i.invoice_id=$4::uuid AND NOT i.deleted`+filter, renderScopeArgs(scope, id)...)
	if err != nil {
		return nil, invoiceViewsFirst(err)
	}
	return &domain.InvoiceViewInvoice{ID: row.ID, Association: row.Association, ActivityFee: row.ActivityFee,
		MembershipFee: row.MembershipFee, Description: row.Description, Number: row.Number, Cancelled: row.Cancelled,
		SelectedTutor: row.SelectedTutor, CreationDate: row.CreationDate}, nil
}

func (r *InvoiceViews) Payments(ctx context.Context, invoice string, scope domain.RenderScope) ([]domain.InvoiceViewPayment, error) {
	prefix, filter := renderScopeQuery(scope, "p", ` AND p.payment_id IN (SELECT payment_id FROM visible_payments)`)
	var rows []struct {
		ID                    string     `db:"id"`
		Type                  string     `db:"type"`
		Subject               int64      `db:"subject"`
		Description           *string    `db:"description"`
		Notes                 *string    `db:"notes"`
		PaymentDate           *time.Time `db:"payment_date"`
		CategoryID            *string    `db:"category"`
		MetaPaymentCategories *string    `db:"meta_payment_categories"`
		AssociateID           *string    `db:"associate"`
		SupplierID            *string    `db:"supplier"`
		InstructorID          *string    `db:"instructor"`
		Course                *string    `db:"course"`
		Meta                  *string    `db:"meta"`
	}
	if err := r.db.SelectContext(ctx, &rows, prefix+`
 SELECT p.payment_id::text AS id, p.type, p.subject, p.description, p.notes, p.payment_date,
 p.payment_category_id::text AS category, p.meta_payment_categories::text AS meta_payment_categories,
 p.associate_id::text AS associate, p.supplier_id::text AS supplier, p.instructor_id::text AS instructor,
 p.course::text AS course, p.meta::text AS meta
 FROM application_payment p WHERE p.invoice_id=$4::uuid AND NOT p.deleted`+filter+` LIMIT 21`,
		renderScopeArgs(scope, invoice)...); err != nil {
		return nil, err
	}
	payments := make([]domain.InvoiceViewPayment, len(rows))
	for i, row := range rows {
		payments[i] = domain.InvoiceViewPayment{ID: row.ID, Type: row.Type, Subject: row.Subject, Description: row.Description,
			Notes: row.Notes, PaymentDate: row.PaymentDate, CategoryID: row.CategoryID, MetaPaymentCategories: row.MetaPaymentCategories,
			AssociateID: row.AssociateID, SupplierID: row.SupplierID, InstructorID: row.InstructorID, Course: row.Course, Meta: row.Meta}
	}
	return payments, nil
}

func (r *InvoiceViews) Owner(ctx context.Context, association string) (*domain.InvoiceViewOwner, error) {
	var row struct {
		ExtraText         *string `db:"extra_text_invoices"`
		InvoiceTemplate   *string `db:"invoice_template"`
		EnumerateInvoices bool    `db:"enumerate_invoices"`
		HideCategoryName  bool    `db:"hide_category_name"`
		DefaultCategory   bool    `db:"default_category"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT sa.extra_text_invoices, sa.invoice_template, u.enumerate_invoices, u.hide_category_name,
 u.default_payment_category_id IS NOT NULL AS default_category
 FROM application_sportassociation sa JOIN bakney_user u ON u.user_id=sa.user_id WHERE sa.sport_association_id=$1::uuid`, association)
	if err != nil {
		return nil, invoiceViewsFirst(err)
	}
	owner := domain.InvoiceViewOwner(row)
	return &owner, nil
}

func (r *InvoiceViews) Category(ctx context.Context, id string) (*domain.InvoiceViewCategory, error) {
	var row domain.InvoiceViewCategory
	err := r.db.QueryRowxContext(ctx, `SELECT name, tax_deductible, deleted FROM application_paymentcategory
 WHERE payment_category_id=$1::uuid`, id).Scan(&row.Name, &row.TaxDeductible, &row.Deleted)
	if err != nil {
		return nil, invoiceViewsFirst(err)
	}
	return &row, nil
}

func (r *InvoiceViews) Supplier(ctx context.Context, id string) (*domain.InvoiceViewSupplier, error) {
	var row domain.InvoiceViewSupplier
	err := r.db.QueryRowxContext(ctx, `SELECT name, address, tax_code FROM application_supplierandcustomers
 WHERE supplier_id=$1::uuid`, id).Scan(&row.Name, &row.Address, &row.TaxCode)
	if err != nil {
		return nil, invoiceViewsFirst(err)
	}
	return &row, nil
}

func (r *InvoiceViews) AssociateMinor(ctx context.Context, id string) (bool, error) {
	var minor bool
	err := r.db.GetContext(ctx, &minor, `SELECT is_minor FROM application_associate WHERE associate_id=$1::uuid`, id)
	return minor, invoiceViewsFirst(err)
}

// PaymentSubscription follows Payment.get_subscription: the first live,
// X-Group-Id aware installment's course subscription's subscription; the
// first X-Group-Id aware subscription; the first carnet subscription's; the
// first live course subscription's; the first camp period's subscription's.
// A matched row whose chain has no subscription returns nil.
func (r *InvoiceViews) PaymentSubscription(ctx context.Context, payment string, group *string) (*string, error) {
	queries := []struct {
		sql   string
		group bool
	}{
		{`SELECT cs.subscription_id::text FROM application_coursesubscriptioninstallment n
 JOIN application_coursesubscription cs ON cs.course_subscription_id=n.course_subscription_id
 WHERE n.payment_id=$1::uuid AND NOT n.deleted AND ($2::uuid IS NULL OR n.group_id=$2::uuid)
 ORDER BY n.course_subscription_installment_id LIMIT 1`, true},
		{`SELECT s.subscription_id::text FROM application_subscription s
 WHERE s.payment_id=$1::uuid AND ($2::uuid IS NULL OR s.group_id=$2::uuid) ORDER BY s.subscription_id LIMIT 1`, true},
		{`SELECT c.subscription_id::text FROM application_carnetsubscription c
 WHERE c.payment_id=$1::uuid ORDER BY c.carnet_subscription_id LIMIT 1`, false},
		{`SELECT cs.subscription_id::text FROM application_coursesubscription cs
 WHERE cs.payment_id=$1::uuid AND NOT cs.deleted ORDER BY cs.course_subscription_id LIMIT 1`, false},
		{`SELECT c.subscription_id::text FROM application_campsandretreatssubscriptionperiod p
 JOIN application_campsandretreatssubscription c ON c.camps_and_retreats_subscription_id=p.camps_and_retreats_subscription_id
 WHERE p.payment_id=$1::uuid ORDER BY p.camps_and_retreats_subscription_period_id LIMIT 1`, false},
	}
	for _, query := range queries {
		args := []any{payment}
		if query.group {
			args = append(args, group)
		}
		var subscription *string
		err := r.db.GetContext(ctx, &subscription, query.sql, args...)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		return subscription, err
	}
	return nil, nil
}

func (r *InvoiceViews) Subscription(ctx context.Context, id string) (*domain.InvoiceViewSubscription, error) {
	var row struct {
		ID                string     `db:"id"`
		Start             *time.Time `db:"start_date"`
		End               *time.Time `db:"end_date"`
		Type              int64      `db:"type"`
		Meta              *string    `db:"meta"`
		CustomData        *string    `db:"custom_data"`
		SubscriptionPlans *string    `db:"subscription_fee_plans"`
		MembershipPlans   *string    `db:"membership_fee_plans"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT s.subscription_id::text AS id, s.start_date, s.end_date, s.type, s.meta::text AS meta,
 s.custom_data::text AS custom_data, sa.subscription_fee_plans::text AS subscription_fee_plans,
 sa.membership_fee_plans::text AS membership_fee_plans
 FROM application_subscription s JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 WHERE s.subscription_id=$1::uuid`, id)
	if err != nil {
		return nil, invoiceViewsFirst(err)
	}
	return &domain.InvoiceViewSubscription{ID: row.ID, Start: row.Start, End: row.End, Type: row.Type, Meta: row.Meta,
		CustomData: row.CustomData, SubscriptionPlans: row.SubscriptionPlans, MembershipPlans: row.MembershipPlans}, nil
}

func (r *InvoiceViews) MembershipPeriod(ctx context.Context, subscription *string) (*time.Time, *time.Time, bool, error) {
	var row struct {
		Start *time.Time `db:"start_date"`
		End   *time.Time `db:"end_date"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT start_date, end_date FROM application_subscriptionmembership
 WHERE subscription_id IS NOT DISTINCT FROM $1::uuid ORDER BY subscription_membership_id LIMIT 1`, subscription)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil, false, nil
	}
	if err != nil {
		return nil, nil, false, err
	}
	return row.Start, row.End, true, nil
}

// CourseName follows get_course_carnet_name's lookups: the first course
// subscription (all rows), else the first installment (X-Group-Id aware,
// deleted included), else the first carnet subscription paid by the payment.
func (r *InvoiceViews) CourseName(ctx context.Context, payment string, group *string) (domain.InvoiceViewCourseName, error) {
	queries := []struct {
		sql   string
		group bool
	}{
		{`SELECT c.title, true AS present FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 WHERE cs.payment_id=$1::uuid ORDER BY cs.course_subscription_id LIMIT 1`, false},
		{`SELECT c.title, true AS present FROM application_coursesubscriptioninstallment n
 JOIN application_coursesubscription cs ON cs.course_subscription_id=n.course_subscription_id
 JOIN application_course c ON c.course_id=cs.course_id
 WHERE n.payment_id=$1::uuid AND ($2::uuid IS NULL OR n.group_id=$2::uuid) ORDER BY n.course_subscription_installment_id LIMIT 1`, true},
		{`SELECT k.title, k.carnet_id IS NOT NULL AS present FROM application_carnetsubscription cs
 LEFT JOIN application_carnet k ON k.carnet_id=cs.carnet_id_id
 WHERE cs.payment_id=$1::uuid ORDER BY cs.carnet_subscription_id LIMIT 1`, false},
	}
	for _, query := range queries {
		args := []any{payment}
		if query.group {
			args = append(args, group)
		}
		var row struct {
			Title   *string `db:"title"`
			Present bool    `db:"present"`
		}
		err := r.db.GetContext(ctx, &row, query.sql, args...)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		if err != nil {
			return domain.InvoiceViewCourseName{}, err
		}
		return domain.InvoiceViewCourseName{Found: true, MissingCarnet: !row.Present, Title: row.Title}, nil
	}
	return domain.InvoiceViewCourseName{}, nil
}

func (r *InvoiceViews) CustomerInvoice(ctx context.Context, id string) (*domain.InvoiceViewCustomerInvoice, error) {
	var c domain.InvoiceViewCustomerInvoice
	err := r.db.QueryRowxContext(ctx, `SELECT prefix, number, fiscal_year, assignor_prefix_vat_number, assignor_vat_number,
 assignor_tax_code, assignor_denomination, assignor_fiscal_regime, assignor_address, assignor_postal_code, assignor_city,
 assignor_province, assignor_country, assignor_contact_email, assignor_contact_phone, assignor_fax,
 transferor_prefix_vat_number, transferor_vat_number, transferor_tax_code, transferor_denomination, transferor_fiscal_regime,
 transferor_address, transferor_postal_code, transferor_city, transferor_province, transferor_country, transferor_contact_email,
 transferor_contact_phone, transferor_fax, country_transmitter_prefix, id_transmitter, transmitting_format, document_type,
 currency, transmitting_date, causal, lines::text, payment_condition, payment_modality, payment_expiry_date
 FROM application_customerinvoice WHERE customer_invoice_id=$1::uuid`, id).Scan(
		&c.Prefix, &c.Number, &c.FiscalYear, &c.AssignorPrefixVAT, &c.AssignorVAT, &c.AssignorTaxCode, &c.AssignorDenomination,
		&c.AssignorFiscalRegime, &c.AssignorAddress, &c.AssignorPostalCode, &c.AssignorCity, &c.AssignorProvince, &c.AssignorCountry,
		&c.AssignorEmail, &c.AssignorPhone, &c.AssignorFax, &c.TransferorPrefixVAT, &c.TransferorVAT, &c.TransferorTaxCode,
		&c.TransferorDenomination, &c.TransferorFiscalRegime, &c.TransferorAddress, &c.TransferorPostalCode, &c.TransferorCity,
		&c.TransferorProvince, &c.TransferorCountry, &c.TransferorEmail, &c.TransferorPhone, &c.TransferorFax,
		&c.CountryTransmitterPrefix, &c.IDTransmitter, &c.TransmittingFormat, &c.DocumentType, &c.Currency, &c.TransmittingDate,
		&c.Causal, &c.Lines, &c.PaymentCondition, &c.PaymentModality, &c.PaymentExpiryDate)
	if err != nil {
		return nil, invoiceViewsFirst(err)
	}
	return &c, nil
}

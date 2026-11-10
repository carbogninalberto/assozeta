package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// DocumentViews reads the rows the HTML views of printing_views.py render.
// Related rows are read as Django's foreign-key descriptors read them (the
// base manager: no group, soft-delete or scope filter).
type DocumentViews struct {
	MemberDocuments
	db *sqlx.DB
}

func NewDocumentViews(db *sqlx.DB) *DocumentViews {
	return &DocumentViews{MemberDocuments: NewMemberDocuments(db), db: db}
}

var _ domain.DocumentViewStore = (*DocumentViews)(nil)

const viewAssociationColumns = `sa.sport_association_id::text AS id, sa.user_id::text AS user_id, u.role AS user_role, u.email AS user_email,
 sa.denomination, sa.address_city, sa.address, sa.address_cap, sa.tax_code, sa.logo, sa.regulation, sa.demand,
 sa.additional_sections::text AS additional_sections, sa.additional_fields::text AS additional_fields, sa.document_header,
 sa.invoice_footer, sa.subscription_template, sa.federation, sa.enroll_number, sa.iban, sa.website, sa.abbreviated, sa.vat_number,
 sa.whatsapp, sa.sport, sa.show_regulation_to_members, sa.show_regulation_to_both, sa.show_regulation_to_athletes,
 sa.show_demand_to_members, sa.show_demand_to_both, sa.show_demand_to_athletes
 FROM application_sportassociation sa JOIN bakney_user u ON u.user_id=sa.user_id`

type viewAssociationRow struct {
	ID                   string  `db:"id"`
	UserID               string  `db:"user_id"`
	UserRole             int     `db:"user_role"`
	UserEmail            *string `db:"user_email"`
	Denomination         string  `db:"denomination"`
	AddressCity          *string `db:"address_city"`
	Address              *string `db:"address"`
	AddressCap           *string `db:"address_cap"`
	TaxCode              string  `db:"tax_code"`
	Logo                 *string `db:"logo"`
	Regulation           *string `db:"regulation"`
	Demand               *string `db:"demand"`
	AdditionalSections   *string `db:"additional_sections"`
	AdditionalFields     *string `db:"additional_fields"`
	DocumentHeader       *string `db:"document_header"`
	InvoiceFooter        *string `db:"invoice_footer"`
	SubscriptionTemplate *string `db:"subscription_template"`
	Federation           *string `db:"federation"`
	EnrollNumber         *string `db:"enroll_number"`
	Iban                 *string `db:"iban"`
	Website              *string `db:"website"`
	Abbreviated          *string `db:"abbreviated"`
	VatNumber            *string `db:"vat_number"`
	Whatsapp             *string `db:"whatsapp"`
	Sport                *string `db:"sport"`
	RegulationMembers    bool    `db:"show_regulation_to_members"`
	RegulationBoth       bool    `db:"show_regulation_to_both"`
	RegulationAthletes   bool    `db:"show_regulation_to_athletes"`
	DemandMembers        bool    `db:"show_demand_to_members"`
	DemandBoth           bool    `db:"show_demand_to_both"`
	DemandAthletes       bool    `db:"show_demand_to_athletes"`
}

func (row viewAssociationRow) domain() domain.ViewAssociation {
	return domain.ViewAssociation{ID: row.ID, UserID: row.UserID, UserRole: row.UserRole, UserEmail: row.UserEmail,
		Denomination: row.Denomination, AddressCity: row.AddressCity, Address: row.Address, AddressCap: row.AddressCap,
		TaxCode: row.TaxCode, Logo: row.Logo, Regulation: row.Regulation, Demand: row.Demand,
		AdditionalSections: row.AdditionalSections, AdditionalFields: row.AdditionalFields, DocumentHeader: row.DocumentHeader,
		InvoiceFooter: row.InvoiceFooter, SubscriptionTemplate: row.SubscriptionTemplate, Federation: row.Federation,
		EnrollNumber: row.EnrollNumber, Iban: row.Iban, Website: row.Website, Abbreviated: row.Abbreviated, VatNumber: row.VatNumber,
		Whatsapp: row.Whatsapp, Sport: row.Sport,
		ShowRegulation: [3]bool{row.RegulationMembers, row.RegulationBoth, row.RegulationAthletes},
		ShowDemand:     [3]bool{row.DemandMembers, row.DemandBoth, row.DemandAthletes}}
}

func notFound(err error) error {
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ErrViewNotFound
	}
	return err
}

func (r *DocumentViews) Association(ctx context.Context, id string) (*domain.ViewAssociation, error) {
	var row viewAssociationRow
	if err := r.db.GetContext(ctx, &row, `SELECT `+viewAssociationColumns+` WHERE sa.sport_association_id=$1::uuid`, id); err != nil {
		return nil, notFound(err)
	}
	result := row.domain()
	return &result, nil
}

func (r *DocumentViews) ActiveAssociation(ctx context.Context, id string, scope *string) (*domain.ViewAssociation, error) {
	var row viewAssociationRow
	err := r.db.GetContext(ctx, &row, `SELECT `+viewAssociationColumns+` WHERE sa.sport_association_id=$1::uuid AND NOT sa.deleted
 AND ($2::uuid IS NULL OR sa.sport_association_id=$2::uuid)`, id, scope)
	if err != nil {
		return nil, notFound(err)
	}
	result := row.domain()
	return &result, nil
}

func (r *DocumentViews) AssociationsOfUser(ctx context.Context, user string) ([]domain.ViewAssociation, error) {
	rows := []viewAssociationRow{}
	if err := r.db.SelectContext(ctx, &rows, `SELECT `+viewAssociationColumns+` WHERE sa.user_id=$1::uuid AND NOT sa.deleted LIMIT 21`,
		user); err != nil {
		return nil, err
	}
	result := make([]domain.ViewAssociation, len(rows))
	for i, row := range rows {
		result[i] = row.domain()
	}
	return result, nil
}

const viewAssociateColumns = `associate_id::text AS id, first_name, last_name, sex, tax_code, born_date, born_city, address, address_city,
 address_cap, nationality, nationality_residence, email, phone, phone_2, phone_3, phone_4, phone_label, phone_2_label, phone_3_label,
 phone_4_label, notes FROM application_associate`

type viewAssociateRow struct {
	ID             string     `db:"id"`
	FirstName      *string    `db:"first_name"`
	LastName       *string    `db:"last_name"`
	Sex            string     `db:"sex"`
	TaxCode        *string    `db:"tax_code"`
	BornDate       *time.Time `db:"born_date"`
	BornCity       *string    `db:"born_city"`
	Address        *string    `db:"address"`
	AddressCity    *string    `db:"address_city"`
	AddressCap     *string    `db:"address_cap"`
	Nationality    *string    `db:"nationality"`
	NationalityRes *string    `db:"nationality_residence"`
	Email          *string    `db:"email"`
	Phone          *string    `db:"phone"`
	Phone2         *string    `db:"phone_2"`
	Phone3         *string    `db:"phone_3"`
	Phone4         *string    `db:"phone_4"`
	PhoneLabel     *string    `db:"phone_label"`
	Phone2Label    *string    `db:"phone_2_label"`
	Phone3Label    *string    `db:"phone_3_label"`
	Phone4Label    *string    `db:"phone_4_label"`
	Notes          *string    `db:"notes"`
}

func (r *DocumentViews) Associate(ctx context.Context, id string) (*domain.ViewAssociate, error) {
	var row viewAssociateRow
	if err := r.db.GetContext(ctx, &row, `SELECT `+viewAssociateColumns+` WHERE associate_id=$1::uuid`, id); err != nil {
		return nil, notFound(err)
	}
	result := domain.ViewAssociate(row)
	return &result, nil
}

// MainTutor: tutor_relations.filter(is_primary=True).first(); without one,
// tutor_relations.first() would be promoted and saved (refused).
func (r *DocumentViews) MainTutor(ctx context.Context, associate string) (*domain.ViewAssociate, error) {
	var tutor string
	err := r.db.GetContext(ctx, &tutor, `SELECT tutor_id::text FROM application_associatetutorrelation
 WHERE associate_id=$1::uuid AND is_primary ORDER BY id LIMIT 1`, associate)
	if errors.Is(err, sql.ErrNoRows) {
		var any bool
		if err := r.db.GetContext(ctx, &any, `SELECT EXISTS(SELECT 1 FROM application_associatetutorrelation WHERE associate_id=$1::uuid)`,
			associate); err != nil {
			return nil, err
		}
		if any {
			return nil, domain.ErrViewTutorWrite
		}
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return r.Associate(ctx, tutor)
}

func (r *DocumentViews) Subscription(ctx context.Context, id string, scope domain.RenderScope) (*domain.ViewSubscription, error) {
	prefix, filter := renderScopeQuery(scope, "s", ` AND s.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)`)
	var row struct {
		ID                  string    `db:"id"`
		Association         string    `db:"association"`
		AssociateID         *string   `db:"associate_id"`
		CreationDate        time.Time `db:"creation_date"`
		Type                int64     `db:"type"`
		Role                int64     `db:"role"`
		AdditionalFields    *string   `db:"additional_fields"`
		SignatureURL        *string   `db:"signature_url"`
		SignatureStorageKey *string   `db:"signature_storage_key"`
		HasPayment          bool      `db:"has_payment"`
		PaymentMeta         *string   `db:"payment_meta"`
	}
	err := r.db.GetContext(ctx, &row, prefix+`
 SELECT s.subscription_id::text AS id, s.sport_association_id::text AS association, s.associate_id::text AS associate_id,
        s.creation_date, s.type, s.role, s.additional_fields::text AS additional_fields, s.signature_url, s.signature_storage_key,
        p.payment_id IS NOT NULL AS has_payment, p.meta::text AS payment_meta
 FROM application_subscription s LEFT JOIN application_payment p ON p.payment_id=s.payment_id
 WHERE s.subscription_id=$4::uuid`+filter, renderScopeArgs(scope, id)...)
	if err != nil {
		return nil, notFound(err)
	}
	return &domain.ViewSubscription{ID: row.ID, Association: row.Association, AssociateID: row.AssociateID, CreationDate: row.CreationDate,
		Type: row.Type, Role: row.Role, AdditionalFields: row.AdditionalFields, SignatureURL: row.SignatureURL,
		SignatureStorageKey: row.SignatureStorageKey, HasPayment: row.HasPayment, PaymentMeta: row.PaymentMeta}, nil
}

// CoursesList is ", ".join(course.title) over the live course subscriptions
// (CourseSubscription.objects, select_related('course')), in the database's order.
func (r *DocumentViews) CoursesList(ctx context.Context, subscription string) (string, error) {
	titles := []sql.NullString{}
	if err := r.db.SelectContext(ctx, &titles, `SELECT c.title FROM application_coursesubscription cs
 JOIN application_course c ON c.course_id=cs.course_id WHERE cs.subscription_id=$1::uuid AND NOT cs.deleted`,
		subscription); err != nil {
		return "", err
	}
	joined := ""
	for i, title := range titles {
		if !title.Valid {
			return "", domain.ErrViewCourseTitle
		}
		if i > 0 {
			joined += ", "
		}
		joined += title.String
	}
	return joined, nil
}

func (r *DocumentViews) Compensation(ctx context.Context, id string, scope domain.RenderScope) (*domain.ViewPayment, error) {
	prefix, filter := renderScopeQuery(scope, "p", ` AND p.payment_id IN (SELECT payment_id FROM visible_payments)`)
	var row struct {
		ID           string     `db:"id"`
		Association  *string    `db:"association"`
		InstructorID *string    `db:"instructor_id"`
		SignatureID  *string    `db:"signature_id"`
		Amount       string     `db:"amount"`
		PaymentDate  *time.Time `db:"payment_date"`
		Notes        *string    `db:"notes"`
	}
	err := r.db.GetContext(ctx, &row, prefix+`
 SELECT p.payment_id::text AS id, p.sport_association_id::text AS association, p.instructor_id::text AS instructor_id,
        p.signature_id::text AS signature_id, p.amount::text AS amount, p.payment_date, p.notes
 FROM application_payment p WHERE p.payment_id=$4::uuid AND NOT p.deleted`+filter, renderScopeArgs(scope, id)...)
	if err != nil {
		return nil, notFound(err)
	}
	return &domain.ViewPayment{ID: row.ID, Association: row.Association, InstructorID: row.InstructorID, SignatureID: row.SignatureID,
		Amount: row.Amount, PaymentDate: row.PaymentDate, Notes: row.Notes}, nil
}

func (r *DocumentViews) Instructor(ctx context.Context, id string) (*domain.ViewInstructor, error) {
	var row struct {
		IsVolunteer          bool       `db:"is_volunteer"`
		FirstName            string     `db:"first_name"`
		LastName             string     `db:"last_name"`
		TaxCode              *string    `db:"tax_code"`
		Address              *string    `db:"address"`
		CivicNumber          *string    `db:"civic_number"`
		AddressCity          *string    `db:"address_city"`
		BornDate             *time.Time `db:"born_date"`
		BornCity             *string    `db:"born_city"`
		BornProvince         *string    `db:"born_province"`
		Phone                *string    `db:"phone"`
		Email                *string    `db:"email"`
		StudyTitle           *string    `db:"study_title"`
		Role                 *string    `db:"role"`
		StipulatedContractIn *time.Time `db:"stipulated_contract_in"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT is_volunteer, first_name, last_name, tax_code, address, civic_number, address_city,
 born_date, born_city, born_province, phone, email, study_title, role, stipulated_contract_in
 FROM application_instructor WHERE instructor_id=$1::uuid`, id)
	if err != nil {
		return nil, notFound(err)
	}
	result := domain.ViewInstructor(row)
	return &result, nil
}

func (r *DocumentViews) Signature(ctx context.Context, id string) (string, error) {
	var signature string
	err := r.db.GetContext(ctx, &signature, `SELECT signature FROM application_signature WHERE signature_id=$1::uuid`, id)
	return signature, notFound(err)
}

func (r *DocumentViews) Hours(ctx context.Context, payment string) ([]domain.ViewHours, error) {
	rows := []struct {
		CompensationType string    `db:"compensation_type"`
		Date             time.Time `db:"date"`
		Period           *string   `db:"period"`
	}{}
	if err := r.db.SelectContext(ctx, &rows, `SELECT compensation_type, date, period FROM application_instructorhours
 WHERE payment_id=$1::uuid`, payment); err != nil {
		return nil, err
	}
	result := make([]domain.ViewHours, len(rows))
	for i, row := range rows {
		result[i] = domain.ViewHours(row)
	}
	return result, nil
}

func (r *DocumentViews) Template(ctx context.Context, id string) (*domain.ViewTemplate, error) {
	var row struct {
		Association        string  `db:"association"`
		Template           *string `db:"template"`
		Footer             bool    `db:"footer"`
		Header             bool    `db:"header"`
		CustomFooterHeader bool    `db:"custom_footer_header"`
		CustomFooter       *string `db:"custom_footer"`
		CustomHeader       *string `db:"custom_header"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT sport_association_id::text AS association, template, footer, header, custom_footer_header,
 custom_footer, custom_header FROM application_sportassociationmoduletemplates WHERE sport_association_module_templates_id=$1::uuid`, id)
	if err != nil {
		return nil, notFound(err)
	}
	result := domain.ViewTemplate(row)
	return &result, nil
}

func (r *DocumentViews) Appointment(ctx context.Context, id string) (*domain.ViewAppointment, error) {
	var row struct {
		Subscription string     `db:"subscription"`
		Region       string     `db:"region"`
		Date         *time.Time `db:"date"`
		Sport        *string    `db:"sport"`
		Meta         *string    `db:"meta"`
		Association  string     `db:"association"`
		AssociateID  *string    `db:"associate_id"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT m.subscription_id::text AS subscription, m.region, m.date, m.sport, m.meta::text AS meta, s.sport_association_id::text AS association,
 s.associate_id::text AS associate_id FROM application_medicalappointments m
 JOIN application_subscription s ON s.subscription_id=m.subscription_id
 JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 WHERE m.medical_appointments_id=$1::uuid ORDER BY m.medical_appointments_id LIMIT 1`, id)
	if err != nil {
		return nil, notFound(err)
	}
	result := domain.ViewAppointment(row)
	return &result, nil
}

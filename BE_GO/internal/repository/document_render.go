package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// DocumentRender reads the rows the PDF views of printing_views.py name and
// writes their effects, each write committed on its own (Django autocommit).
type DocumentRender struct {
	MemberDocuments
	db *sqlx.DB
}

func NewDocumentRender(db *sqlx.DB) *DocumentRender {
	return &DocumentRender{MemberDocuments: NewMemberDocuments(db), db: db}
}

var _ domain.DocumentRenderStore = (*DocumentRender)(nil)

// scopeFilter is scoped_queryset plus the group-aware manager for a table
// aliased alias, with the athlete family boundary through familyScopeCTE.
// Parameters: $1 association, $2 effective person, $3 group, then the caller's.
func renderScopeQuery(scope domain.RenderScope, alias string, athlete string) (prefix, filter string) {
	prefix = `WITH scope_parameters AS (SELECT $1::uuid association_id, $2::uuid effective_id, $3::uuid group_id)`
	filter = ` AND ($3::uuid IS NULL OR ` + alias + `.group_id=$3) AND ($1::uuid IS NULL OR ` + alias + `.sport_association_id=$1)`
	if scope.Association != nil && scope.Athlete {
		prefix += familyScopeCTE
		filter += athlete
	}
	return prefix, filter
}

func renderScopeArgs(scope domain.RenderScope, args ...any) []any {
	var effective any
	if scope.Effective != "" {
		effective = scope.Effective
	}
	return append([]any{scope.Association, effective, scope.Group}, args...)
}

// documentRenderTokenRecords are the base-manager lookups of the
// HasDocumentToken classes (table, uid column, document column).
var documentRenderTokenRecords = map[domain.RenderTokenRecord]string{
	{Table: "application_invoice", Key: "invoice_id", Document: "document_pdf_id"}: `SELECT EXISTS(SELECT 1 FROM docmanager_document d
 WHERE d.token=$2::uuid AND d.document_id IN (SELECT r.document_pdf_id FROM application_invoice r WHERE r.invoice_id=$1::uuid))`,
	{Table: "application_customerinvoice", Key: "customer_invoice_id", Document: "pdf_id"}: `SELECT EXISTS(SELECT 1 FROM docmanager_document d
 WHERE d.token=$2::uuid AND d.document_id IN (SELECT r.pdf_id FROM application_customerinvoice r WHERE r.customer_invoice_id=$1::uuid))`,
	{Table: "application_instructorhours", Key: "payment_id", Document: "document_id"}: `SELECT EXISTS(SELECT 1 FROM docmanager_document d
 WHERE d.token=$2::uuid AND d.document_id IN (SELECT r.document_id FROM application_instructorhours r WHERE r.payment_id=$1::uuid))`,
	{Table: "application_medicalappointments", Key: "medical_appointments_id", Document: "document_id"}: `SELECT EXISTS(SELECT 1 FROM docmanager_document d
 WHERE d.token=$2::uuid AND d.document_id IN (SELECT r.document_id FROM application_medicalappointments r WHERE r.medical_appointments_id=$1::uuid))`,
}

func (r *DocumentRender) RecordDocumentToken(ctx context.Context, record domain.RenderTokenRecord, uid, token string) (bool, error) {
	query, known := documentRenderTokenRecords[record]
	if !known {
		return false, nil
	}
	var found bool
	err := r.db.GetContext(ctx, &found, query, uid, token)
	return found, err
}

// ActorAssociation is SportAssociation.objects.filter(user=actor).first().
func (r *DocumentRender) ActorAssociation(ctx context.Context, user string) (*string, error) {
	var id string
	err := r.db.GetContext(ctx, &id, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1::uuid AND NOT deleted ORDER BY sport_association_id LIMIT 1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}

// OwnedAssociations is SportAssociation.objects.filter(user=user)[:21] (get()).
func (r *DocumentRender) OwnedAssociations(ctx context.Context, user string) ([]string, error) {
	ids := []string{}
	err := r.db.SelectContext(ctx, &ids, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1::uuid AND NOT deleted LIMIT 21`, user)
	return ids, err
}

func (r *DocumentRender) Subscription(ctx context.Context, id string, scope domain.RenderScope) (*domain.RenderSubscription, error) {
	prefix, filter := renderScopeQuery(scope, "s", ` AND s.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)`)
	var row struct {
		ID           string  `db:"id"`
		Association  string  `db:"association"`
		Denomination *string `db:"denomination"`
		HasAssociate bool    `db:"has_associate"`
		First        *string `db:"first_name"`
		Last         *string `db:"last_name"`
		Document     *string `db:"document"`
	}
	err := r.db.GetContext(ctx, &row, prefix+`
 SELECT s.subscription_id::text AS id, s.sport_association_id::text AS association, sa.denomination,
        s.associate_id IS NOT NULL AS has_associate, a.first_name, a.last_name, s.document_pdf_id::text AS document
 FROM application_subscription s JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.subscription_id=$4::uuid`+filter, renderScopeArgs(scope, id)...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrRenderNotFound
	}
	if err != nil {
		return nil, err
	}
	return &domain.RenderSubscription{ID: row.ID, Association: row.Association, Denomination: row.Denomination, HasAssociate: row.HasAssociate,
		First: row.First, Last: row.Last, Document: row.Document}, nil
}

func (r *DocumentRender) Invoice(ctx context.Context, id string, scope domain.RenderScope) (*domain.RenderInvoice, error) {
	prefix, filter := renderScopeQuery(scope, "i", ` AND i.invoice_id IN (SELECT invoice_id FROM visible_payments WHERE invoice_id IS NOT NULL)`)
	var row struct {
		ID           string  `db:"id"`
		Association  string  `db:"association"`
		Denomination *string `db:"denomination"`
		Document     *string `db:"document"`
	}
	err := r.db.GetContext(ctx, &row, prefix+`
 SELECT i.invoice_id::text AS id, i.sport_association_id::text AS association, sa.denomination, i.document_pdf_id::text AS document
 FROM application_invoice i JOIN application_sportassociation sa ON sa.sport_association_id=i.sport_association_id
 WHERE i.invoice_id=$4::uuid AND NOT i.deleted`+filter, renderScopeArgs(scope, id)...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrRenderNotFound
	}
	if err != nil {
		return nil, err
	}
	return &domain.RenderInvoice{ID: row.ID, Association: row.Association, Denomination: row.Denomination, Document: row.Document}, nil
}

type renderPaymentRow struct {
	ID              string     `db:"id"`
	Association     string     `db:"association"`
	Denomination    *string    `db:"denomination"`
	Amount          string     `db:"amount"`
	Date            *time.Time `db:"payment_date"`
	HasAssociate    bool       `db:"has_associate"`
	AssociateFirst  *string    `db:"associate_first"`
	AssociateLast   *string    `db:"associate_last"`
	AssociateEmail  *string    `db:"associate_email"`
	HasInstructor   bool       `db:"has_instructor"`
	InstructorFirst *string    `db:"instructor_first"`
	InstructorLast  *string    `db:"instructor_last"`
	InstructorUser  *string    `db:"instructor_user"`
}

const renderPaymentColumns = `p.payment_id::text AS id, p.sport_association_id::text AS association, sa.denomination,
 p.amount::text AS amount, p.payment_date, p.associate_id IS NOT NULL AS has_associate, a.first_name AS associate_first,
 a.last_name AS associate_last, a.email AS associate_email, p.instructor_id IS NOT NULL AS has_instructor,
 ins.first_name AS instructor_first, ins.last_name AS instructor_last, ins.user_id::text AS instructor_user
 FROM application_payment p JOIN application_sportassociation sa ON sa.sport_association_id=p.sport_association_id
 LEFT JOIN application_associate a ON a.associate_id=p.associate_id
 LEFT JOIN application_instructor ins ON ins.instructor_id=p.instructor_id`

func (row renderPaymentRow) domain() domain.RenderPayment {
	text := func(value *string) string {
		if value == nil {
			return ""
		}
		return *value
	}
	return domain.RenderPayment{ID: row.ID, Association: row.Association, Denomination: row.Denomination, Amount: row.Amount, Date: row.Date,
		HasAssociate: row.HasAssociate, AssociateFirst: row.AssociateFirst, AssociateLast: row.AssociateLast, AssociateEmail: row.AssociateEmail,
		HasInstructor: row.HasInstructor, InstructorFirst: text(row.InstructorFirst), InstructorLast: text(row.InstructorLast),
		InstructorOwnerUser: text(row.InstructorUser)}
}

// InvoicePayments is scoped_queryset(Payment.objects).filter(invoice=invoice)[:21].
func (r *DocumentRender) InvoicePayments(ctx context.Context, invoice string, scope domain.RenderScope) ([]domain.RenderPayment, error) {
	prefix, filter := renderScopeQuery(scope, "p", ` AND p.payment_id IN (SELECT payment_id FROM visible_payments)`)
	rows := []renderPaymentRow{}
	if err := r.db.SelectContext(ctx, &rows, prefix+` SELECT `+renderPaymentColumns+`
 WHERE p.invoice_id=$4::uuid AND NOT p.deleted`+filter+` LIMIT 21`, renderScopeArgs(scope, invoice)...); err != nil {
		return nil, err
	}
	payments := make([]domain.RenderPayment, len(rows))
	for index, row := range rows {
		payments[index] = row.domain()
	}
	return payments, nil
}

func (r *DocumentRender) Compensation(ctx context.Context, id string, scope domain.RenderScope) (*domain.RenderPayment, error) {
	prefix, filter := renderScopeQuery(scope, "p", ` AND p.payment_id IN (SELECT payment_id FROM visible_payments)`)
	var row renderPaymentRow
	err := r.db.GetContext(ctx, &row, prefix+` SELECT `+renderPaymentColumns+` WHERE p.payment_id=$4::uuid AND NOT p.deleted`+filter,
		renderScopeArgs(scope, id)...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrRenderNotFound
	}
	if err != nil {
		return nil, err
	}
	payment := row.domain()
	return &payment, nil
}

// CompensationHours is InstructorHours.objects.filter(payment=payment), in
// the database's order (Django iterates the same unordered query).
func (r *DocumentRender) CompensationHours(ctx context.Context, payment string) ([]domain.RenderHours, error) {
	rows := []struct {
		ID         string  `db:"id"`
		Instructor string  `db:"instructor"`
		Document   *string `db:"document"`
	}{}
	if err := r.db.SelectContext(ctx, &rows, `SELECT instructor_hours_id::text AS id, instructor_id::text AS instructor, document_id::text AS document
 FROM application_instructorhours WHERE payment_id=$1::uuid`, payment); err != nil {
		return nil, err
	}
	hours := make([]domain.RenderHours, len(rows))
	for index, row := range rows {
		hours[index] = domain.RenderHours{ID: row.ID, Instructor: row.Instructor, Document: row.Document}
		var association string
		err := r.db.GetContext(ctx, &association, instructorOwnerAssociation, row.Instructor)
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			return nil, err
		}
		if err == nil {
			hours[index].OwnerAssoc = &association
		}
	}
	return hours, nil
}

func (r *DocumentRender) CustomerInvoice(ctx context.Context, id string) (*domain.RenderCustomerInvoice, error) {
	var row struct {
		ID           string  `db:"id"`
		Association  string  `db:"association"`
		Denomination *string `db:"denomination"`
		Prefix       *string `db:"prefix"`
		Number       *int64  `db:"number"`
		Year         *int64  `db:"fiscal_year"`
		Document     *string `db:"document"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT c.customer_invoice_id::text AS id, c.sport_association_id::text AS association, sa.denomination,
 c.prefix, c.number, c.fiscal_year, c.pdf_id::text AS document
 FROM application_customerinvoice c JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id
 WHERE c.customer_invoice_id=$1::uuid`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrRenderNotFound
	}
	if err != nil {
		return nil, err
	}
	return &domain.RenderCustomerInvoice{ID: row.ID, Association: row.Association, Denomination: row.Denomination, Prefix: row.Prefix,
		Number: row.Number, Year: row.Year, Document: row.Document}, nil
}

func (r *DocumentRender) Template(ctx context.Context, id, association string) (*domain.RenderTemplate, error) {
	var row struct {
		ID           string  `db:"id"`
		Association  string  `db:"association"`
		Denomination *string `db:"denomination"`
		Name         string  `db:"name"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT t.sport_association_module_templates_id::text AS id, t.sport_association_id::text AS association,
 sa.denomination, t.name FROM application_sportassociationmoduletemplates t
 JOIN application_sportassociation sa ON sa.sport_association_id=t.sport_association_id
 WHERE t.sport_association_module_templates_id=$1::uuid AND t.sport_association_id=$2::uuid`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrRenderNotFound
	}
	if err != nil {
		return nil, err
	}
	return &domain.RenderTemplate{ID: row.ID, Association: row.Association, Denomination: row.Denomination, Name: row.Name}, nil
}

func (r *DocumentRender) FolderExists(ctx context.Context, id int64) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_folder WHERE id=$1)`, id)
	return found, err
}

// Appointment is MedicalAppointments.objects.filter(pk=id).select_related(...).first().
func (r *DocumentRender) Appointment(ctx context.Context, id string) (*domain.RenderAppointment, error) {
	var row struct {
		ID           string  `db:"id"`
		Subscription string  `db:"subscription"`
		Association  string  `db:"association"`
		Denomination *string `db:"denomination"`
		Region       string  `db:"region"`
		Date         *string `db:"date"`
		Document     *string `db:"document"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT m.medical_appointments_id::text AS id, m.subscription_id::text AS subscription,
 s.sport_association_id::text AS association, sa.denomination,
 m.region, to_char(m.date, 'YYYY-MM-DD') AS date, m.document_id::text AS document
 FROM application_medicalappointments m JOIN application_subscription s ON s.subscription_id=m.subscription_id
 JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 WHERE m.medical_appointments_id=$1::uuid ORDER BY m.medical_appointments_id LIMIT 1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrRenderNotFound
	}
	if err != nil {
		return nil, err
	}
	return &domain.RenderAppointment{ID: row.ID, Subscription: row.Subscription, Association: row.Association, Denomination: row.Denomination, Region: row.Region,
		Date: row.Date, Document: row.Document}, nil
}

// AppointmentForm reads the rows document_medical_appointment hands
// extract_values for a PDF form: the appointment, its subscription's
// association with the owner's e-mail, and the subscription's associate.
func (r *DocumentRender) AppointmentForm(ctx context.Context, id string) (*domain.RenderAppointmentForm, error) {
	var row struct {
		Associate          *string   `db:"associate"`
		Created            time.Time `db:"created"`
		Address            *string   `db:"associate_address"`
		Denomination       *string   `db:"sa_denomination"`
		Federation         *string   `db:"sa_federation"`
		AssociationAddress *string   `db:"sa_address"`
		AddressCap         *string   `db:"sa_address_cap"`
		AddressCity        *string   `db:"sa_address_city"`
		Whatsapp           *string   `db:"sa_whatsapp"`
		PresidentFirst     *string   `db:"sa_president_first_name"`
		PresidentLast      *string   `db:"sa_president_last_name"`
		Signature          *string   `db:"sa_president_signature"`
		Stamp              *string   `db:"sa_stamp"`
		Email              *string   `db:"user_email"`
		LastName           *string   `db:"a_last_name"`
		FirstName          *string   `db:"a_first_name"`
		BornCity           *string   `db:"a_born_city"`
		BornDate           *string   `db:"a_born_date"`
		City               *string   `db:"a_address_city"`
		IDNumber           *string   `db:"a_id_number"`
		TaxCode            *string   `db:"a_tax_code"`
		Sport              *string   `db:"m_sport"`
		Place              *string   `db:"m_address"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT s.associate_id::text AS associate, m.creation_date AS created, a.address AS associate_address,
 sa.denomination AS sa_denomination, sa.federation AS sa_federation, sa.address AS sa_address, sa.address_cap AS sa_address_cap,
 sa.address_city AS sa_address_city, sa.whatsapp AS sa_whatsapp, sa.president_first_name AS sa_president_first_name,
 sa.president_last_name AS sa_president_last_name, sa.president_signature AS sa_president_signature, sa.stamp AS sa_stamp,
 u.email AS user_email, a.last_name AS a_last_name, a.first_name AS a_first_name, a.born_city AS a_born_city,
 to_char(a.born_date, 'YYYY-MM-DD') AS a_born_date, a.address_city AS a_address_city, a.id_number AS a_id_number,
 a.tax_code AS a_tax_code, m.sport AS m_sport, m.address AS m_address
 FROM application_medicalappointments m JOIN application_subscription s ON s.subscription_id=m.subscription_id
 JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 LEFT JOIN bakney_user u ON u.user_id=sa.user_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE m.medical_appointments_id=$1::uuid ORDER BY m.medical_appointments_id LIMIT 1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrRenderNotFound
	}
	if err != nil {
		return nil, err
	}
	return &domain.RenderAppointmentForm{Associate: row.Associate, Address: row.Address, Created: row.Created, Text: map[string]*string{
		"sport_association.denomination": row.Denomination, "sport_association.federation": row.Federation,
		"sport_association.address": row.AssociationAddress, "sport_association.address_cap": row.AddressCap,
		"sport_association.address_city": row.AddressCity, "sport_association.whatsapp": row.Whatsapp,
		"sport_association.president_first_name": row.PresidentFirst, "sport_association.president_last_name": row.PresidentLast,
		"sport_association.president_signature": row.Signature, "sport_association.stamp": row.Stamp,
		"sport_association.user.email": row.Email, "associate.last_name": row.LastName, "associate.first_name": row.FirstName,
		"associate.born_city": row.BornCity, "associate.born_date": row.BornDate, "associate.address_city": row.City,
		"associate.id_number": row.IDNumber, "associate.tax_code": row.TaxCode, "medical_appointment.sport": row.Sport,
		"other.address": row.Place}}, nil
}

// AppointmentFormTutor: tutor_relations.filter(is_primary=True).first().tutor;
// without a primary relation, tutor_relations.first() would be promoted and
// saved (ErrViewTutorWrite).
func (r *DocumentRender) AppointmentFormTutor(ctx context.Context, associate string) (*domain.RenderFormTutor, error) {
	var tutor domain.RenderFormTutor
	var row struct {
		Phone *string `db:"phone"`
		Email *string `db:"email"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT t.phone, t.email FROM application_associatetutorrelation r
 JOIN application_associate t ON t.associate_id=r.tutor_id
 WHERE r.associate_id=$1::uuid AND r.is_primary ORDER BY r.id LIMIT 1`, associate)
	if errors.Is(err, sql.ErrNoRows) {
		var related bool
		if err := r.db.GetContext(ctx, &related, `SELECT EXISTS(SELECT 1 FROM application_associatetutorrelation WHERE associate_id=$1::uuid)`,
			associate); err != nil {
			return nil, err
		}
		if related {
			return nil, domain.ErrViewTutorWrite
		}
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	tutor.Phone, tutor.Email = row.Phone, row.Email
	return &tutor, nil
}

func (r *DocumentRender) write(ctx context.Context, apply func(tx *sqlx.Tx) error) error {
	owned, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	if err := apply(owned.tx); err != nil {
		_ = owned.Rollback()
		return err
	}
	return owned.Commit()
}

// CreateDocument is Document.objects.create(filename=...) with auditlog's
// create entry (no resolver: indexed to the actor's own association).
func (r *DocumentRender) CreateDocument(ctx context.Context, document domain.NewRenderDocument, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	return r.write(ctx, func(tx *sqlx.Tx) error {
		if _, err := tx.ExecContext(ctx, `INSERT INTO docmanager_document (document_id, creation_date, filepath, filename, file_size_bytes, token)
 VALUES ($1, $2, NULL, $3, NULL, $4)`, document.ID, document.Created, document.Filename, document.Token); err != nil {
			return err
		}
		changes, err := createdModelChanges(ctx, tx, "docmanager_document", "document_id", document.ID)
		if err != nil {
			return err
		}
		for name, rendered := range documentReverse {
			changes[name] = [2]string{"None", rendered}
		}
		return appendModelAuditForApp(ctx, tx, "docmanager", documentAudit(document.ID, 0, changes, actorAssociation, audit, at))
	})
}

// CreateArchive is SportAssociationDocumentsArchive.objects.create(...): the
// entry renders the in-memory timezone.now() default of the date field.
func (r *DocumentRender) CreateArchive(ctx context.Context, id, association, document string, folder int64, created time.Time,
	audit domain.LoginAuditContext, at time.Time) error {
	return r.write(ctx, func(tx *sqlx.Tx) error {
		if _, err := tx.ExecContext(ctx, `INSERT INTO application_sportassociationdocumentsarchive
 (sport_association_documents_archive_id, sport_association_id, document_id, date, folder_id) VALUES ($1, $2, $3, $4::date, $5)`,
			id, association, document, created.UTC().Format("2006-01-02"), folder); err != nil {
			return err
		}
		changes := map[string][2]string{"sport_association_documents_archive_id": {"None", id}, "sport_association": {"None", association},
			"document": {"None", document}, "date": {"None", pythonAwareUTC(created)}, "folder": {"None", folderText(&folder)}}
		return appendModelAudit(ctx, tx, archiveAudit(id, 0, changes, association, "direct", audit, at))
	})
}

func (r *DocumentRender) CreateSubscriptionFile(ctx context.Context, id string, subscription domain.RenderSubscription, document string,
	created time.Time, audit domain.LoginAuditContext, at time.Time) error {
	return r.write(ctx, func(tx *sqlx.Tx) error {
		if _, err := tx.ExecContext(ctx, `INSERT INTO application_subscriptionfile (subscription_file_id, subscription_id, document_id, creation_date)
 VALUES ($1, $2, $3, $4)`, id, subscription.ID, document, created); err != nil {
			return err
		}
		changes, err := createdModelChanges(ctx, tx, "application_subscriptionfile", "subscription_file_id", id)
		if err != nil {
			return err
		}
		path := "subscription.sport_association"
		return appendModelAudit(ctx, tx, modelAuditEntry{Model: "subscriptionfile", Key: id, Representation: "SubscriptionFile object (" + id + ")",
			Action: 0, Changes: changes, Context: audit, AssociationID: &subscription.Association, ResolutionPath: &path, At: at})
	})
}

// Link saves the row's document foreign key: auditlog's update entry (only
// the changed field) then the write.
func (r *DocumentRender) SubscriptionSaveSeason(ctx context.Context, subscription string) (domain.SubscriptionSaveSeason, error) {
	return modelHooksSubscriptionSeason(ctx, r.db, subscription)
}

func (r *DocumentRender) Link(ctx context.Context, link domain.RenderLink, document string, audit domain.LoginAuditContext, at time.Time) error {
	return r.write(ctx, func(tx *sqlx.Tx) error {
		old := "None"
		if link.Old != nil {
			old = *link.Old
		}
		if old != document {
			entry := modelAuditEntry{Model: link.Model, Key: link.ID, Representation: link.Representation + " object (" + link.ID + ")",
				Action: 1, Changes: map[string][2]string{link.Field: {old, document}}, Context: audit, At: at}
			if link.IndexAssociation != nil {
				path := link.IndexPath
				entry.AssociationID, entry.ResolutionPath = link.IndexAssociation, &path
			}
			if err := appendModelAudit(ctx, tx, entry); err != nil {
				return err
			}
		}
		_, err := tx.ExecContext(ctx, `UPDATE `+link.Table+` SET `+link.Column+`=$2::uuid WHERE `+link.KeyColumn+`=$1::uuid`, link.ID, document)
		return err
	})
}

package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SubscriptionMedical struct{ db *sqlx.DB }

func NewSubscriptionMedical(db *sqlx.DB) *SubscriptionMedical { return &SubscriptionMedical{db: db} }

var _ domain.SubscriptionMedicalStore = (*SubscriptionMedical)(nil)

type subscriptionMedicalTransaction struct{ *associateDraftTransaction }

func (r *SubscriptionMedical) BeginSubscriptionMedical(ctx context.Context) (domain.SubscriptionMedicalTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionMedicalTransaction{associateDraftTransaction: &associateDraftTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

func (t *subscriptionMedicalTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

// The certificate's audit entries resolve no association of their own:
// auditlog falls back to the actor's association, when there is one.
func medicalCertificateAudit(id string, action int, changes map[string][2]string, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) modelAuditEntry {
	return modelAuditEntry{Model: "medicalcertificate", Key: id, Representation: "MedicalCertificate object (" + id + ")", Action: action,
		Changes: changes, Context: audit, AssociationID: actorAssociation, ResolutionPath: associateDraftActorPath(actorAssociation), At: at}
}

func (t *subscriptionMedicalTransaction) AppointmentSubscription(ctx context.Context, id, association string, impersonation, group *string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_subscription WHERE subscription_id=$1 AND sport_association_id=$2
 AND ($3::uuid IS NULL OR sport_association_id=$3::uuid) AND ($4::uuid IS NULL OR group_id=$4::uuid))`, id, association, impersonation, group)
	return found, err
}

func (t *subscriptionMedicalTransaction) Appointments(ctx context.Context, subscription string) ([]domain.MedicalAppointmentRow, error) {
	rows := []struct {
		ID           string    `db:"id"`
		Subscription string    `db:"subscription"`
		Region       string    `db:"region"`
		Document     *string   `db:"document"`
		Token        *string   `db:"token"`
		Date         *string   `db:"date"`
		Notes        *string   `db:"notes"`
		Sport        *string   `db:"sport"`
		Address      *string   `db:"address"`
		Meta         *string   `db:"meta"`
		Created      time.Time `db:"created"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT a.medical_appointments_id::text AS id, a.subscription_id::text AS subscription, a.region,
 a.document_id::text AS document, d.token::text AS token, to_char(a.date, 'YYYY-MM-DD') AS date, a.notes, a.sport, a.address,
 a.meta::text AS meta, a.creation_date AS created
 FROM application_medicalappointments a LEFT JOIN docmanager_document d ON d.document_id=a.document_id
 WHERE a.subscription_id=$1 ORDER BY a.date DESC`, subscription); err != nil {
		return nil, err
	}
	result := make([]domain.MedicalAppointmentRow, 0, len(rows))
	for _, row := range rows {
		item := domain.MedicalAppointmentRow{ID: row.ID, Subscription: row.Subscription, Region: row.Region, Document: row.Document,
			Token: row.Token, Date: row.Date, Notes: row.Notes, Sport: row.Sport, Address: row.Address, Created: row.Created}
		if row.Meta != nil {
			item.Meta = []byte(*row.Meta)
		}
		result = append(result, item)
	}
	return result, nil
}

func (t *subscriptionMedicalTransaction) LockAppointment(ctx context.Context, id, subscription, association string) (bool, error) {
	var found string
	err := t.tx.GetContext(ctx, &found, `SELECT a.medical_appointments_id::text FROM application_medicalappointments a
 JOIN application_subscription s ON s.subscription_id=a.subscription_id
 WHERE a.medical_appointments_id=$1 AND a.subscription_id=$2 AND s.sport_association_id=$3 FOR UPDATE OF a`, id, subscription, association)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	return err == nil, err
}

func (t *subscriptionMedicalTransaction) DeleteAppointment(ctx context.Context, id string, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) error {
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_medicalappointments", "medical_appointments_id", id)
	if err != nil {
		return err
	}
	// The deleted row resolves nothing and its changes name no association: the actor's.
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "medicalappointments", Key: id,
		Representation: "MedicalAppointments object (" + id + ")", Action: 2, Changes: changes, Context: audit,
		AssociationID: actorAssociation, ResolutionPath: associateDraftActorPath(actorAssociation), At: at})
}

// medicalTargetFilter is _medical_certificate_targets as SQL on alias s,
// with its parameters numbered from next.
func medicalTargetFilter(scope domain.MedicalTargetScope, next int) (string, []any) {
	switch {
	case scope.Impersonation != nil:
		return fmt.Sprintf(`s.sport_association_id=$%d`, next), []any{*scope.Impersonation}
	case scope.Athlete:
		return fmt.Sprintf(`s.user_id=$%d`, next), []any{scope.User}
	}
	return fmt.Sprintf(`s.sport_association_id IN (SELECT sport_association_id FROM application_sportassociation WHERE user_id=$%d AND NOT deleted)`,
		next), []any{scope.User}
}

const medicalCertificateColumns = `c.medical_id::text AS id, c.document_id::text AS document, c.user_id::text AS "user",
 to_char(c.expiration_date, 'YYYY-MM-DD') AS expiration, c.competitive_medical_certificate AS competitive, c.notes`

type medicalCertificateScan struct {
	ID          string  `db:"id"`
	Document    *string `db:"document"`
	User        *string `db:"user"`
	Expiration  *string `db:"expiration"`
	Competitive bool    `db:"competitive"`
	Notes       *string `db:"notes"`
}

func (row medicalCertificateScan) certificate() *domain.MedicalCertificateRow {
	return &domain.MedicalCertificateRow{ID: row.ID, Document: row.Document, User: row.User, Expiration: row.Expiration,
		Competitive: row.Competitive, Notes: row.Notes}
}

func (t *subscriptionMedicalTransaction) Subscription(ctx context.Context, id string, scope domain.MedicalTargetScope) (*domain.MedicalSubscription, error) {
	filter, args := medicalTargetFilter(scope, 3)
	var row struct {
		Association string       `db:"association"`
		Medical     *string      `db:"medical"`
		Created     time.Time    `db:"created"`
		Start       sql.NullTime `db:"startdate"`
		End         sql.NullTime `db:"enddate"`
		StartDay    int          `db:"startday"`
		StartMonth  int          `db:"startmonth"`
		EndDay      int          `db:"endday"`
		EndMonth    int          `db:"endmonth"`
		CustomEnd   bool         `db:"customend"`
		Duration    int          `db:"duration"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT s.sport_association_id::text AS association, s.medical_id::text AS medical, s.creation_date AS created,
 s.start_date AS startdate, s.end_date AS enddate, u.subscription_start_day AS startday, u.subscription_start_month AS startmonth,
 u.subscription_end_day AS endday, u.subscription_end_month AS endmonth, u.custom_end_date AS customend, u.subscription_duration AS duration
 FROM application_subscription s
 JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=a.user_id
 WHERE s.subscription_id=$1 AND ($2::uuid IS NULL OR s.group_id=$2::uuid) AND `+filter+`
 ORDER BY s.subscription_id LIMIT 1 FOR UPDATE OF s`, append([]any{id, scope.Group}, args...)...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	date := func(d sql.NullTime) *domain.CalendarDate {
		if !d.Valid {
			return nil
		}
		y, m, day := d.Time.Date()
		return &domain.CalendarDate{Year: y, Month: int(m), Day: day}
	}
	result := &domain.MedicalSubscription{ID: id, Association: row.Association, Target: domain.SubscriptionTagTarget{Created: row.Created.UTC(),
		Dates: domain.SubscriptionDates{Start: date(row.Start), End: date(row.End)},
		Rule: domain.SubscriptionDateRule{Duration: row.Duration, Season: domain.CalendarRule{StartDay: row.StartDay, StartMonth: row.StartMonth,
			CustomEnd: row.CustomEnd, EndDay: row.EndDay, EndMonth: row.EndMonth}}}}
	if row.Medical != nil {
		var certificate medicalCertificateScan
		if err = t.tx.GetContext(ctx, &certificate, `SELECT `+medicalCertificateColumns+` FROM application_medicalcertificate c
 WHERE c.medical_id=$1 FOR UPDATE`, *row.Medical); err != nil {
			return nil, err
		}
		result.Certificate = certificate.certificate()
	}
	return result, nil
}

func (t *subscriptionMedicalTransaction) Draft(ctx context.Context, id string, scope domain.MedicalTargetScope) (*domain.MedicalDraft, error) {
	if scope.Athlete && scope.Impersonation == nil {
		return nil, nil // drafts.none()
	}
	filter, args := medicalTargetFilter(scope, 2)
	var row struct {
		Association string  `db:"association"`
		Data        *string `db:"data"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT s.sport_association_id::text AS association, s.data::text AS data
 FROM application_associateimportdraft s WHERE s.associate_import_draft_id=$1 AND `+filter+`
 ORDER BY s.associate_import_draft_id LIMIT 1 FOR UPDATE`, append([]any{id}, args...)...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	result := &domain.MedicalDraft{ID: id, Association: row.Association}
	if row.Data != nil {
		result.Data = []byte(*row.Data)
	}
	return result, nil
}

func (t *subscriptionMedicalTransaction) Certificate(ctx context.Context, id, user string) (*domain.MedicalCertificateRow, error) {
	var row medicalCertificateScan
	err := t.tx.GetContext(ctx, &row, `SELECT `+medicalCertificateColumns+` FROM application_medicalcertificate c
 WHERE c.medical_id=$1 AND c.user_id=$2 FOR UPDATE`, id, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return row.certificate(), nil
}

func (t *subscriptionMedicalTransaction) SharedElsewhere(ctx context.Context, certificate, association string, group *string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_subscription WHERE medical_id=$1 AND sport_association_id<>$2
 AND ($3::uuid IS NULL OR group_id=$3::uuid))`, certificate, association, group)
	return found, err
}

func (t *subscriptionMedicalTransaction) ForeignReference(ctx context.Context, certificate, association string) (bool, error) {
	var found bool
	// exclude(sport_association=...) keeps drafts without an association.
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_subscription WHERE medical_id=$1 AND sport_association_id<>$2)
 OR EXISTS(SELECT 1 FROM application_associateimportdraft WHERE sport_association_id IS DISTINCT FROM $2::uuid
 AND data #> '{medical_certificate,medical_id}' = to_jsonb($1::text))`, certificate, association)
	return found, err
}

func (t *subscriptionMedicalTransaction) CopyCertificate(ctx context.Context, id string, source domain.MedicalCertificateRow, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_medicalcertificate
 (medical_id, document_id, user_id, creation_date, expiration_date, competitive_medical_certificate, notes)
 VALUES ($1, $2::uuid, $3::uuid, $4, $5::date, $6, $7)`, id, source.Document, source.User, at, source.Expiration, source.Competitive,
		source.Notes); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_medicalcertificate", "medical_id", id)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, medicalCertificateAudit(id, 0, changes, actorAssociation, audit, at))
}

func (t *subscriptionMedicalTransaction) CreateCertificate(ctx context.Context, id, user string, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	return t.associateDraftTransaction.CreateCertificate(ctx, id, user, actorAssociation, audit, at)
}

func optionalText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func (t *subscriptionMedicalTransaction) SetSubscriptionMedical(ctx context.Context, subscription, association, old string, medical *string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET medical_id=$2::uuid WHERE subscription_id=$1`, subscription,
		medical); err != nil {
		return err
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "subscription", Key: subscription,
		Representation: "Subscription object (" + subscription + ")", Action: 1,
		Changes: map[string][2]string{"medical": {old, optionalText(medical)}}, Context: audit, AssociationID: &association,
		ResolutionPath: &path, At: at})
}

func (t *subscriptionMedicalTransaction) SubscriptionMemberNames(ctx context.Context, subscription string) (first, last *string, found bool,
	err error) {
	var row struct {
		First *string `db:"first"`
		Last  *string `db:"last"`
	}
	err = t.tx.GetContext(ctx, &row, `SELECT a.first_name AS first, a.last_name AS last FROM application_subscription s
 JOIN application_associate a ON a.associate_id=s.associate_id WHERE s.subscription_id=$1`, subscription)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil, false, nil
	}
	return row.First, row.Last, err == nil, err
}

func (t *subscriptionMedicalTransaction) CreatePlaceholderDocument(ctx context.Context, document domain.NewDocument, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	return insertPlaceholderDocument(ctx, t.tx, document, actorAssociation, audit, at)
}

func (t *subscriptionMedicalTransaction) AttachNewCertificate(ctx context.Context, subscription domain.MedicalSubscription, certificate string,
	dates domain.SubscriptionDates, audit domain.LoginAuditContext, at time.Time) error {
	changes := map[string][2]string{"medical": {"None", certificate}}
	if before, after := calendarText(subscription.Target.Dates.Start), calendarText(dates.Start); before != after {
		changes["start_date"] = [2]string{before, after}
	}
	if before, after := calendarText(subscription.Target.Dates.End), calendarText(dates.End); before != after {
		changes["end_date"] = [2]string{before, after}
	}
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET medical_id=$2::uuid, start_date=$3::date, end_date=$4::date
 WHERE subscription_id=$1`, subscription.ID, certificate, calendarValue(dates.Start), calendarValue(dates.End)); err != nil {
		return err
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "subscription", Key: subscription.ID,
		Representation: "Subscription object (" + subscription.ID + ")", Action: 1, Changes: changes, Context: audit,
		AssociationID: &subscription.Association, ResolutionPath: &path, At: at})
}

func (t *subscriptionMedicalTransaction) SavePlaceholderCertificate(ctx context.Context, certificate, document, expiration string,
	actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_medicalcertificate SET document_id=$2::uuid, expiration_date=$3::date
 WHERE medical_id=$1`, certificate, document, expiration); err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, medicalCertificateAudit(certificate, 1, map[string][2]string{"document": {"None", document},
		"expiration_date": {"None", expiration + " 00:00:00+00:00"}}, actorAssociation, audit, at))
}

func (t *subscriptionMedicalTransaction) DetachSubscription(ctx context.Context, subscription domain.MedicalSubscription, dates domain.SubscriptionDates,
	audit domain.LoginAuditContext, at time.Time) error {
	changes := map[string][2]string{}
	if subscription.Certificate != nil {
		changes["medical"] = [2]string{subscription.Certificate.ID, "None"}
	}
	if before, after := calendarText(subscription.Target.Dates.Start), calendarText(dates.Start); before != after {
		changes["start_date"] = [2]string{before, after}
	}
	if before, after := calendarText(subscription.Target.Dates.End), calendarText(dates.End); before != after {
		changes["end_date"] = [2]string{before, after}
	}
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET medical_id=NULL, start_date=$2::date, end_date=$3::date
 WHERE subscription_id=$1`, subscription.ID, calendarValue(dates.Start), calendarValue(dates.End)); err != nil {
		return err
	}
	if len(changes) == 0 {
		return nil
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "subscription", Key: subscription.ID,
		Representation: "Subscription object (" + subscription.ID + ")", Action: 1, Changes: changes, Context: audit,
		AssociationID: &subscription.Association, ResolutionPath: &path, At: at})
}

func (t *subscriptionMedicalTransaction) SaveCertificate(ctx context.Context, old domain.MedicalCertificateRow, notes *string, competitive bool,
	actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_medicalcertificate SET notes=$2, competitive_medical_certificate=$3 WHERE medical_id=$1`,
		old.ID, notes, competitive); err != nil {
		return err
	}
	changes := map[string][2]string{}
	// smart_str() renders None as 'None' on both sides.
	if before, after := optionalText(old.Notes), optionalText(notes); before != after {
		changes["notes"] = [2]string{before, after}
	}
	if old.Competitive != competitive {
		changes["competitive_medical_certificate"] = [2]string{pythonBoolText(old.Competitive), pythonBoolText(competitive)}
	}
	if len(changes) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, medicalCertificateAudit(old.ID, 1, changes, actorAssociation, audit, at))
}

func pythonBoolText(value bool) string {
	if value {
		return "True"
	}
	return "False"
}

func (t *subscriptionMedicalTransaction) SaveExpiration(ctx context.Context, certificate domain.MedicalCertificateRow, expiration string,
	actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error {
	return t.associateDraftTransaction.SaveCertificateExpiration(ctx, domain.AssociateDraftCertificate{ID: certificate.ID, Expiration: certificate.Expiration},
		expiration, expiration+" 00:00:00+00:00", actorAssociation, audit, at)
}

func (t *subscriptionMedicalTransaction) DeleteCertificate(ctx context.Context, id string, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) error {
	// Subscription.medical is SET_NULL: the collector updates every referencing row (no auditlog entry).
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET medical_id=NULL WHERE medical_id=$1`, id); err != nil {
		return err
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_medicalcertificate", "medical_id", id)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, medicalCertificateAudit(id, 2, changes, actorAssociation, audit, at))
}

// documentReferenced is _medical_document_is_referenced: any row of a table
// whose foreign key (direct or many-to-many through table) targets the document.
func (t *subscriptionMedicalTransaction) documentReferenced(ctx context.Context, id string) (bool, error) {
	references := []struct {
		Table  string `db:"source"`
		Column string `db:"field"`
	}{}
	if err := t.tx.SelectContext(ctx, &references, `SELECT c.conrelid::regclass::text AS source, a.attname AS field
 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
 WHERE c.contype='f' AND c.confrelid='docmanager_document'::regclass ORDER BY 1, 2`); err != nil {
		return false, err
	}
	if len(references) == 0 {
		return false, nil
	}
	clauses := make([]string, 0, len(references))
	for _, reference := range references {
		clauses = append(clauses, fmt.Sprintf(`EXISTS(SELECT 1 FROM %s WHERE %s=$1)`, reference.Table, quoteIdentifier(reference.Column)))
	}
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT `+strings.Join(clauses, " OR "), id)
	return found, err
}

func quoteIdentifier(name string) string { return `"` + strings.ReplaceAll(name, `"`, `""`) + `"` }

func (t *subscriptionMedicalTransaction) DeleteUnreferencedDocument(ctx context.Context, id string, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	referenced, err := t.documentReferenced(ctx, id)
	if err != nil || referenced {
		return err
	}
	changes, _, err := deleteModelWithAuditReverse(ctx, t.tx, "docmanager_document", "document_id", id, instructorHoursDocumentReverse)
	if err != nil {
		return err
	}
	return appendModelAuditForApp(ctx, t.tx, "docmanager", modelAuditEntry{Model: "document", Key: id,
		Representation: "Document object (" + id + ")", Action: 2, Changes: changes, Context: audit, AssociationID: actorAssociation,
		ResolutionPath: associateDraftActorPath(actorAssociation), At: at})
}

func (t *subscriptionMedicalTransaction) SaveDraftData(ctx context.Context, draft domain.MedicalDraft, data, sorted []byte,
	audit domain.LoginAuditContext, at time.Time) error {
	old, err := associateDraftStoredJSON(draft.Data)
	if err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `UPDATE application_associateimportdraft SET data=$2::text::jsonb WHERE associate_import_draft_id=$1`,
		draft.ID, string(data)); err != nil {
		return err
	}
	if old == string(sorted) {
		return nil
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "associateimportdraft", Key: draft.ID,
		Representation: "AssociateImportDraft object (" + draft.ID + ")", Action: 1,
		Changes: map[string][2]string{"data": {old, string(sorted)}}, Context: audit, AssociationID: &draft.Association,
		ResolutionPath: &path, At: at})
}

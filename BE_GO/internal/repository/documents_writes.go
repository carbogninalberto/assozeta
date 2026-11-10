package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// DocumentUploads stores the rows of the document upload views: Documents,
// archive rows, subscription files, uploaded medical certificates and the
// certificate's subscription or draft.
type DocumentUploads struct {
	MemberDocuments
	db *sqlx.DB
}

func NewDocumentUploads(db *sqlx.DB) *DocumentUploads {
	return &DocumentUploads{MemberDocuments: NewMemberDocuments(db), db: db}
}

var _ domain.DocumentUploadStore = (*DocumentUploads)(nil)

type documentUploadTransaction struct {
	*subscriptionMedicalTransaction
	archive *archiveWriteTransaction
}

func (r *DocumentUploads) BeginDocumentUpload(ctx context.Context) (domain.DocumentUploadTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	base := &paymentCategoryTransaction{ownedTransaction: tx}
	return &documentUploadTransaction{
		subscriptionMedicalTransaction: &subscriptionMedicalTransaction{associateDraftTransaction: &associateDraftTransaction{paymentCategoryTransaction: base}},
		archive:                        &archiveWriteTransaction{paymentCategoryTransaction: base}}, nil
}

func (t *documentUploadTransaction) FolderAssociation(ctx context.Context, id int64) (string, error) {
	return t.archive.FolderAssociation(ctx, id)
}

func (t *documentUploadTransaction) FolderExists(ctx context.Context, id int64) (string, bool, error) {
	association, err := t.archive.FolderAssociation(ctx, id)
	if errors.Is(err, domain.ErrFolderTargetMissing) {
		return "", false, nil
	}
	return association, err == nil, err
}

func (t *documentUploadTransaction) CurrentAssociation(ctx context.Context, user string) (*string, error) {
	return t.ActorAssociation(ctx, user)
}

// Document has no resolver and no association field: its entries are indexed
// to the actor's association, when there is one.
func documentAudit(id string, action int, changes map[string][2]string, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) modelAuditEntry {
	return modelAuditEntry{Model: "document", Key: id, Representation: "Document object (" + id + ")", Action: action, Changes: changes,
		Context: audit, AssociationID: actorAssociation, ResolutionPath: associateDraftActorPath(actorAssociation), At: at}
}

// documentReverse is the reverse foreign key with a related_name that
// auditlog renders (as its empty manager) on Document creates and deletes.
var documentReverse = map[string]string{"customer_invoice_pdf": "application.CustomerInvoice.None"}

func (t *documentUploadTransaction) CreateDocument(ctx context.Context, document domain.NewDocument, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO docmanager_document (document_id, creation_date, filepath, filename, file_size_bytes, token)
 VALUES ($1, $2, NULL, $3, NULL, $4)`, document.ID, document.Created, document.Filename, document.Token); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "docmanager_document", "document_id", document.ID)
	if err != nil {
		return err
	}
	for name, rendered := range documentReverse {
		changes[name] = [2]string{"None", rendered}
	}
	return appendModelAuditForApp(ctx, t.tx, "docmanager", documentAudit(document.ID, 0, changes, actorAssociation, audit, at))
}

func (t *documentUploadTransaction) DeleteDocument(ctx context.Context, document domain.NewDocument, _ string, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	rows := []struct {
		ID          string `db:"id"`
		Association string `db:"association"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT sport_association_documents_archive_id::text AS id, sport_association_id::text AS association
 FROM application_sportassociationdocumentsarchive WHERE document_id=$1 ORDER BY sport_association_documents_archive_id FOR UPDATE`,
		document.ID); err != nil {
		return err
	}
	for _, row := range rows {
		if err := t.archive.DeleteArchive(ctx, row.ID, row.Association, audit, at); err != nil {
			return err
		}
	}
	changes, _, err := deleteModelWithAuditReverse(ctx, t.tx, "docmanager_document", "document_id", document.ID, documentReverse)
	if err != nil {
		return err
	}
	return appendModelAuditForApp(ctx, t.tx, "docmanager", documentAudit(document.ID, 2, changes, actorAssociation, audit, at))
}

func folderText(folder *int64) string {
	if folder == nil {
		return "None"
	}
	return strconv.FormatInt(*folder, 10)
}

// pythonAwareUTC is str() of an aware UTC datetime.
func pythonAwareUTC(value time.Time) string { return pythonNaiveUTC(value) + "+00:00" }

func archiveAudit(id string, action int, changes map[string][2]string, association, path string, audit domain.LoginAuditContext,
	at time.Time) modelAuditEntry {
	return modelAuditEntry{Model: "sportassociationdocumentsarchive", Key: id, Representation: "SportAssociationDocumentsArchive object (" + id + ")",
		Action: action, Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: &path, At: at}
}

// CreateArchive inserts the row; auditlog renders the in-memory date default,
// the aware datetime timezone.now() returned, while the column stores its date.
func (t *documentUploadTransaction) CreateArchive(ctx context.Context, row domain.ArchiveRecord, created time.Time, audit domain.LoginAuditContext,
	at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_sportassociationdocumentsarchive
 (sport_association_documents_archive_id, sport_association_id, document_id, date, folder_id) VALUES ($1, $2, $3, $4::date, $5)`,
		row.ID, row.Association, row.Document, created.UTC().Format("2006-01-02"), row.Folder); err != nil {
		return err
	}
	changes := map[string][2]string{"sport_association_documents_archive_id": {"None", row.ID}, "sport_association": {"None", row.Association},
		"document": {"None", row.Document}, "date": {"None", pythonAwareUTC(created)}, "folder": {"None", folderText(row.Folder)}}
	return appendModelAudit(ctx, t.tx, archiveAudit(row.ID, 0, changes, row.Association, "direct", audit, at))
}

func (t *documentUploadTransaction) Archive(ctx context.Context, id, association string) (domain.ArchiveRecord, error) {
	var row struct {
		ID          string `db:"id"`
		Association string `db:"association"`
		Document    string `db:"document"`
		Date        string `db:"date"`
		Folder      *int64 `db:"folder"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT sport_association_documents_archive_id::text AS id, sport_association_id::text AS association,
 document_id::text AS document, to_char(date, 'YYYY-MM-DD') AS date, folder_id AS folder FROM application_sportassociationdocumentsarchive
 WHERE sport_association_documents_archive_id=$1 AND sport_association_id=$2 FOR UPDATE`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ArchiveRecord{}, domain.ErrArchiveNotFound
	}
	return domain.ArchiveRecord{ID: row.ID, Association: row.Association, Document: row.Document, Date: row.Date, Folder: row.Folder}, err
}

func (t *documentUploadTransaction) ArchiveIDTaken(ctx context.Context, id, except string) (bool, error) {
	var taken bool
	err := t.tx.GetContext(ctx, &taken, `SELECT EXISTS(SELECT 1 FROM application_sportassociationdocumentsarchive
 WHERE sport_association_documents_archive_id=$1 AND sport_association_documents_archive_id<>$2)`, id, except)
	return taken, err
}

// SaveArchive writes every field; the update entry (logged before the save,
// so its row resolves directly) lists the changed fields.
func (t *documentUploadTransaction) SaveArchive(ctx context.Context, row domain.ArchiveRecord, changes map[string][2]string,
	audit domain.LoginAuditContext, at time.Time) error {
	if len(changes) > 0 {
		if err := appendModelAudit(ctx, t.tx, archiveAudit(row.ID, 1, changes, row.Association, "direct", audit, at)); err != nil {
			return err
		}
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_sportassociationdocumentsarchive SET sport_association_id=$2, document_id=$3,
 date=$4::date, folder_id=$5 WHERE sport_association_documents_archive_id=$1`, row.ID, row.Association, row.Document, row.Date, row.Folder)
	return err
}

func (t *documentUploadTransaction) InsertArchive(ctx context.Context, row domain.ArchiveRecord, audit domain.LoginAuditContext, at time.Time) error {
	changes := map[string][2]string{"sport_association_documents_archive_id": {"None", row.ID}, "sport_association": {"None", row.Association},
		"document": {"None", row.Document}, "date": {"None", row.Date}, "folder": {"None", folderText(row.Folder)}}
	// pre_save finds no row under the new key: the update entry is indexed
	// before the row exists, through its changes.
	if err := appendModelAudit(ctx, t.tx, archiveAudit(row.ID, 1, changes, row.Association, "changes_json.sport_association", audit, at)); err != nil {
		return err
	}
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_sportassociationdocumentsarchive
 (sport_association_documents_archive_id, sport_association_id, document_id, date, folder_id) VALUES ($1, $2, $3, $4::date, $5)`,
		row.ID, row.Association, row.Document, row.Date, row.Folder); err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, archiveAudit(row.ID, 0, changes, row.Association, "direct", audit, at))
}

func (t *documentUploadTransaction) UploadSubscription(ctx context.Context, id string, impersonation, group *string) (*domain.UploadSubscription, error) {
	var association string
	err := t.tx.GetContext(ctx, &association, `SELECT sport_association_id::text FROM application_subscription WHERE subscription_id=$1
 AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) AND ($3::uuid IS NULL OR group_id=$3::uuid) FOR UPDATE`, id, impersonation, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &domain.UploadSubscription{ID: id, Association: association}, nil
}

func (t *documentUploadTransaction) CreateSubscriptionFile(ctx context.Context, id string, subscription domain.UploadSubscription, document string,
	created time.Time, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_subscriptionfile (subscription_file_id, subscription_id, document_id, creation_date)
 VALUES ($1, $2, $3, $4)`, id, subscription.ID, document, created); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_subscriptionfile", "subscription_file_id", id)
	if err != nil {
		return err
	}
	path := "subscription.sport_association"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "subscriptionfile", Key: id, Representation: "SubscriptionFile object (" + id + ")",
		Action: 0, Changes: changes, Context: audit, AssociationID: &subscription.Association, ResolutionPath: &path, At: at})
}

func (t *documentUploadTransaction) CreateUploadedCertificate(ctx context.Context, id, document string, user *string, created time.Time,
	actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_medicalcertificate
 (medical_id, document_id, user_id, creation_date, expiration_date, competitive_medical_certificate, notes)
 VALUES ($1, $2, $3::uuid, $4, NULL, false, NULL)`, id, document, user, created); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_medicalcertificate", "medical_id", id)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, medicalCertificateAudit(id, 0, changes, actorAssociation, audit, at))
}

func (t *documentUploadTransaction) AttachCertificate(ctx context.Context, subscription domain.MedicalSubscription, certificate string,
	dates domain.SubscriptionDates, audit domain.LoginAuditContext, at time.Time) error {
	old := "None"
	if subscription.Certificate != nil {
		old = subscription.Certificate.ID
	}
	changes := map[string][2]string{}
	if old != certificate {
		changes["medical"] = [2]string{old, certificate}
	}
	if before, after := calendarText(subscription.Target.Dates.Start), calendarText(dates.Start); before != after {
		changes["start_date"] = [2]string{before, after}
	}
	if before, after := calendarText(subscription.Target.Dates.End), calendarText(dates.End); before != after {
		changes["end_date"] = [2]string{before, after}
	}
	if len(changes) > 0 {
		path := "direct"
		if err := appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "subscription", Key: subscription.ID,
			Representation: "Subscription object (" + subscription.ID + ")", Action: 1, Changes: changes, Context: audit,
			AssociationID: &subscription.Association, ResolutionPath: &path, At: at}); err != nil {
			return err
		}
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET medical_id=$2, start_date=$3::date, end_date=$4::date
 WHERE subscription_id=$1`, subscription.ID, certificate, calendarValue(dates.Start), calendarValue(dates.End))
	return err
}

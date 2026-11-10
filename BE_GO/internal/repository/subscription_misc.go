package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SubscriptionMisc struct{ db *sqlx.DB }

func NewSubscriptionMisc(db *sqlx.DB) *SubscriptionMisc { return &SubscriptionMisc{db: db} }

var _ domain.SubscriptionMiscStore = (*SubscriptionMisc)(nil)

type subscriptionMiscTransaction struct{ *paymentCategoryTransaction }

func (r *SubscriptionMisc) BeginSubscriptionMisc(ctx context.Context) (domain.SubscriptionMiscTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionMiscTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *subscriptionMiscTransaction) Association(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

func (t *subscriptionMiscTransaction) first(ctx context.Context, query string, args ...any) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, query, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func (t *subscriptionMiscTransaction) ScopedFirstAssociation(ctx context.Context, user string, scope *string) (*string, error) {
	return t.first(ctx, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) ORDER BY sport_association_id LIMIT 1`, user, scope)
}

func (t *subscriptionMiscTransaction) ActorAssociation(ctx context.Context, actor string) (*string, error) {
	return t.ScopedFirstAssociation(ctx, actor, nil)
}

func (t *subscriptionMiscTransaction) ImportDoing(ctx context.Context, association string) (bool, *bool, error) {
	rows := []struct {
		Doing *bool `db:"doing"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT doing FROM application_associateimportdraftstatus
 WHERE sport_association_id=$1 AND status_type=1 ORDER BY creation_date DESC LIMIT 1`, association); err != nil {
		return false, nil, err
	}
	if len(rows) == 0 {
		return false, nil, nil
	}
	return true, rows[0].Doing, nil
}

type subscriptionMiscCustomRow struct {
	ID         string  `db:"id"`
	CustomData *string `db:"custom_data"`
}

func subscriptionMiscCustoms(rows []subscriptionMiscCustomRow) []domain.SubscriptionMiscCustom {
	result := make([]domain.SubscriptionMiscCustom, 0, len(rows))
	for _, row := range rows {
		result = append(result, domain.SubscriptionMiscCustom{ID: row.ID, CustomData: row.CustomData})
	}
	return result
}

// The JSONField icontains lookup: UPPER(custom_data::text) LIKE UPPER('%...%')
// (the patterns arrive escaped for LIKE). No ORDER BY, as in Django.
func (t *subscriptionMiscTransaction) Federation(ctx context.Context, association string, patterns []string, group, scope *string) (
	[]domain.SubscriptionMiscCustom, error) {
	rows := []subscriptionMiscCustomRow{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT subscription_id::text AS id, custom_data::text AS custom_data FROM application_subscription
 WHERE EXISTS(SELECT 1 FROM unnest($2::text[]) AS p(pattern) WHERE UPPER(custom_data::text) LIKE UPPER(p.pattern))
 AND sport_association_id=$1 AND ($3::uuid IS NULL OR group_id=$3::uuid) AND ($4::uuid IS NULL OR sport_association_id=$4::uuid)`,
		association, patterns, group, scope)
	return subscriptionMiscCustoms(rows), err
}

func (t *subscriptionMiscTransaction) CreateLink(ctx context.Context, id, association, token, gymName string, expires, created time.Time,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_cumulativesubscriptiongymlinks
 (cumulative_subscription_gym_links_id, token, expires_at, creation_date, sport_association_id, gym_name) VALUES ($1, $2, $3, $4, $5, $6)`,
		id, token, expires, created, association, gymName); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_cumulativesubscriptiongymlinks", "cumulative_subscription_gym_links_id", id)
	if err != nil {
		return err
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "cumulativesubscriptiongymlinks", Key: id,
		Representation: "CumulativeSubscriptionGymLinks object (" + id + ")", Action: 0, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at})
}

func (t *subscriptionMiscTransaction) LiveLink(ctx context.Context, token string, now time.Time) (*domain.SubscriptionMiscLink, error) {
	rows := []struct {
		ID          string  `db:"id"`
		Association string  `db:"association"`
		GymName     *string `db:"gym_name"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT cumulative_subscription_gym_links_id::text AS id, sport_association_id::text AS association,
 gym_name FROM application_cumulativesubscriptiongymlinks WHERE token=$1 AND expires_at > $2
 ORDER BY cumulative_subscription_gym_links_id LIMIT 1`, token, now); err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &domain.SubscriptionMiscLink{ID: rows[0].ID, AssociationID: rows[0].Association, GymName: rows[0].GymName}, nil
}

func (t *subscriptionMiscTransaction) DeleteExpiredLinks(ctx context.Context, now time.Time, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) error {
	rows := []struct {
		ID          string `db:"id"`
		Association string `db:"association"`
	}{}
	// The collector deletes in primary key order.
	if err := t.tx.SelectContext(ctx, &rows, `SELECT cumulative_subscription_gym_links_id::text AS id, sport_association_id::text AS association
 FROM application_cumulativesubscriptiongymlinks WHERE expires_at < $1 ORDER BY cumulative_subscription_gym_links_id FOR UPDATE`, now); err != nil {
		return err
	}
	for _, row := range rows {
		changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_cumulativesubscriptiongymlinks", "cumulative_subscription_gym_links_id", row.ID)
		if err != nil {
			return err
		}
		path, association := "changes_json.sport_association", row.Association
		live, err := t.liveAssociation(ctx, association)
		if err != nil {
			return err
		}
		// Index: the deleted row resolves through its changes, else the actor's association.
		associationID, resolution := actorAssociation, associateDraftActorPath(actorAssociation)
		if live {
			associationID, resolution = &association, &path
		}
		if err = appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "cumulativesubscriptiongymlinks", Key: row.ID,
			Representation: "CumulativeSubscriptionGymLinks object (" + row.ID + ")", Action: 2, Changes: changes, Context: audit,
			AssociationID: associationID, ResolutionPath: resolution, At: at}); err != nil {
			return err
		}
	}
	return nil
}

// liveAssociation is SportAssociation.objects.filter(pk=...).first() of the
// index fallback (the active manager).
func (t *subscriptionMiscTransaction) liveAssociation(ctx context.Context, id string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1 AND NOT deleted)`, id)
	return found, err
}

func (t *subscriptionMiscTransaction) PairingLookup(ctx context.Context) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM (SELECT p.state, p.remote_pairing_id, c.self_hosted
 FROM instance_bakneypairing p JOIN instance_instanceconfiguration c ON c.id=p.config_id ORDER BY p.id LIMIT 1) f
 WHERE f.self_hosted AND f.state IN ('pending', 'paired') AND f.remote_pairing_id IS NOT NULL)`)
	return found, err
}

func (t *subscriptionMiscTransaction) ActivePeriods(ctx context.Context, association string, today time.Time, group, scope *string) (
	[]domain.SubscriptionMiscPeriod, error) {
	rows := []struct {
		Associate *string    `db:"associate"`
		Start     *time.Time `db:"start_date"`
		End       *time.Time `db:"end_date"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT associate_id::text AS associate, start_date, end_date FROM application_subscription
 WHERE sport_association_id=$1 AND status_flag IN (4, 2) AND end_date >= $2::date
 AND ($3::uuid IS NULL OR group_id=$3::uuid) AND ($4::uuid IS NULL OR sport_association_id=$4::uuid)`,
		association, today.Format("2006-01-02"), group, scope); err != nil {
		return nil, err
	}
	result := make([]domain.SubscriptionMiscPeriod, 0, len(rows))
	for _, row := range rows {
		result = append(result, domain.SubscriptionMiscPeriod{AssociateID: row.Associate, Start: row.Start, End: row.End})
	}
	return result, nil
}

// Candidates: exclude(associate_id__in=ids) keeps rows without an associate.
func (t *subscriptionMiscTransaction) Candidates(ctx context.Context, association, needle string, cutoff time.Time, excluded []string,
	group, scope *string) ([]domain.SubscriptionMiscCustom, error) {
	rows := []subscriptionMiscCustomRow{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT subscription_id::text AS id, custom_data::text AS custom_data FROM application_subscription
 WHERE sport_association_id=$1 AND UPPER(custom_data::text) LIKE UPPER($2) AND start_date <= $3::date
 AND NOT (associate_id::text = ANY($4::text[]) AND associate_id IS NOT NULL)
 AND ($5::uuid IS NULL OR group_id=$5::uuid) AND ($6::uuid IS NULL OR sport_association_id=$6::uuid)`,
		association, needle, cutoff.Format("2006-01-02"), excluded, group, scope)
	return subscriptionMiscCustoms(rows), err
}

func (t *subscriptionMiscTransaction) Subscription(ctx context.Context, id, association string, group, scope *string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_subscription WHERE subscription_id=$1 AND sport_association_id=$2
 AND ($3::uuid IS NULL OR group_id=$3::uuid) AND ($4::uuid IS NULL OR sport_association_id=$4::uuid))`, id, association, group, scope)
	return found, err
}

func (t *subscriptionMiscTransaction) File(ctx context.Context, id, subscription string) (*domain.SubscriptionMiscFile, error) {
	rows := []struct {
		ID       string `db:"id"`
		Document string `db:"document"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT subscription_file_id::text AS id, document_id::text AS document
 FROM application_subscriptionfile WHERE subscription_file_id=$1 AND subscription_id=$2 FOR UPDATE`, id, subscription); err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &domain.SubscriptionMiscFile{ID: rows[0].ID, DocumentID: rows[0].Document}, nil
}

// DocumentShared: any foreign key (direct or many-to-many through table) to
// the document from a row other than the file itself.
func (t *subscriptionMiscTransaction) DocumentShared(ctx context.Context, document, file string) (bool, error) {
	references := []struct {
		Table  string `db:"source"`
		Column string `db:"field"`
	}{}
	if err := t.tx.SelectContext(ctx, &references, `SELECT c.conrelid::regclass::text AS source, a.attname AS field
 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
 WHERE c.contype='f' AND c.confrelid='docmanager_document'::regclass ORDER BY 1, 2`); err != nil {
		return false, err
	}
	for _, reference := range references {
		query := `SELECT EXISTS(SELECT 1 FROM ` + reference.Table + ` WHERE ` + quoteIdentifier(reference.Column) + `=$1`
		args := []any{document}
		if reference.Table == "application_subscriptionfile" {
			query += ` AND subscription_file_id<>$2`
			args = append(args, file)
		}
		var found bool
		if err := t.tx.GetContext(ctx, &found, query+`)`, args...); err != nil {
			return false, err
		}
		if found {
			return true, nil
		}
	}
	return false, nil
}

func subscriptionMiscFileAudit(id string, changes map[string][2]string, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) modelAuditEntry {
	return modelAuditEntry{Model: "subscriptionfile", Key: id, Representation: "SubscriptionFile object (" + id + ")", Action: 2,
		Changes: changes, Context: audit, AssociationID: actorAssociation, ResolutionPath: associateDraftActorPath(actorAssociation), At: at}
}

func (t *subscriptionMiscTransaction) DeleteFile(ctx context.Context, id string, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) (map[string][2]string, error) {
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_subscriptionfile", "subscription_file_id", id)
	if err != nil {
		return nil, err
	}
	return changes, appendModelAudit(ctx, t.tx, subscriptionMiscFileAudit(id, changes, actorAssociation, audit, at))
}

func (t *subscriptionMiscTransaction) RecordFileDelete(ctx context.Context, id string, changes map[string][2]string, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	return appendModelAudit(ctx, t.tx, subscriptionMiscFileAudit(id, changes, actorAssociation, audit, at))
}

// DeleteSharedDocument runs the shared deletion collector on the document
// (document_delete.go) and reads the file's delete diff back from the entry
// the collector wrote for it.
func (t *subscriptionMiscTransaction) DeleteSharedDocument(ctx context.Context, document, file string, group *string,
	audit domain.LoginAuditContext, at time.Time) (map[string][2]string, error) {
	graph, err := documentDeleteGraph()
	if err != nil {
		return nil, err
	}
	run := &collaboratorDeleteRun{tx: t.tx, graph: graph, audit: audit, group: group, now: func() time.Time { return at }}
	rows, err := run.load(ctx, graph.Models[documentDeleteLabel], `t.document_id = $1::uuid FOR UPDATE`, document)
	if err != nil {
		return nil, err
	}
	if err = run.Delete(ctx, documentDeleteLabel, rows); err != nil {
		return nil, fmt.Errorf("subscription file document delete: %w", err)
	}
	var stored string
	if err = t.tx.GetContext(ctx, &stored, `SELECT changes::text FROM auditlog_logentry WHERE object_pk=$1 AND action=2
 AND content_type_id=(SELECT id FROM django_content_type WHERE app_label='application' AND model='subscriptionfile')
 ORDER BY id DESC LIMIT 1`, file); err != nil {
		return nil, err
	}
	changes := map[string][2]string{}
	if err = json.Unmarshal([]byte(stored), &changes); err != nil {
		return nil, err
	}
	return changes, nil
}

func (t *subscriptionMiscTransaction) DeleteDocument(ctx context.Context, id string, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) error {
	changes, _, err := deleteModelWithAuditReverse(ctx, t.tx, "docmanager_document", "document_id", id, instructorHoursDocumentReverse)
	if err != nil {
		return err
	}
	return appendModelAuditForApp(ctx, t.tx, "docmanager", modelAuditEntry{Model: "document", Key: id,
		Representation: "Document object (" + id + ")", Action: 2, Changes: changes, Context: audit, AssociationID: actorAssociation,
		ResolutionPath: associateDraftActorPath(actorAssociation), At: at})
}

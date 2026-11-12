package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// WorkflowTaskStore holds the reads and the one write (get_main_tutor's
// promotion of the first tutor relation) of the workflow Celery tasks
// (check_workflows_trigger, check_workflows_trigger_cron,
// workflow_block_execute). Each statement autocommits as in Django.
type WorkflowTaskStore struct{ db *sqlx.DB }

func NewWorkflowTaskStore(db *sqlx.DB) *WorkflowTaskStore { return &WorkflowTaskStore{db: db} }

type workflowTaskRow struct {
	ID          uuid.UUID `db:"automation_workflow_id"`
	Association uuid.UUID `db:"sport_association_id"`
	Tree        string    `db:"automation_tree"`
}

func (row workflowTaskRow) workflow() domain.WorkflowTaskWorkflow {
	return domain.WorkflowTaskWorkflow{ID: row.ID, Association: row.Association, Tree: row.Tree}
}

// AssociationWorkflows is AutomationWorkflow.objects.filter(
// sport_association=id, enabled=True).iterator(): no ORDER BY, the rows in
// the order PostgreSQL returns them, as Django iterates them.
func (r *WorkflowTaskStore) AssociationWorkflows(ctx context.Context, association uuid.UUID) ([]domain.WorkflowTaskWorkflow, error) {
	var rows []workflowTaskRow
	err := r.db.SelectContext(ctx, &rows, `SELECT "communications_automationworkflow"."automation_workflow_id",
 "communications_automationworkflow"."sport_association_id", "communications_automationworkflow"."automation_tree"::text AS automation_tree
 FROM "communications_automationworkflow"
 WHERE ("communications_automationworkflow"."enabled" AND "communications_automationworkflow"."sport_association_id" = $1)`, association)
	out := make([]domain.WorkflowTaskWorkflow, len(rows))
	for i, row := range rows {
		out[i] = row.workflow()
	}
	return out, err
}

// EnabledWorkflow is AutomationWorkflow.objects.filter(
// automation_workflow_id=id, enabled=True).first().
func (r *WorkflowTaskStore) EnabledWorkflow(ctx context.Context, id uuid.UUID) (*domain.WorkflowTaskWorkflow, error) {
	var row workflowTaskRow
	err := r.db.GetContext(ctx, &row, `SELECT automation_workflow_id, sport_association_id, automation_tree::text AS automation_tree
 FROM communications_automationworkflow WHERE enabled AND automation_workflow_id=$1 ORDER BY automation_workflow_id LIMIT 1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	workflow := row.workflow()
	return &workflow, nil
}

// BlockWorkflow is EnabledWorkflow with select_related('sport_association',
// 'sport_association__user'): the owner's id and email.
func (r *WorkflowTaskStore) BlockWorkflow(ctx context.Context, id uuid.UUID) (*domain.WorkflowTaskBlockWorkflow, error) {
	var row struct {
		workflowTaskRow
		Owner uuid.UUID `db:"owner_id"`
		Email string    `db:"owner_email"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT w.automation_workflow_id, w.sport_association_id, w.automation_tree::text AS automation_tree,
 u.user_id AS owner_id, u.email AS owner_email
 FROM communications_automationworkflow w
 INNER JOIN application_sportassociation a ON (w.sport_association_id=a.sport_association_id)
 INNER JOIN bakney_user u ON (a.user_id=u.user_id)
 WHERE w.enabled AND w.automation_workflow_id=$1 ORDER BY w.automation_workflow_id LIMIT 1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &domain.WorkflowTaskBlockWorkflow{WorkflowTaskWorkflow: row.workflow(), OwnerID: row.Owner, OwnerEmail: row.Email}, nil
}

// SubscriptionExists is Subscription.objects.filter(subscription_id=id).first()
// is not None (SubscriptionManager: no group outside a request, deleted rows included).
func (r *WorkflowTaskStore) SubscriptionExists(ctx context.Context, id uuid.UUID) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_subscription WHERE subscription_id=$1)`, id)
	return found, err
}

// BlockSubscription is Subscription.objects.filter(subscription_id=id)
// .select_related('associate', 'user').first().
func (r *WorkflowTaskStore) BlockSubscription(ctx context.Context, id uuid.UUID) (*domain.WorkflowTaskSubscription, error) {
	var row struct {
		ID        uuid.UUID  `db:"subscription_id"`
		User      uuid.UUID  `db:"user_id"`
		UserEmail string     `db:"user_email"`
		Associate *uuid.UUID `db:"associate_id"`
		Email     *string    `db:"associate_email"`
		Minor     *bool      `db:"is_minor"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT s.subscription_id, u.user_id, u.email AS user_email, p.associate_id,
 p.email AS associate_email, p.is_minor
 FROM application_subscription s LEFT OUTER JOIN application_associate p ON (s.associate_id=p.associate_id)
 INNER JOIN bakney_user u ON (s.user_id=u.user_id)
 WHERE s.subscription_id=$1 ORDER BY s.subscription_id LIMIT 1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &domain.WorkflowTaskSubscription{ID: row.ID, UserID: row.User, UserEmail: row.UserEmail, AssociateID: row.Associate,
		AssociateEmail: row.Email, AssociateMinor: row.Minor != nil && *row.Minor}, nil
}

// Tag is Tags.objects.filter(tag_id=id).first().
func (r *WorkflowTaskStore) Tag(ctx context.Context, id uuid.UUID) (*domain.WorkflowTaskTag, error) {
	var tag domain.WorkflowTaskTag
	err := r.db.QueryRowxContext(ctx, `SELECT tag_id, tag_name FROM application_tags WHERE tag_id=$1 ORDER BY tag_id LIMIT 1`, id).
		Scan(&tag.ID, &tag.Name)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &tag, nil
}

// workflowTaskLikeEscape is connection.ops.prep_for_like_query.
func workflowTaskLikeEscape(text string) string {
	return strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(text)
}

// CronSubscriptions is the cron trigger's queryset:
// Subscription.objects.filter(sport_association, start_date__lte=today,
// end_date__gte=today) with the target filter, evaluated without ORDER BY
// (the tag filter joins the tags and repeats a subscription per matching
// tag, as Django iterates it). Only the keys are read.
func (r *WorkflowTaskStore) CronSubscriptions(ctx context.Context, association uuid.UUID, today time.Time,
	filter domain.WorkflowTaskCronFilter) ([]uuid.UUID, error) {
	from := `"application_subscription"`
	where := `("application_subscription"."end_date" >= $2 AND "application_subscription"."sport_association_id" = $1 AND "application_subscription"."start_date" <= $2`
	arguments := []any{association, today.Format("2006-01-02")}
	switch {
	case filter.Status != nil:
		where += ` AND NOT "application_subscription"."archived" AND "application_subscription"."status_flag" = $3`
		arguments = append(arguments, *filter.Status)
	case filter.Archived != nil:
		where += ` AND "application_subscription"."archived"`
	case filter.Tag != nil:
		from += ` INNER JOIN "application_subscription_tags" ON ("application_subscription"."subscription_id" = "application_subscription_tags"."subscription_id")` +
			` INNER JOIN "application_tags" ON ("application_subscription_tags"."tags_id" = "application_tags"."tag_id")`
		where += ` AND ("application_subscription_tags"."tags_id" = $3 OR UPPER("application_tags"."tag_name"::text) LIKE UPPER($4))`
		arguments = append(arguments, filter.Tag.ID, "%"+workflowTaskLikeEscape(filter.Tag.Name)+"%")
	}
	var ids []uuid.UUID
	err := r.db.SelectContext(ctx, &ids, `SELECT "application_subscription"."subscription_id" FROM `+from+` WHERE `+where+`)`, arguments...)
	return ids, err
}

// FirstTutorRelation is associate.tutor_relations[.filter(is_primary=True)].first().
func (r *WorkflowTaskStore) FirstTutorRelation(ctx context.Context, associate uuid.UUID, primaryOnly bool) (*domain.TutorRelation, error) {
	var row domain.TutorRelation
	query := `SELECT id, associate_id, tutor_id, is_primary FROM application_associatetutorrelation WHERE associate_id=$1`
	if primaryOnly {
		query += ` AND is_primary`
	}
	err := r.db.QueryRowxContext(ctx, query+` ORDER BY id ASC LIMIT 1`, associate).Scan(&row.ID, &row.Associate, &row.Tutor, &row.Primary)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// AssociateEmail follows tutor_relation.tutor (base manager).
func (r *WorkflowTaskStore) AssociateEmail(ctx context.Context, associate uuid.UUID) (*string, error) {
	var email *string
	err := r.db.GetContext(ctx, &email, `SELECT email FROM application_associate WHERE associate_id=$1`, associate)
	return email, err
}

// PromoteTutorRelation is get_main_tutor's tutor_relation.save() with
// is_primary=True: the auditlog update entry (no actor outside a request),
// indexed through the associate's association, then every field written.
func (r *WorkflowTaskStore) PromoteTutorRelation(ctx context.Context, relation domain.TutorRelation, at time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var association *string
	if err = tx.GetContext(ctx, &association, `SELECT sport_association_id::text FROM application_associate WHERE associate_id=$1`,
		relation.Associate); err != nil && !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	key := strconv.FormatInt(relation.ID, 10)
	id := relation.ID
	entry := modelAuditEntry{Model: "associatetutorrelation", Key: key, ObjectID: &id,
		Representation: "AssociateTutorRelation object (" + key + ")", Action: 1,
		Changes: map[string][2]string{"is_primary": {"False", "True"}}, At: at}
	if association != nil {
		path := "associate.sport_association"
		entry.AssociationID, entry.ResolutionPath = association, &path
	}
	if err = appendModelAudit(ctx, tx, entry); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `UPDATE application_associatetutorrelation SET associate_id=$2, tutor_id=$3, is_primary=true WHERE id=$1`,
		relation.ID, relation.Associate, relation.Tutor); err != nil {
		return err
	}
	return tx.Commit()
}

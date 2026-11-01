package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type AuditLogs struct{ db *sqlx.DB }

func NewAuditLogs(db *sqlx.DB) *AuditLogs { return &AuditLogs{db: db} }

var _ domain.AuditLogStore = (*AuditLogs)(nil)

func (r *AuditLogs) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, owner, nil, false)
}

// abbrev(inet) is the inet output text (no netmask for a host address), which
// is what Django reads for a GenericIPAddressField.
const auditLogColumns = `e.id, e.action, e.timestamp, e.object_pk, e.object_repr, ct.model, e.actor_id::text AS actor_id,
 u.first_name AS actor_first_name, u.last_name AS actor_last_name, u.email AS actor_user_email, e.actor_email,
 abbrev(e.remote_addr) AS remote_addr, e.changes::text AS changes, e.additional_data::text AS additional_data,
 e.serialized_data::text AS serialized_data
 FROM auditlog_logentry e JOIN django_content_type ct ON ct.id=e.content_type_id LEFT JOIN bakney_user u ON u.user_id=e.actor_id`

// auditLogWhere is the list queryset: the association's indexed entries, minus
// accesstoken entries and user entries whose changes have a last_login key
// (Django's negated nullable lookup keeps a NULL changes), then the filters.
func auditLogWhere(query domain.AuditLogQuery) (string, []any) {
	args := []any{query.AssociationID}
	arg := func(value any) string {
		args = append(args, value)
		return "$" + strconv.Itoa(len(args))
	}
	where := []string{
		`e.id IN (SELECT U0.log_entry_id FROM application_auditlogindex U0 WHERE U0.sport_association_id=$1)`,
		`NOT (ct.model = 'accesstoken')`,
		`NOT (ct.model = 'user' AND jsonb_exists(e.changes, 'last_login') AND e.changes IS NOT NULL)`,
	}
	if query.Search != nil {
		// icontains: UPPER(col::text) LIKE UPPER('%' || escaped || '%').
		replacer := strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)
		pattern := arg("%" + replacer.Replace(*query.Search) + "%")
		where = append(where, fmt.Sprintf(`(UPPER(e.object_repr::text) LIKE UPPER(%[1]s) OR UPPER(u.email::text) LIKE UPPER(%[1]s)
 OR UPPER(u.first_name::text) LIKE UPPER(%[1]s) OR UPPER(u.last_name::text) LIKE UPPER(%[1]s))`, pattern))
	}
	if query.Action != nil {
		where = append(where, "e.action = "+arg(*query.Action))
	}
	if query.Models != nil {
		where = append(where, "e.content_type_id IN (SELECT V0.id FROM django_content_type V0 WHERE V0.model = ANY("+arg(query.Models)+"::text[]))")
	}
	if query.ActorID != nil {
		where = append(where, "e.actor_id = "+arg(*query.ActorID)+"::uuid")
	}
	if query.ObjectPK != nil {
		where = append(where, "e.object_pk = "+arg(*query.ObjectPK))
	}
	if query.From != nil {
		where = append(where, "e.timestamp >= "+arg(*query.From))
	}
	if query.To != nil {
		where = append(where, "e.timestamp <= "+arg(*query.To))
	}
	return " WHERE " + strings.Join(where, " AND "), args
}

func (r *AuditLogs) CountEntries(ctx context.Context, query domain.AuditLogQuery) (int64, error) {
	where, args := auditLogWhere(query)
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT count(*) FROM auditlog_logentry e JOIN django_content_type ct ON ct.id=e.content_type_id
 LEFT JOIN bakney_user u ON u.user_id=e.actor_id`+where, args...)
	return count, err
}

func (r *AuditLogs) Entries(ctx context.Context, query domain.AuditLogQuery, limit, offset int64) ([]domain.AuditLogRow, error) {
	where, args := auditLogWhere(query)
	order := map[string]string{"timestamp": "e.timestamp", "action": "e.action", "model": "ct.model"}[query.OrderColumn]
	if order == "" {
		return nil, errors.New("audit log order column not supported")
	}
	if query.Descending {
		order += " DESC"
	}
	args = append(args, limit, offset)
	rows := []domain.AuditLogRow{}
	err := r.db.SelectContext(ctx, &rows, "SELECT "+auditLogColumns+where+" ORDER BY "+order+
		fmt.Sprintf(" LIMIT $%d OFFSET $%d", len(args)-1, len(args)), args...)
	return rows, err
}

func (r *AuditLogs) IndexedEntry(ctx context.Context, association string, id int64) (*domain.AuditLogRow, error) {
	var row domain.AuditLogRow
	err := r.db.GetContext(ctx, &row, "SELECT "+auditLogColumns+`
 WHERE e.id=$2 AND EXISTS(SELECT 1 FROM application_auditlogindex i WHERE i.log_entry_id=$2 AND i.sport_association_id=$1)`, association, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// auditDate renders a datetime column for the resolvers' strftime: the UTC
// year (float, so out-of-range values stay readable) and 'DD/MM'.
func auditDate(column, name string) string {
	return fmt.Sprintf(`CASE WHEN isfinite(%[1]s) THEN extract(year FROM (%[1]s AT TIME ZONE 'UTC'))::float8::text END AS %[2]s_year,
 CASE WHEN isfinite(%[1]s) THEN to_char(%[1]s AT TIME ZONE 'UTC', 'DD/MM') END AS %[2]s_daymonth`, column, name)
}

// auditObjects are the default managers the AuditLogEntrySerializer resolvers
// query: soft-delete filters and, for group-aware managers, the X-Group-Id
// filter ($2). Related rows are read without manager filters.
var auditObjects = map[string]struct {
	query   string
	grouped bool
}{
	"course": {`SELECT t.course_id::text AS id, t.title FROM application_course t
 WHERE t.course_id=$1 AND NOT t.deleted AND ($2::uuid IS NULL OR t.group_id=$2::uuid)`, true},
	"associate": {`SELECT t.associate_id::text AS id, t.first_name, t.last_name FROM application_associate t
 WHERE t.associate_id=$1 AND NOT t.deleted AND ($2::uuid IS NULL OR t.group_id=$2::uuid)`, true},
	"subscription": {`SELECT t.subscription_id::text AS id, t.subscription_type, t.associate_id::text AS associate, a.first_name, a.last_name
 FROM application_subscription t LEFT JOIN application_associate a ON a.associate_id=t.associate_id
 WHERE t.subscription_id=$1 AND ($2::uuid IS NULL OR t.group_id=$2::uuid)`, true},
	"coursesubscription": {`SELECT t.course_subscription_id::text AS id, t.subscription_id::text AS subscription, s.associate_id::text AS associate,
 a.first_name, a.last_name, c.title FROM application_coursesubscription t JOIN application_course c ON c.course_id=t.course_id
 LEFT JOIN application_subscription s ON s.subscription_id=t.subscription_id LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE t.course_subscription_id=$1 AND NOT t.deleted`, false},
	"payment": {`SELECT t.payment_id::text AS id, t.description, t.amount::text AS amount FROM application_payment t
 WHERE t.payment_id=$1 AND NOT t.deleted AND ($2::uuid IS NULL OR t.group_id=$2::uuid)`, true},
	"invoice": {`SELECT t.invoice_id::text AS id FROM application_invoice t
 WHERE t.invoice_id=$1 AND NOT t.deleted AND ($2::uuid IS NULL OR t.group_id=$2::uuid)`, true},
	"instructor": {`SELECT t.instructor_id::text AS id, t.first_name, t.last_name FROM application_instructor t WHERE t.instructor_id=$1`, false},
	"attendanceday": {`SELECT t.attendance_day_id::text AS id, t.title, ` + auditDate("t.date", "date") + `
 FROM application_attendanceday t WHERE t.attendance_day_id=$1`, false},
	"attendanceregistry": {`SELECT t.attendance_registry_id::text AS id, c.title FROM application_attendanceregistry t
 JOIN application_course c ON c.course_id=t.course_id WHERE t.attendance_registry_id=$1`, false},
	"medicalcertificate": {`SELECT t.medical_id::text AS id FROM application_medicalcertificate t WHERE t.medical_id=$1`, false},
	"carnet":             {`SELECT t.carnet_id::text AS id, t.title FROM application_carnet t WHERE t.carnet_id=$1`, false},
	"carnetsubscription": {`SELECT t.carnet_subscription_id::text AS id FROM application_carnetsubscription t
 WHERE t.carnet_subscription_id=$1`, false},
	"subscriptionmembership": {`SELECT t.subscription_membership_id::text AS id, t.subscription_id::text AS subscription,
 s.associate_id::text AS associate, a.first_name, a.last_name FROM application_subscriptionmembership t
 LEFT JOIN application_subscription s ON s.subscription_id=t.subscription_id LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE t.subscription_membership_id=$1`, false},
	"group":  {`SELECT t.group_id::text AS id, t.name FROM application_group t WHERE t.group_id=$1`, false},
	"family": {`SELECT t.family_id::text AS id FROM application_family t WHERE t.family_id=$1`, false},
	"tags":   {`SELECT t.tag_id::text AS id FROM application_tags t WHERE t.tag_id=$1`, false},
	"user": {`SELECT t.user_id::text AS id, t.first_name, t.last_name, t.email FROM bakney_user t
 WHERE t.user_id=$1 AND NOT t.deleted`, false},
	"sportassociation": {`SELECT t.sport_association_id::text AS id, t.denomination FROM application_sportassociation t
 WHERE t.sport_association_id=$1 AND NOT t.deleted`, false},
	"module":           {`SELECT t.module_id::text AS id, t.title FROM application_module t WHERE t.module_id=$1`, false},
	"campsandretreats": {`SELECT t.camps_and_retreats_id::text AS id, t.title FROM application_campsandretreats t WHERE t.camps_and_retreats_id=$1`, false},
	"campsandretreatsperiod": {`SELECT t.camps_and_retreats_period_id::text AS id, c.title, ` + auditDate("t.start_date", "start") + `
 FROM application_campsandretreatsperiod t JOIN application_campsandretreats c ON c.camps_and_retreats_id=t.camps_and_retreats_id
 WHERE t.camps_and_retreats_period_id=$1`, false},
	"campsandretreatssubscription": {`SELECT t.camps_and_retreats_subscription_id::text AS id FROM application_campsandretreatssubscription t
 WHERE t.camps_and_retreats_subscription_id=$1`, false},
	"reminders": {`SELECT t.reminders_id::text AS id, t.event_title, ` + auditDate("t.send_at", "send") + `
 FROM application_reminders t WHERE t.reminders_id=$1`, false},
	"globalcalendarevents": {`SELECT t.global_calendar_id::text AS id FROM application_globalcalendarevents t WHERE t.global_calendar_id=$1`, false},
	"message":              {`SELECT t.message_id::text AS id, t.subject, t.type FROM communications_message t WHERE t.message_id=$1`, false},
	"automationworkflow": {`SELECT t.automation_workflow_id::text AS id, t.name FROM communications_automationworkflow t
 WHERE t.automation_workflow_id=$1`, false},
	"communicationconfiguration": {`SELECT t.communication_configuration_id::text AS id FROM communications_communicationconfiguration t
 WHERE t.communication_configuration_id=$1`, false},
}

func (r *AuditLogs) AuditObject(ctx context.Context, kind, id string, group *string) (domain.AuditObject, error) {
	spec, ok := auditObjects[kind]
	if !ok {
		return nil, fmt.Errorf("audit object kind %q not supported", kind)
	}
	args := []any{id}
	if spec.grouped {
		args = append(args, group)
	}
	rows, err := r.db.QueryxContext(ctx, spec.query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	if !rows.Next() {
		return nil, rows.Err()
	}
	columns, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	values := make([]sql.NullString, len(columns))
	targets := make([]any, len(columns))
	for i := range values {
		targets[i] = &values[i]
	}
	if err := rows.Scan(targets...); err != nil {
		return nil, err
	}
	object := domain.AuditObject{}
	for i, column := range columns {
		if values[i].Valid {
			text := values[i].String
			object[column] = &text
		} else {
			object[column] = nil
		}
	}
	return object, rows.Err()
}

func (r *AuditLogs) EventLists(ctx context.Context, registry, calendar bool) ([]string, []string, error) {
	registries, calendars := []string{}, []string{}
	if registry {
		if err := r.db.SelectContext(ctx, &registries, `SELECT events::text FROM application_attendanceregistry WHERE events IS NOT NULL`); err != nil {
			return nil, nil, err
		}
	}
	if calendar {
		if err := r.db.SelectContext(ctx, &calendars, `SELECT events::text FROM application_globalcalendarevents WHERE events IS NOT NULL`); err != nil {
			return nil, nil, err
		}
	}
	return registries, calendars, nil
}

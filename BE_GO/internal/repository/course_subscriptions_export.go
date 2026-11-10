package repository

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// The statements below are the ones current_view_course_subscription sends
// through the ORM, with the same tables, join types and predicates and
// without ORDER BY: the export keeps the order the database returns for them
// (course-subscriptions.md, "Excel export"). Each statement also selects
// every column of the tables the ORM selects, so the planner estimates the
// same row widths and costs, and so the same plan, for both backends.

// exportSelect lists every column of the tables as "<alias>__<column>".
func (t *courseSubscriptionTransaction) exportSelect(ctx context.Context, tables ...string) (string, error) {
	terms := []string{}
	for _, table := range tables {
		columns := []string{}
		if err := t.tx.SelectContext(ctx, &columns, `SELECT column_name::text FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = $1 ORDER BY ordinal_position`, table); err != nil {
			return "", err
		}
		if len(columns) == 0 {
			return "", fmt.Errorf("course subscription export: table %s has no columns", table)
		}
		for _, column := range columns {
			terms = append(terms, `"`+table+`"."`+column+`" AS "`+table+`__`+column+`"`)
		}
	}
	return strings.Join(terms, ", "), nil
}

func exportText(value any) *string {
	switch typed := value.(type) {
	case string:
		return &typed
	case []byte:
		text := string(typed)
		return &text
	case [16]byte:
		text := fmt.Sprintf("%x-%x-%x-%x-%x", typed[0:4], typed[4:6], typed[6:8], typed[8:10], typed[10:16])
		return &text
	}
	return nil
}

func exportTime(value any) *time.Time {
	if typed, ok := value.(time.Time); ok {
		return &typed
	}
	return nil
}

func exportInt(value any) *int64 {
	var number int64
	switch typed := value.(type) {
	case int64:
		number = typed
	case int32:
		number = int64(typed)
	case int16:
		number = int64(typed)
	case int:
		number = int64(typed)
	default:
		return nil
	}
	return &number
}

// exportMaps runs statement and returns each row keyed by column alias.
func (t *courseSubscriptionTransaction) exportMaps(ctx context.Context, statement string, args ...any) ([]map[string]any, error) {
	rows, err := t.tx.QueryxContext(ctx, statement, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []map[string]any{}
	for rows.Next() {
		row := map[string]any{}
		if err := rows.MapScan(row); err != nil {
			return nil, err
		}
		result = append(result, row)
	}
	return result, rows.Err()
}

// ExportRows is get_queryset(...).select_related('subscription',
// 'subscription__associate', 'course').
func (t *courseSubscriptionTransaction) ExportRows(ctx context.Context, query domain.CourseSubscriptionQuery) ([]domain.CourseSubscriptionExportRow, error) {
	where, args := "", []any{}
	add := func(condition string, values ...any) {
		for _, value := range values {
			args = append(args, value)
			condition += "$" + strconv.Itoa(len(args)) + "::uuid"
		}
		if where != "" {
			where += " AND "
		}
		where += condition
	}
	if !query.IncludeDeleted {
		// The manager's filter and the view's filter(deleted=False).
		add(`NOT "application_coursesubscription"."deleted"`)
		add(`NOT "application_coursesubscription"."deleted"`)
	}
	if query.CourseID != nil {
		add(`"application_coursesubscription"."course_id" = `, *query.CourseID)
	}
	if query.SubscriptionID != nil {
		add(`"application_coursesubscription"."subscription_id" = `, *query.SubscriptionID)
	}
	add(`"application_course"."sport_association_id" = `, query.AssociationID)
	selected, err := t.exportSelect(ctx, "application_coursesubscription", "application_subscription", "application_associate",
		"application_course")
	if err != nil {
		return nil, err
	}
	// The ORM joins the nullable subscription with LEFT OUTER JOIN, and with INNER JOIN when the queryset also
	// filters on subscription_id.
	subscriptionJoin := "LEFT OUTER JOIN"
	if query.SubscriptionID != nil {
		subscriptionJoin = "INNER JOIN"
	}
	found, err := t.exportMaps(ctx, `SELECT `+selected+` FROM "application_coursesubscription"
 INNER JOIN "application_course" ON ("application_coursesubscription"."course_id" = "application_course"."course_id")
 `+subscriptionJoin+` "application_subscription"
 ON ("application_coursesubscription"."subscription_id" = "application_subscription"."subscription_id")
 LEFT OUTER JOIN "application_associate" ON ("application_subscription"."associate_id" = "application_associate"."associate_id")
 WHERE (`+where+`)`, args...)
	if err != nil {
		return nil, err
	}
	rows := make([]domain.CourseSubscriptionExportRow, 0, len(found))
	for _, row := range found {
		id, created := exportText(row["application_coursesubscription__course_subscription_id"]), exportTime(row["application_coursesubscription__creation_date"])
		if id == nil || created == nil {
			return nil, fmt.Errorf("course subscription export: unreadable row")
		}
		rows = append(rows, domain.CourseSubscriptionExportRow{ID: *id, CreationDate: *created,
			SubscriptionID: exportText(row["application_subscription__subscription_id"]),
			AssociateID:    exportText(row["application_associate__associate_id"]),
			LastName:       exportText(row["application_associate__last_name"]), FirstName: exportText(row["application_associate__first_name"]),
			BornDate: exportTime(row["application_associate__born_date"]), Email: exportText(row["application_associate__email"]),
			Phone: exportText(row["application_associate__phone"]), CourseTitle: exportText(row["application_course__title"])})
	}
	return rows, nil
}

// ExportAssociationTags is Tags.objects.filter(sport_association=...).
func (t *courseSubscriptionTransaction) ExportAssociationTags(ctx context.Context, association string) ([]domain.CourseSubscriptionExportTag, error) {
	rows := []domain.CourseSubscriptionExportTag{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT "application_tags"."tag_id", "application_tags"."tag_name",
 "application_tags"."sport_association_id" AS "association", "application_tags"."creation_date" AS "created" FROM "application_tags"
 WHERE "application_tags"."sport_association_id" = $1::uuid`, association)
	return rows, err
}

// ExportSubscriptionTags is subscription.tags.all().
func (t *courseSubscriptionTransaction) ExportSubscriptionTags(ctx context.Context, subscription string) ([]domain.CourseSubscriptionExportTag, error) {
	rows := []domain.CourseSubscriptionExportTag{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT "application_tags"."tag_id", "application_tags"."tag_name",
 "application_tags"."sport_association_id" AS "association", "application_tags"."creation_date" AS "created" FROM "application_tags"
 INNER JOIN "application_subscription_tags" ON ("application_tags"."tag_id" = "application_subscription_tags"."tags_id")
 WHERE "application_subscription_tags"."subscription_id" = $1::uuid`, subscription)
	return rows, err
}

// ExportCarnets is CarnetSubscription.objects.filter(course_subscription__in=rows,
// disabled=False).select_related('carnet_id'): one row per matching link.
func (t *courseSubscriptionTransaction) ExportCarnets(ctx context.Context, ids []string) ([]domain.CourseSubscriptionExportCarnet, error) {
	rows := []domain.CourseSubscriptionExportCarnet{}
	if len(ids) == 0 {
		return rows, nil // an empty __in matches nothing without a query
	}
	selected, err := t.exportSelect(ctx, "application_carnetsubscription", "application_carnet")
	if err != nil {
		return nil, err
	}
	found, err := t.exportMaps(ctx, `SELECT `+selected+`, "application_carnetsubscription"."meta"::text AS "meta_text"
 FROM "application_carnetsubscription" INNER JOIN "application_carnetsubscription_course_subscription"
 ON ("application_carnetsubscription"."carnet_subscription_id" = "application_carnetsubscription_course_subscription"."carnetsubscription_id")
 LEFT OUTER JOIN "application_carnet" ON ("application_carnetsubscription"."carnet_id_id" = "application_carnet"."carnet_id")
 WHERE ("application_carnetsubscription_course_subscription"."coursesubscription_id" = ANY($1::uuid[])
 AND NOT "application_carnetsubscription"."disabled")`, ids)
	if err != nil {
		return nil, err
	}
	for _, row := range found {
		id, created := exportText(row["application_carnetsubscription__carnet_subscription_id"]), exportTime(row["application_carnetsubscription__creation_date"])
		if id == nil || created == nil {
			return nil, fmt.Errorf("course subscription export: unreadable carnet row")
		}
		rows = append(rows, domain.CourseSubscriptionExportCarnet{ID: *id, CreationDate: *created, Meta: exportText(row["meta_text"]),
			CarnetID: exportText(row["application_carnet__carnet_id"]), Title: exportText(row["application_carnet__title"]),
			Lessons: exportInt(row["application_carnet__lessons_number"])})
	}
	return rows, nil
}

// ExportCarnetLinks is prefetch_related('course_subscription') of the carnet
// subscriptions (the related manager hides deleted course subscriptions).
func (t *courseSubscriptionTransaction) ExportCarnetLinks(ctx context.Context, carnets []string) ([]domain.CourseSubscriptionExportLink, error) {
	rows := []domain.CourseSubscriptionExportLink{}
	if len(carnets) == 0 {
		return rows, nil
	}
	selected, err := t.exportSelect(ctx, "application_coursesubscription")
	if err != nil {
		return nil, err
	}
	found, err := t.exportMaps(ctx, `SELECT ("application_carnetsubscription_course_subscription"."carnetsubscription_id") AS "holder", `+selected+`
 FROM "application_coursesubscription" INNER JOIN "application_carnetsubscription_course_subscription"
 ON ("application_coursesubscription"."course_subscription_id" = "application_carnetsubscription_course_subscription"."coursesubscription_id")
 WHERE (NOT "application_coursesubscription"."deleted"
 AND "application_carnetsubscription_course_subscription"."carnetsubscription_id" = ANY($1::uuid[]))`, carnets)
	if err != nil {
		return nil, err
	}
	for _, row := range found {
		holder, id := exportText(row["holder"]), exportText(row["application_coursesubscription__course_subscription_id"])
		if holder == nil || id == nil {
			return nil, fmt.Errorf("course subscription export: unreadable link row")
		}
		rows = append(rows, domain.CourseSubscriptionExportLink{CarnetSubscriptionID: *holder, CourseSubscriptionID: *id})
	}
	return rows, nil
}

// AssociationDenomination is sport_association.denomination.
func (t *courseSubscriptionTransaction) AssociationDenomination(ctx context.Context, association string) (*string, error) {
	var denomination *string
	err := t.tx.GetContext(ctx, &denomination, `SELECT denomination FROM application_sportassociation WHERE sport_association_id=$1`, association)
	return denomination, err
}

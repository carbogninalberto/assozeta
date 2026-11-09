package repository

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// AuditLogIndexResolution is resolve_sport_association_for_log_entry's
// (sport_association, resolution_path) pair.
type AuditLogIndexResolution struct {
	AssociationID string
	Path          string
}

// auditIndexResolver is one registered resolver of
// BE/application/audit_resolvers.py applied to model_class.objects.get(pk):
// the default manager's filters (soft deletion, X-Group-Id) and the related
// traversal, which uses each related model's base manager.
type auditIndexResolver struct {
	table, pk string
	intPK     bool
	deleted   bool // the default manager excludes deleted rows
	grouped   bool // the default manager filters by the request's X-Group-Id
	none      bool // the resolver returns None for every object
	joins     string
	path      string
}

const (
	auditIndexDirect = ` JOIN application_sportassociation sa ON sa.sport_association_id=o.sport_association_id`
	// user_resolver: SportAssociation.objects (not deleted) filter(user=...).first()
	auditIndexOwnerOf = ` JOIN application_sportassociation sa ON sa.user_id=%s AND NOT sa.deleted`
)

func auditIndexDirectModel(table, pk string) auditIndexResolver {
	return auditIndexResolver{table: table, pk: pk, joins: auditIndexDirect, path: "direct"}
}

func auditIndexNone(table, pk string) auditIndexResolver {
	return auditIndexResolver{table: table, pk: pk, none: true}
}

func auditIndexVia(table, pk, joins, path string) auditIndexResolver {
	return auditIndexResolver{table: table, pk: pk, joins: joins, path: path}
}

func auditIndexOwner(column string) string {
	return strings.Replace(auditIndexOwnerOf, "%s", column, 1)
}

const (
	auditIndexViaSubscription = ` JOIN application_subscription s ON s.subscription_id=o.subscription_id` +
		` JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id`
	auditIndexViaCourse = ` JOIN application_course c ON c.course_id=o.course_id` +
		` JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id`
	auditIndexViaCamps = ` JOIN application_campsandretreats cr ON cr.camps_and_retreats_id=o.camps_and_retreats_id` +
		` JOIN application_sportassociation sa ON sa.sport_association_id=cr.sport_association_id`
)

// auditIndexResolvers is setup_audit_resolvers keyed by "app_label.model".
// Models whose resolver reads an attribute they do not have (direct models
// without sport_association, Signature, MedicalCertificate's reverse
// subscription accessor, VatManagement) resolve nothing and fall back.
var auditIndexResolvers = func() map[string]auditIndexResolver {
	resolvers := map[string]auditIndexResolver{
		"application.sportassociation": {table: "application_sportassociation", pk: "sport_association_id", deleted: true,
			joins: ` JOIN application_sportassociation sa ON sa.sport_association_id=o.sport_association_id`, path: "self"},
		"application.user": {table: "bakney_user", pk: "user_id", deleted: true, joins: auditIndexOwner("o.user_id"),
			path: "user.sport_association"},
		"application.instructor": auditIndexVia("application_instructor", "instructor_id",
			` JOIN bakney_user u ON u.user_id=o.user_id`+auditIndexOwner("u.user_id"), "instructor.user.sport_association"),
		"application.instructorhours": auditIndexVia("application_instructorhours", "instructor_hours_id",
			` JOIN application_instructor i ON i.instructor_id=o.instructor_id JOIN bakney_user u ON u.user_id=i.user_id`+
				auditIndexOwner("u.user_id"), "instructor.user.sport_association"),
		"application.associatetutorrelation": {table: "application_associatetutorrelation", pk: "id", intPK: true,
			joins: ` JOIN application_associate a ON a.associate_id=o.associate_id` +
				` JOIN application_sportassociation sa ON sa.sport_association_id=a.sport_association_id`,
			path: "associate.sport_association"},
		"application.coursesubscription": {table: "application_coursesubscription", pk: "course_subscription_id", deleted: true,
			joins: auditIndexViaCourse, path: "course.sport_association"},
		"application.coursesubscriptioninstallment": {table: "application_coursesubscriptioninstallment",
			pk: "course_subscription_installment_id", deleted: true, grouped: true,
			joins: ` JOIN application_coursesubscription cs ON cs.course_subscription_id=o.course_subscription_id` +
				` JOIN application_course c ON c.course_id=cs.course_id` +
				` JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id`,
			path: "course_subscription.course.sport_association"},
		"application.medicalcertificate": auditIndexNone("application_medicalcertificate", "medical_id"),
		"application.medicalappointments": auditIndexVia("application_medicalappointments", "medical_appointments_id",
			auditIndexViaSubscription, "subscription.sport_association"),
		"application.subscriptiontoken": auditIndexVia("application_subscriptiontoken", "subscription_token_id",
			auditIndexViaSubscription, "subscription.sport_association"),
		"application.subscriptionfile": auditIndexVia("application_subscriptionfile", "subscription_file_id",
			auditIndexViaSubscription, "subscription.sport_association"),
		"application.subscriptiontransfer": auditIndexVia("application_subscriptiontransfer", "subscription_transfer_id",
			auditIndexViaSubscription, "subscription.sport_association"),
		"application.signature":    auditIndexNone("application_signature", "signature_id"),
		"application.soldproducts": auditIndexVia("application_soldproducts", "sold_product_id", auditIndexViaSubscription, "subscription.sport_association"),
		"application.campsandretreatsperiod": auditIndexVia("application_campsandretreatsperiod", "camps_and_retreats_period_id",
			auditIndexViaCamps, "camps_and_retreats.sport_association"),
		"application.campsandretreatsperiodsservice": auditIndexVia("application_campsandretreatsperiodsservice",
			"camps_and_retreats_period_service_id",
			` JOIN application_campsandretreatsperiod p ON p.camps_and_retreats_period_id=o.camps_and_retreats_period_id`+
				` JOIN application_campsandretreats cr ON cr.camps_and_retreats_id=p.camps_and_retreats_id`+
				` JOIN application_sportassociation sa ON sa.sport_association_id=cr.sport_association_id`,
			"period.camps_and_retreats.sport_association"),
		"application.campsandretreatssubscription": auditIndexVia("application_campsandretreatssubscription",
			"camps_and_retreats_subscription_id", auditIndexViaCamps, "camps_and_retreats.sport_association"),
		"application.campsandretreatssubscriptionperiod": auditIndexVia("application_campsandretreatssubscriptionperiod",
			"camps_and_retreats_subscription_period_id",
			` JOIN application_campsandretreatssubscription crs ON crs.camps_and_retreats_subscription_id=o.camps_and_retreats_subscription_id`+
				` JOIN application_campsandretreats cr ON cr.camps_and_retreats_id=crs.camps_and_retreats_id`+
				` JOIN application_sportassociation sa ON sa.sport_association_id=cr.sport_association_id`,
			"subscription.camps_and_retreats.sport_association"),
		"application.invoicerows": auditIndexVia("application_invoicerows", "invoice_row_id",
			` JOIN application_invoice inv ON inv.invoice_id=o.invoice_id`+
				` JOIN application_sportassociation sa ON sa.sport_association_id=inv.sport_association_id`,
			"invoice.sport_association"),
		"application.attendanceregistry": auditIndexVia("application_attendanceregistry", "attendance_registry_id",
			auditIndexViaCourse, "course.sport_association"),
		"application.attendanceday": auditIndexVia("application_attendanceday", "attendance_day_id",
			` JOIN application_attendanceregistry ar ON ar.attendance_registry_id=o.attendance_registry_id`+
				` JOIN application_course c ON c.course_id=ar.course_id`+
				` JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id`,
			"attendance_registry.course.sport_association"),
		"application.moduleresponses": auditIndexVia("application_moduleresponses", "module_response_id",
			` JOIN application_module m ON m.module_id=o.module_id`+
				` JOIN application_sportassociation sa ON sa.sport_association_id=m.sport_association_id`,
			"module.sport_association"),
		// CarnetSubscription has no carnet attribute: only the subscription branch applies.
		"application.carnetsubscription": auditIndexVia("application_carnetsubscription", "carnet_subscription_id",
			auditIndexViaSubscription, "subscription.sport_association"),
		"application.vatmanagement": auditIndexNone("application_vatmanagement", "vat_management_id"),
	}
	direct := [][2]string{
		{"application.associate", "application_associate:associate_id:dg"},
		{"application.group", "application_group:group_id"},
		{"application.paymentcategory", "application_paymentcategory:payment_category_id"},
		{"application.payment", "application_payment:payment_id:dg"},
		{"application.supplierandcustomers", "application_supplierandcustomers:supplier_id:dg"},
		{"application.subscription", "application_subscription:subscription_id:g"},
		{"application.subscriptionmembership", "application_subscriptionmembership:subscription_membership_id"},
		{"application.notificationtemplates", "application_notificationtemplates:notification_template_id"},
		{"application.material", "application_material:material_id"},
		{"application.tags", "application_tags:tag_id"},
		{"application.associatetrial", "application_associatetrial:associate_trial_id"},
		{"application.associateimportdraft", "application_associateimportdraft:associate_import_draft_id"},
		{"application.associateimportdraftstatus", "application_associateimportdraftstatus:associate_import_draft_status_id"},
		{"application.cumulativesubscriptiongymlinks", "application_cumulativesubscriptiongymlinks:cumulative_subscription_gym_links_id"},
		{"application.course", "application_course:course_id:dg"},
		{"application.coursetags", "application_coursetags:tag_id"},
		{"application.courselocation", "application_courselocation:course_location_id"},
		{"application.campsandretreats", "application_campsandretreats:camps_and_retreats_id"},
		{"application.invoice", "application_invoice:invoice_id:dg"},
		{"application.invoicesuppliers", "application_invoicesuppliers:invoice_supplier_id"},
		{"application.customerinvoice", "application_customerinvoice:customer_invoice_id"},
		{"application.balancesheet", "application_balancesheet:balance_sheet_id"},
		{"application.customaccounts", "application_customaccounts:custom_account_id"},
		{"application.customaccountstransfer", "application_customaccountstransfer:custom_account_transfer_id"},
		{"application.module", "application_module:module_id"},
		{"application.globalcalendarevents", "application_globalcalendarevents:global_calendar_id"},
		{"application.reminders", "application_reminders:reminders_id"},
		{"application.carnet", "application_carnet:carnet_id"},
		{"application.sportassociationmembershipcardconfiguration", "application_sportassociationmembershipcardconfiguration:sport_association_membership_card_configuration_id"},
		{"application.sportassociationheadquarter", "application_sportassociationheadquarter:sport_association_headquarter_id"},
		{"application.sportassociationmoduletemplates", "application_sportassociationmoduletemplates:sport_association_module_templates_id"},
		{"application.sportassociationmaterial", "application_sportassociationmaterial:sport_association_material_id"},
		{"application.sportassociationdocumentsarchive", "application_sportassociationdocumentsarchive:sport_association_documents_archive_id"},
		{"application.reminder", "application_reminder:reminder_id"},
		{"communications.message", "communications_message:message_id"},
		{"communications.automationworkflow", "communications_automationworkflow:automation_workflow_id"},
		{"communications.communicationconfiguration", "communications_communicationconfiguration:communication_configuration_id"},
		// Registered with direct_resolver but without a sport_association attribute.
		{"application.previewandcustomfeatures", "application_previewandcustomfeatures:preview_and_custom_features_id:-"},
		{"application.usersonboarding", "application_usersonboarding:users_onboarding_id:-"},
		{"application.collaborationinvites", "application_collaborationinvites:collaboration_invite_id:-"},
		{"application.userpartial", "bakney_user_partial:id:-"},
		{"application.family", "application_family:family_id:-"},
		{"application.institution", "application_institution:institution_id:-"},
		{"application.sentemails", "application_sentemails:id:-"},
		{"application.nurturingemailsplan", "nurturing_emails_plan:nurturing_plan_id:-"},
		{"application.nurturingemails", "nurturing_emails:nurturing_email_id:-"},
		{"application.billingplan", "billing_plan:billing_plan_id:-"},
		{"application.billingsubscription", "billing_subscription:billing_subscription_id:-"},
		{"application.billingpayment", "billing_payment:billing_payment_id:-"},
	}
	for _, item := range direct {
		parts := strings.Split(item[1], ":")
		resolver := auditIndexDirectModel(parts[0], parts[1])
		if len(parts) == 3 {
			switch parts[2] {
			case "dg":
				resolver.deleted, resolver.grouped = true, true
			case "g":
				resolver.grouped = true
			case "-":
				resolver = auditIndexNone(parts[0], parts[1])
			}
		}
		resolvers[item[0]] = resolver
	}
	return resolvers
}()

// AuditLogIndexResolverKeys lists the registered content types (for tests).
func AuditLogIndexResolverKeys() []string {
	keys := make([]string, 0, len(auditIndexResolvers))
	for key := range auditIndexResolvers {
		keys = append(keys, key)
	}
	return keys
}

// auditIndexEntry is the LogEntry state the resolution reads.
type auditIndexEntry struct {
	AppLabel string         `db:"app_label"`
	Model    string         `db:"model"`
	ObjectPK string         `db:"object_pk"`
	Changes  sql.NullString `db:"changes"`
	ActorID  sql.NullString `db:"actor_id"`
}

const auditIndexEntrySQL = `SELECT ct.app_label, ct.model, e.object_pk, e.changes::text AS changes, e.actor_id::text AS actor_id
 FROM auditlog_logentry e JOIN django_content_type ct ON ct.id=e.content_type_id`

// ResolveAuditLogIndex is resolve_sport_association_for_log_entry for the
// stored LogEntry id, with groupHeader as the request's X-Group-Id ("" when
// there is no request or header). It returns nil when nothing resolves.
// Python exceptions the Django function catches (unparseable keys and
// values) select the next fallback; database errors are returned.
func ResolveAuditLogIndex(ctx context.Context, q sqlx.QueryerContext, entryID int64, groupHeader string) (*AuditLogIndexResolution, error) {
	var entry auditIndexEntry
	if err := sqlx.GetContext(ctx, q, &entry, auditIndexEntrySQL+` WHERE e.id=$1`, entryID); err != nil {
		return nil, err
	}
	return resolveAuditIndexEntry(ctx, q, entry, groupHeader)
}

func resolveAuditIndexEntry(ctx context.Context, q sqlx.QueryerContext, entry auditIndexEntry, groupHeader string) (*AuditLogIndexResolution, error) {
	if resolver, ok := auditIndexResolvers[entry.AppLabel+"."+entry.Model]; ok && !resolver.none {
		association, err := resolver.resolve(ctx, q, entry.ObjectPK, groupHeader)
		if err != nil || association != "" {
			return auditIndexResult(association, resolver.path), err
		}
	}
	association, err := auditIndexFromChanges(ctx, q, entry.Changes)
	if err != nil || association != "" {
		return auditIndexResult(association, "changes_json.sport_association"), err
	}
	if !entry.ActorID.Valid {
		return nil, nil
	}
	err = sqlx.GetContext(ctx, q, &association, `SELECT sa.sport_association_id::text FROM bakney_user u
 JOIN application_sportassociation sa ON sa.user_id=u.user_id AND NOT sa.deleted
 WHERE u.user_id=$1 ORDER BY sa.sport_association_id LIMIT 1`, entry.ActorID.String)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return auditIndexResult(association, "actor.sport_association"), err
}

func auditIndexResult(association, path string) *AuditLogIndexResolution {
	if association == "" {
		return nil
	}
	return &AuditLogIndexResolution{AssociationID: association, Path: path}
}

// resolve is model_class.objects.get(pk=object_pk) followed by the resolver.
// A key the field rejects, a row the manager hides, a missing related row
// and a null relation all return "" (Django's next fallback).
func (r auditIndexResolver) resolve(ctx context.Context, q sqlx.QueryerContext, objectPK, groupHeader string) (string, error) {
	var key any
	if r.intPK {
		value, inRange, ok := dto.AuditIndexAutoPK(objectPK)
		if !ok || !inRange {
			return "", nil
		}
		key = value
	} else {
		value, ok := dto.AuditIndexUUIDText(objectPK)
		if !ok {
			return "", nil
		}
		key = value
	}
	query := `SELECT sa.sport_association_id::text FROM ` + r.table + ` o` + r.joins + ` WHERE o.` + r.pk + `=$1`
	arguments := []any{key}
	if r.deleted {
		query += ` AND NOT o.deleted`
	}
	if group := auditIndexGroup(groupHeader); r.grouped && group != "" {
		query += ` AND o.group_id=$2::uuid`
		arguments = append(arguments, group)
	}
	var association string
	err := sqlx.GetContext(ctx, q, &association, query+` ORDER BY sa.sport_association_id LIMIT 1`, arguments...)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return association, err
}

// auditIndexGroup is GroupAwareManager's filter(group=X-Group-Id): an empty
// header applies no filter and a value the UUID field rejects is ignored.
func auditIndexGroup(header string) string {
	if header == "" {
		return ""
	}
	group, ok := dto.AuditIndexUUIDText(header)
	if !ok {
		return ""
	}
	return group
}

// auditIndexFromChanges is the changes['sport_association'] fallback: the
// old value then the new one, skipping falsy and "none" values; a value
// UUIDField rejects ends the fallback.
func auditIndexFromChanges(ctx context.Context, q sqlx.QueryerContext, stored sql.NullString) (string, error) {
	if !stored.Valid {
		return "", nil
	}
	decoder := json.NewDecoder(bytes.NewReader([]byte(stored.String)))
	decoder.UseNumber()
	var changes any
	if err := decoder.Decode(&changes); err != nil {
		return "", err
	}
	object, ok := changes.(map[string]any)
	if !ok {
		return "", nil
	}
	data, ok := object["sport_association"]
	if !ok {
		return "", nil
	}
	candidates := []any{data}
	if list, isList := data.([]any); isList {
		candidates = nil
		for index := 0; index < len(list) && index < 2; index++ {
			candidates = append(candidates, list[index])
		}
	}
	for _, candidate := range candidates {
		if !dto.AuditIndexTruthy(candidate) {
			continue
		}
		if text, isText := candidate.(string); isText && strings.ToLower(text) == "none" {
			continue
		}
		id, valid := dto.AuditIndexUUIDValue(candidate)
		if !valid {
			return "", nil
		}
		var association string
		err := sqlx.GetContext(ctx, q, &association, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE sport_association_id=$1::uuid AND NOT deleted ORDER BY sport_association_id LIMIT 1`, id)
		if err == nil {
			return association, nil
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return "", err
		}
	}
	return "", nil
}

// IndexAuditLogEntry is create_audit_log_index for a new LogEntry: nothing
// when an index row exists or nothing resolves, else one AuditLogIndex row.
// Callers run it inside their write transaction, so a failure rolls back
// the audited change (Django's atomic_audit_mutation mode).
func IndexAuditLogEntry(ctx context.Context, tx *sqlx.Tx, entryID int64, groupHeader string, at time.Time) (*AuditLogIndexResolution, error) {
	var exists bool
	if err := tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_auditlogindex WHERE log_entry_id=$1)`, entryID); err != nil || exists {
		return nil, err
	}
	resolution, err := ResolveAuditLogIndex(ctx, tx, entryID, groupHeader)
	if err != nil || resolution == nil {
		return nil, err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO application_auditlogindex
 (audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at) VALUES($1,$2,$3,$4,$5)`,
		uuid.NewString(), entryID, resolution.AssociationID, resolution.Path, at)
	return resolution, err
}

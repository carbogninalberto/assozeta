package service

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

var (
	ErrAdminUpdateNotSuperuser = errors.New("admin update requires a superuser")
	// ErrAdminUpdateMissingBilling is the bare 400 for a missing or null billing_subscription.
	ErrAdminUpdateMissingBilling = errors.New("admin update billing data missing")
	// ErrAdminUpdateServer stands for the view's uncaught exceptions (500).
	ErrAdminUpdateServer = errors.New("admin update raised")
)

type AssociationAdmin struct {
	store domain.AssociationAdminStore
	now   func() time.Time
}

func NewAssociationAdmin(store domain.AssociationAdminStore, now func() time.Time) *AssociationAdmin {
	return &AssociationAdmin{store: store, now: now}
}

// Update follows sport_association_admin_update. Raw request values are
// assigned and saved without validation; auditlog diffs the raw in-memory
// values on pre_save, so each audit entry is written before its save and
// survives a value the save then rejects. Without a request transaction the
// billing save also survives a failed notes save.
func (s *AssociationAdmin) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if identity.ScopeUser == nil || !identity.ScopeUser.Superuser {
		return ErrAdminUpdateNotSuperuser
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginAssociationAdmin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	owner, notes, err := tx.AdminAssociation(ctx, id, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	billing, err := tx.FirstBillingForUpdate(ctx, owner)
	if err != nil {
		return err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return ErrAdminUpdateServer
	}
	value, present := object.Lookup("billing_subscription")
	if !present || value == nil {
		return ErrAdminUpdateMissingBilling
	}
	data, ok := value.(domain.RequestObject)
	if !ok {
		return ErrAdminUpdateServer
	}
	auditContext := paymentCategoryAuditContext(identity, audit)
	at := s.now().UTC().Truncate(time.Microsecond)
	write, changes, err := adminBillingDiff(billing, data)
	if err != nil {
		return err
	}
	if len(changes) > 0 {
		event := domain.AdminAudit{Model: "billingsubscription", ObjectID: billing.ID,
			Representation: "BillingSubscription object (" + billing.ID + ")", Changes: changes, Context: auditContext, At: at}
		// No resolver or changes_json match: the actor's association, if any.
		if association, err := tx.ActiveAssociationOf(ctx, identity.Actor.ID); err != nil {
			return err
		} else if association != nil {
			path := "actor.sport_association"
			event.AssociationID, event.ResolutionPath = association, &path
		}
		if err = tx.AppendAdminAudit(ctx, event); err != nil {
			return err
		}
	}
	if write == nil {
		// A raw value the field cannot prepare raises at save, after the audit.
		return s.commitThen(tx, ErrAdminUpdateServer)
	}
	if err = tx.UpdateBilling(ctx, billing.ID, *write); err != nil {
		return s.commitThen(tx, ErrAdminUpdateServer)
	}
	// Notes: a raw value, str() for non-strings; any exception is the 400.
	updated := notes
	if raw, present := object.Lookup("notes"); present {
		updated = nil
		if raw != nil {
			text := dto.PythonStr(raw)
			updated = &text
		}
	}
	render := func(value *string) string {
		if value == nil {
			return "None"
		}
		return *value
	}
	if render(updated) != render(notes) {
		self := "self"
		if err = tx.AppendAdminAudit(ctx, domain.AdminAudit{Model: "sportassociation", ObjectID: id,
			Representation: "SportAssociation object (" + id + ")", Changes: map[string][2]string{"notes": {render(notes), render(updated)}},
			Context: auditContext, AssociationID: &id, ResolutionPath: &self, At: at}); err != nil {
			return s.commitThen(tx, domain.ErrAdminUpdateNotes)
		}
	}
	if err = tx.UpdateNotes(ctx, id, updated); err != nil {
		return s.commitThen(tx, domain.ErrAdminUpdateNotes)
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *AssociationAdmin) commitThen(tx domain.AssociationAdminTransaction, outcome error) error {
	if err := tx.Commit(); err != nil {
		return err
	}
	return outcome
}

// adminBillingDiff assigns billing_data.get(field, current) for each field
// and renders auditlog's pre_save diff. write is nil when a value cannot be
// prepared for the save. An ends_on string DateTimeField.to_python rejects
// raises during the diff itself, before any audit entry.
func adminBillingDiff(current domain.AdminBilling, data domain.RequestObject) (*domain.AdminBillingWrite, map[string][2]string, error) {
	write := domain.AdminBillingWrite{AutoRenewal: current.AutoRenewal, RenewalType: current.RenewalType,
		EndsOn: current.EndsOn, BillingPlan: current.BillingPlan}
	changes := map[string][2]string{}
	prepared := true
	pythonBool := map[bool]string{true: "True", false: "False"}
	if value, present := data.Lookup("auto_renewal"); present {
		if text := dto.PythonStr(value); text != pythonBool[current.AutoRenewal] {
			changes["auto_renewal"] = [2]string{pythonBool[current.AutoRenewal], text}
		}
		parsed, err := dto.DjangoBooleanPrep(value)
		prepared = prepared && err == nil
		write.AutoRenewal = parsed
	}
	if value, present := data.Lookup("renewal_type"); present {
		old := strconv.FormatInt(current.RenewalType, 10)
		if text := dto.PythonStr(value); text != old {
			changes["renewal_type"] = [2]string{old, text}
		}
		parsed, err := dto.PythonIntPrep(value)
		prepared = prepared && err == nil
		write.RenewalType = parsed
	}
	if value, present := data.Lookup("ends_on"); present {
		old := "None"
		if current.EndsOn != nil {
			old = paymentCategoryAuditTime(*current.EndsOn)
		}
		var updated *time.Time
		rendered := "None"
		switch v := value.(type) {
		case nil:
		case string:
			parsed, ok := dto.DjangoDateTimePrep(v)
			if !ok {
				return nil, nil, ErrAdminUpdateServer
			}
			updated, rendered = &parsed, paymentCategoryAuditTime(parsed)
		default:
			// to_python raises TypeError, which auditlog tolerates; the save does not.
			rendered, prepared = dto.PythonStr(v), false
		}
		if rendered != old {
			changes["ends_on"] = [2]string{old, rendered}
		}
		write.EndsOn = updated
	}
	if value, present := data.Lookup("billing_plan"); present {
		// The foreign key is compared as its raw attname value.
		old := "None"
		if current.BillingPlan != nil {
			old = *current.BillingPlan
		}
		text, isString := value.(string)
		rendered := dto.PythonStr(value)
		if value == nil {
			rendered = "None"
		}
		// strings_only keeps None and numbers unconverted, so only an equal
		// string (or None with no plan) compares equal.
		if (value == nil) != (current.BillingPlan == nil) || (value != nil && (!isString || text != old)) {
			changes["billing_plan"] = [2]string{old, rendered}
		}
		write.BillingPlan = nil
		if value != nil {
			// UUIDField.to_python: uuid.UUID(hex=...) for strings, UUID(int=...)
			// for integers and booleans; anything else raises.
			var id string
			var err error = ErrAdminUpdateServer
			if isString {
				if parsed, parseErr := paymentInfoParseUUID(text); parseErr == nil {
					id, err = parsed.String(), nil
				}
			} else if _, isNumber := value.(json.Number); isNumber || value == true || value == false {
				id, err = dto.DjangoUUIDFromInt(value)
			}
			prepared = prepared && err == nil
			write.BillingPlan = &id
		}
	}
	if !prepared {
		return nil, changes, nil
	}
	return &write, changes, nil
}

package service

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"math/big"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/stripeapi"
)

var (
	// ErrStripePaymentNotFound is stripe_pay's NotFound('payment not found').
	ErrStripePaymentNotFound = errors.New("payment not found")
	// ErrStripeOffline is the 409 {'error': 'Online payments are not configured.'}.
	ErrStripeOffline = errors.New("online payments are not configured")
	// ErrStripeNoPayments is multiple-pay's 400 {'data': {'msg': 'no payments provided.'}}.
	ErrStripeNoPayments = errors.New("no payments provided")
	// ErrStripeWebhookUnconfigured is the webhook's 503.
	ErrStripeWebhookUnconfigured = errors.New("stripe webhook is not configured")
	// ErrStripeWebhookRejected is the webhook's empty 400.
	ErrStripeWebhookRejected = errors.New("stripe webhook rejected")
)

// StripeEnvironmentKeys are the STRIPE_* settings effective_integration('stripe')
// falls back to when the instance has no stored override.
type StripeEnvironmentKeys struct {
	PublicKey, SecretKey, WebhookSecret string
}

// StripeRoutes ports stripe_pay, stripe_multiple_pay and stripe_webhook
// (BE/application/views/stripe_views.py) with mark_payment_as_paid for
// payments that already have an invoice.
type StripeRoutes struct {
	store        domain.StripeRoutesStore
	config       domain.SocialConfigurationReader
	availability *StripeAvailability
	env          StripeEnvironmentKeys
	secrets      []string
	client       *stripeapi.Client
	now          func() time.Time
	log          *slog.Logger
}

func NewStripeRoutes(store domain.StripeRoutesStore, config domain.SocialConfigurationReader, env StripeEnvironmentKeys, secretKey string,
	client *stripeapi.Client, now func() time.Time) *StripeRoutes {
	availability := NewStripeAvailability(config, StripeEnvironment{PublicKey: env.PublicKey != "", Secret: env.SecretKey != "",
		Webhook: env.PublicKey != "" || env.SecretKey != "" || env.WebhookSecret != ""}, secretKey)
	return &StripeRoutes{store: store, config: config, availability: availability, env: env, secrets: []string{secretKey}, client: client,
		now: now, log: slog.Default()}
}

// StripeRequest carries the request values the views read.
type StripeRequest struct {
	GroupHeader string
	Audit       domain.LoginAuditContext
}

func (s *StripeRoutes) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

func stripeQuery(identity domain.ResolvedIdentity, group *string) domain.StripePaymentQuery {
	return domain.StripePaymentQuery{Scope: paymentStateScope(identity), Group: group}
}

// stripeAuditContext is auditlog's actor: the authenticated user, none for anonymous requests.
func stripeAuditContext(identity domain.ResolvedIdentity, audit domain.LoginAuditContext) domain.LoginAuditContext {
	if identity.Actor == nil {
		audit.ActorID, audit.ActorEmail, audit.AdditionalData = nil, nil, nil
		return audit
	}
	return paymentCategoryAuditContext(identity, audit)
}

// ---------------------------------------------------------------------------
// Integration settings (BE/application/utils/stripe_utils.py)
// ---------------------------------------------------------------------------

var errStripeImproperlyConfigured = errors.New("stripe integration credential cannot be decrypted")

// effective is effective_integration('stripe') with decrypt=True.
func (s *StripeRoutes) effective(ctx context.Context) (map[string]any, error) {
	row, err := s.config.SocialConfiguration(ctx)
	if err != nil {
		return nil, err
	}
	if row != nil && row.SelfHosted {
		integrations, err := socialJSON(row.Integrations)
		if err != nil {
			return nil, err
		}
		if dto.PythonTruthy(integrations) {
			all, ok := integrations.(map[string]any)
			if !ok {
				return nil, ErrSocialConfiguration
			}
			if record, present := all["stripe"]; present {
				fields, ok := record.(map[string]any)
				if !ok {
					return nil, ErrSocialConfiguration
				}
				if value, present := fields["settings"]; present && value != nil {
					settings, ok := value.(map[string]any)
					if !ok {
						return nil, ErrSocialConfiguration
					}
					values := map[string]any{}
					for key, item := range settings {
						values[key] = item
					}
					for _, key := range []string{"secret_key", "webhook_secret"} {
						item := values[key]
						if !dto.PythonTruthy(item) {
							values[key] = ""
							continue
						}
						text, ok := item.(string)
						if !ok {
							return nil, ErrSocialConfiguration
						}
						plain, err := DecryptIntegrationSecret(text, s.secrets)
						if err != nil {
							return nil, errStripeImproperlyConfigured
						}
						values[key] = plain
					}
					return values, nil
				}
			}
		}
	}
	env := s.env
	return map[string]any{"public_key": env.PublicKey, "secret_key": env.SecretKey, "webhook_secret": env.WebhookSecret,
		"enabled": env.PublicKey != "" || env.SecretKey != "" || env.WebhookSecret != ""}, nil
}

// enabledValue is value[key] if value['enabled'] else ” (a missing key is a KeyError).
func enabledValue(values map[string]any, key string) (any, error) {
	enabled, present := values["enabled"]
	if !present {
		return nil, ErrSocialConfiguration
	}
	if !dto.PythonTruthy(enabled) {
		return "", nil
	}
	value, present := values[key]
	if !present {
		return nil, ErrSocialConfiguration
	}
	return value, nil
}

// requestKey is stripe_request_options()['api_key']: an empty key raises ImproperlyConfigured.
func (s *StripeRoutes) requestKey(ctx context.Context) (string, error) {
	values, err := s.effective(ctx)
	if err != nil {
		return "", err
	}
	value, err := enabledValue(values, "secret_key")
	if err != nil {
		return "", err
	}
	key, ok := value.(string)
	if !ok || key == "" {
		// A non-string stored key or a disabled/blank one never reaches the SDK.
		return "", domain.ErrStripeServer
	}
	return key, nil
}

// session opens the SDK state of this request lazily (the key is read at each call in Django;
// one request reads the same configuration).
func (s *StripeRoutes) session(ctx context.Context, current **stripeapi.Session) (*stripeapi.Session, error) {
	key, err := s.requestKey(ctx)
	if err != nil {
		return nil, err
	}
	if *current == nil {
		*current = s.client.Session(key)
	}
	return *current, nil
}

// online is online_payments_available(payment.sport_association).
func (s *StripeRoutes) online(ctx context.Context, payment *domain.StripePayment) (bool, error) {
	if !payment.HasAssociation || payment.OwnerOnline == nil {
		return false, domain.ErrStripeServer
	}
	if !*payment.OwnerOnline {
		return false, nil
	}
	return s.availability.DirectCredentialsConfigured(ctx)
}

// ---------------------------------------------------------------------------
// Values
// ---------------------------------------------------------------------------

// cents is int(amount * 100) for a Decimal amount (truncated toward zero).
func cents(amounts ...string) (int64, error) {
	total := new(big.Rat)
	for _, amount := range amounts {
		value, ok := new(big.Rat).SetString(amount)
		if !ok {
			return 0, domain.ErrStripeServer
		}
		total.Add(total, value)
	}
	total.Mul(total, big.NewRat(100, 1))
	quotient := new(big.Int).Quo(total.Num(), total.Denom())
	if !quotient.IsInt64() {
		return 0, domain.ErrStripeNotPorted
	}
	return quotient.Int64(), nil
}

// attribute is StripeObject attribute access: a missing key is an AttributeError.
func attribute(object stripeapi.Object, name string) (any, error) {
	value, present := object.Lookup(name)
	if !present {
		return nil, domain.ErrStripeServer
	}
	return value, nil
}

func succeeded(object stripeapi.Object) (bool, error) {
	status, err := attribute(object, "status")
	if err != nil {
		return false, err
	}
	return status == "succeeded", nil
}

// equalsInt is Python's value == n for a decoded JSON value.
func equalsInt(value any, n int64) bool {
	switch v := value.(type) {
	case json.Number:
		parsed, ok := new(big.Float).SetPrec(2048).SetString(string(v))
		return ok && parsed.Cmp(new(big.Float).SetInt64(n)) == 0
	case bool:
		return (v && n == 1) || (!v && n == 0)
	}
	return false
}

// clientSecret is payment_intent.client_secret or None.
func clientSecret(object stripeapi.Object) (any, error) {
	value, err := attribute(object, "client_secret")
	if err != nil {
		return nil, err
	}
	if !dto.PythonTruthy(value) {
		return nil, nil
	}
	return json.RawMessage(dto.PythonJSONDumps(value)), nil
}

// stripeID is payment_intent.stripe_id (the object's id).
func stripeID(object stripeapi.Object) (string, error) {
	value, present := object.Lookup("id")
	text, ok := value.(string)
	if !present || !ok {
		return "", domain.ErrStripeNotPorted
	}
	return text, nil
}

// pythonText is f'{value}' for a nullable text column.
func pythonText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// prefix is value[:3] (code points); None is a TypeError.
func prefix(value *string) (string, error) {
	if value == nil {
		return "", domain.ErrStripeServer
	}
	runes := []rune(*value)
	if len(runes) > 3 {
		runes = runes[:3]
	}
	return string(runes), nil
}

// methodsValue decodes sport_association.stripe_available_methods (None for SQL or JSON null).
func methodsValue(raw *string) (any, error) {
	if raw == nil {
		return nil, nil
	}
	value, err := dto.ParseProfileTablesJSON([]byte(*raw))
	if err != nil {
		return nil, domain.ErrStripeServer
	}
	if value != nil {
		// Only encodings this port reproduces are accepted before any write.
		if _, err := stripeapi.Encode([]stripeapi.Param{{Key: "payment_method_types", Value: value}}); err != nil {
			return nil, domain.ErrStripeNotPorted
		}
	}
	return value, nil
}

// ---------------------------------------------------------------------------
// mark_payment_as_paid
// ---------------------------------------------------------------------------

// markable reports whether mark_payment_as_paid on this payment stays within
// the ported path: an unpaid payment without an invoice creates one, prints it
// and notifies (not ported).
func markable(payment *domain.StripePayment) error {
	if !payment.Paid && payment.InvoiceID == nil {
		return domain.ErrStripeNotPorted
	}
	return nil
}

// mark is mark_payment_as_paid(request or None, payment, ...) for a payment
// with an invoice: atomically the locked payment's first supplier invoice is
// marked paid now (the payment itself is not saved); afterwards
// payment.user.user_id is read for the log (no user: an error after the commit).
func (s *StripeRoutes) mark(ctx context.Context, identity domain.ResolvedIdentity, request StripeRequest, payment *domain.StripePayment,
	withRequest bool) error {
	if withRequest && payment.UserID == nil && payment.AssociateID == nil {
		return domain.ErrStripeServer
	}
	tx, err := s.store.BeginStripe(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row, err := tx.LockStripePayment(ctx, payment.ID, supplierGroup(request.GroupHeader))
	if err != nil {
		return err
	}
	if row == nil {
		return domain.ErrStripeServer
	}
	if err := markable(row); err != nil {
		return err
	}
	invoice, err := tx.SupplierInvoice(ctx, row.ID)
	if err != nil {
		return err
	}
	if invoice != nil {
		now := s.at()
		changes := map[string][2]string{}
		if !invoice.Paid {
			changes["paid"] = [2]string{"False", "True"}
		}
		if before, after := courseSubscriptionAuditTime(invoice.PaymentDate), courseSubscriptionAuditTime(&now); before != after {
			changes["payment_date"] = [2]string{before, after}
		}
		if err := tx.SaveSupplierInvoice(ctx, invoice.ID, true, &now); err != nil {
			return err
		}
		if err := tx.AppendSupplierInvoiceAudit(ctx, *invoice, changes, stripeAuditContext(identity, request.Audit), now); err != nil {
			return err
		}
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	if row.UserID == nil {
		return domain.ErrStripeServer
	}
	return nil
}

// marks runs planned marks in order, then the error that stopped the planning.
func (s *StripeRoutes) marks(ctx context.Context, identity domain.ResolvedIdentity, request StripeRequest, targets []*domain.StripePayment,
	withRequest bool, stopped error) error {
	for _, target := range targets {
		if err := markable(target); err != nil {
			return err
		}
	}
	for _, target := range targets {
		if err := s.mark(ctx, identity, request, target, withRequest); err != nil {
			return err
		}
	}
	return stopped
}

// saveIntent is payment.payment_intent_id = value; payment.save() (with
// Payment.save()'s defaults) and its auditlog entry, in its own transaction.
func (s *StripeRoutes) saveIntent(ctx context.Context, identity domain.ResolvedIdentity, request StripeRequest, payment *domain.StripePayment,
	intent *string) error {
	tx, err := s.store.BeginStripe(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	changes := map[string][2]string{}
	if !samePointer(payment.IntentID, intent) {
		changes["payment_intent_id"] = [2]string{pythonOptional(payment.IntentID), pythonOptional(intent)}
	}
	if err := paymentSaveDefaults(ctx, tx, &payment.PaymentSaveRow, changes); err != nil {
		return err
	}
	if err := tx.SavePaymentIntent(ctx, payment.ID, intent, payment.CategoryID, payment.Description); err != nil {
		return err
	}
	payment.IntentID = intent
	if len(changes) > 0 {
		if err := tx.AppendPaymentAudit(ctx, domain.PaymentArchiveEvent{PaymentID: payment.ID, AssociationID: payment.AssociationID,
			Changes: changes, Context: stripeAuditContext(identity, request.Audit), At: s.at()}); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// ---------------------------------------------------------------------------
// stripe/pay/<payment_id>
// ---------------------------------------------------------------------------

var stripeMarked = map[string]any{"data": map[string]any{"msg": "payment mark as payed.", "status": "paid"}}

// Pay follows stripe_pay.
func (s *StripeRoutes) Pay(ctx context.Context, identity domain.ResolvedIdentity, request StripeRequest, rawID string, intentQuery *string) (map[string]any, error) {
	group := supplierGroup(request.GroupHeader)
	query := stripeQuery(identity, group)
	var stripe *stripeapi.Session
	if intentQuery != nil && *intentQuery != "" {
		payment, err := s.store.PaymentByIntent(ctx, *intentQuery, query)
		if err != nil {
			return nil, err
		}
		if payment != nil {
			available, err := s.online(ctx, payment)
			if err != nil {
				return nil, err
			}
			if !available {
				return nil, ErrStripeOffline
			}
			session, err := s.session(ctx, &stripe)
			if err != nil {
				return nil, err
			}
			intent, err := session.RetrievePaymentIntent(ctx, *payment.IntentID)
			if err != nil {
				return nil, err
			}
			done, err := succeeded(intent)
			if err != nil {
				return nil, err
			}
			if done {
				metadata, err := attribute(intent, "metadata")
				if err != nil {
					return nil, err
				}
				if metadata != nil {
					var targets []*domain.StripePayment
					if !payment.Paid {
						targets = append(targets, payment)
					}
					stopped := func() error {
						keys, ok := metadata.(domain.RequestObject)
						if !ok {
							return domain.ErrStripeServer
						}
						for _, field := range keys {
							id, err := paymentInfoParseUUID(field.Name)
							if err != nil {
								return domain.ErrStripeServer
							}
							row, err := s.store.PaymentByID(ctx, id.String(), query)
							if err != nil {
								return err
							}
							if row != nil && !row.Paid {
								targets = append(targets, row)
							}
						}
						return nil
					}()
					if err := s.marks(ctx, identity, request, targets, true, stopped); err != nil {
						return nil, err
					}
					return map[string]any{"data": map[string]any{"msg": "payment already paid."}}, nil
				}
				if !payment.Paid {
					if err := s.marks(ctx, identity, request, []*domain.StripePayment{payment}, true, nil); err != nil {
						return nil, err
					}
					return stripeMarked, nil
				}
			}
		}
	}
	id, err := messageReadsParseUUID(rawID)
	if err != nil {
		return nil, err
	}
	payment, err := s.store.PaymentByID(ctx, id, query)
	if err != nil {
		return nil, err
	}
	if payment == nil {
		return nil, ErrStripePaymentNotFound
	}
	available, err := s.online(ctx, payment)
	if err != nil {
		return nil, err
	}
	if !available {
		return nil, ErrStripeOffline
	}
	if payment.Paid {
		return stripeMarked, nil
	}
	amount, err := cents(payment.Amount)
	if err != nil {
		return nil, err
	}
	var intent stripeapi.Object
	if payment.IntentID != nil && *payment.IntentID != "" {
		session, err := s.session(ctx, &stripe)
		if err != nil {
			return nil, err
		}
		retrieved, err := session.RetrievePaymentIntent(ctx, *payment.IntentID)
		var failure *stripeapi.Error
		switch {
		case errors.As(err, &failure) && failure.InvalidRequest:
			// except InvalidRequestError: payment_intent = None
		case err != nil:
			return nil, err
		default:
			done, err := succeeded(retrieved)
			if err != nil {
				return nil, err
			}
			if done {
				if err := s.marks(ctx, identity, request, []*domain.StripePayment{payment}, true, nil); err != nil {
					return nil, err
				}
				return stripeMarked, nil
			}
			value, err := attribute(retrieved, "amount")
			if err != nil {
				return nil, err
			}
			intent = retrieved
			if !equalsInt(value, amount) {
				intent = nil
				if err := s.saveIntent(ctx, identity, request, payment, nil); err != nil {
					return nil, err
				}
			}
		}
	}
	if intent == nil {
		if payment.AssociateID == nil || payment.UserID == nil || !payment.HasAssociation {
			return nil, domain.ErrStripeServer
		}
		methods, err := methodsValue(payment.Methods)
		if err != nil {
			return nil, err
		}
		description := "Associato: " + pythonText(payment.AssociateFirst) + " " + pythonText(payment.AssociateLast) + " - Utente: " +
			pythonText(payment.UserFirst) + " " + pythonText(payment.UserLast) + " - Pagamento di " + payment.Amount + " euro per " +
			pythonText(payment.Denomination)
		session, err := s.session(ctx, &stripe)
		if err != nil {
			return nil, err
		}
		created, err := session.CreatePaymentIntent(ctx, []stripeapi.Param{{Key: "amount", Value: amount}, {Key: "currency", Value: "eur"},
			{Key: "description", Value: description}, {Key: "payment_method_types", Value: methods}})
		if err != nil {
			return nil, err
		}
		identifier, err := stripeID(created)
		if err != nil {
			return nil, err
		}
		if err := s.saveIntent(ctx, identity, request, payment, &identifier); err != nil {
			return nil, err
		}
		intent = created
	}
	secret, err := clientSecret(intent)
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": map[string]any{"client_secret": secret}}, nil
}

// ---------------------------------------------------------------------------
// stripe/multiple-pay
// ---------------------------------------------------------------------------

// MultiplePay follows stripe_multiple_pay.
func (s *StripeRoutes) MultiplePay(ctx context.Context, identity domain.ResolvedIdentity, request StripeRequest, body any) (map[string]any, error) {
	data, ok := body.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrStripeServer
	}
	var payments any = []any{}
	if value, present := data.Lookup("payments"); present {
		payments = value
	}
	// len(request.data.get("payments", [])) in the log line.
	switch payments.(type) {
	case string, []any, domain.RequestObject:
	default:
		return nil, domain.ErrStripeServer
	}
	if !dto.PythonTruthy(payments) {
		return nil, ErrStripeNoPayments
	}
	list, ok := payments.([]any)
	if !ok {
		// A string or a dict is iterated (characters, keys) and indexed with a str.
		return nil, domain.ErrStripeServer
	}
	group := supplierGroup(request.GroupHeader)
	query := stripeQuery(identity, group)
	rendered := make([]any, len(list))
	items := make([]domain.RequestObject, 0, len(list))
	if _, ids := list[0].(string); ids {
		values := []string{}
		for _, item := range list {
			text, ok := item.(string)
			if !ok {
				return nil, domain.ErrStripeNotPorted
			}
			id, err := paymentInfoParseUUID(text)
			if err != nil {
				return nil, domain.ErrStripeServer
			}
			values = append(values, id.String())
		}
		found, err := s.store.PaymentIDs(ctx, values, false, query)
		if err != nil {
			return nil, err
		}
		rendered = make([]any, 0, len(found))
		tx, err := s.store.BeginStripe(ctx)
		if err != nil {
			return nil, err
		}
		for _, id := range found {
			render, err := tx.RenderPayment(ctx, id, group)
			if err != nil {
				tx.Rollback()
				return nil, err
			}
			item, err := dto.PaymentStateItem(render)
			if err != nil {
				tx.Rollback()
				return nil, err
			}
			rendered = append(rendered, item)
			// The serialized sport_association (a UUID string or null).
			var association *string
			encoded, err := json.Marshal(item["sport_association"])
			if err == nil {
				err = json.Unmarshal(encoded, &association)
			}
			if err != nil {
				tx.Rollback()
				return nil, err
			}
			var associationValue any
			if association != nil {
				associationValue = *association
			}
			items = append(items, domain.RequestObject{{Name: "payment_id", Value: id}, {Name: "sport_association", Value: associationValue}})
		}
		tx.Rollback()
	} else {
		for index, item := range list {
			object, ok := item.(domain.RequestObject)
			if !ok {
				return nil, domain.ErrStripeServer
			}
			rendered[index] = json.RawMessage(dto.PythonJSONDumps(item))
			items = append(items, object)
		}
	}
	// payments_groups: the first association key's payments, in order.
	if len(items) == 0 {
		return nil, domain.ErrStripeServer
	}
	var keys []any
	for _, item := range items {
		value, present := item.Lookup("sport_association")
		if !present {
			return nil, domain.ErrStripeServer
		}
		switch value.(type) {
		case nil, string:
		default:
			return nil, domain.ErrStripeNotPorted
		}
		keys = append(keys, value)
	}
	first := keys[0]
	var firstGroup []domain.RequestObject
	for index, item := range items {
		if keys[index] == first {
			firstGroup = append(firstGroup, item)
		}
	}
	var checkout any = ""
	if text, ok := first.(string); ok {
		id, err := paymentInfoParseUUID(text)
		if err != nil {
			return nil, domain.ErrStripeServer
		}
		exists, info, err := s.store.AssociationCheckout(ctx, id.String(), paymentStateScope(identity))
		if err != nil {
			return nil, err
		}
		if exists {
			checkout = info
			if info != nil {
				checkout = *info
			}
		}
	}
	var stripe *stripeapi.Session
	var methods any = []any{"card", "sepa_debit"}
	var targets []*domain.StripePayment
	var ids []string
	stopped := func() error {
		for _, item := range firstGroup {
			value, present := item.Lookup("payment_id")
			if !present {
				return domain.ErrStripeServer
			}
			text, ok := value.(string)
			if !ok {
				return domain.ErrStripeNotPorted
			}
			id, err := paymentInfoParseUUID(text)
			if err != nil {
				return domain.ErrStripeServer
			}
			ids = append(ids, id.String())
			payment, err := s.store.PaymentByID(ctx, id.String(), query)
			if err != nil {
				return err
			}
			if payment == nil {
				return domain.ErrStripeServer
			}
			available, err := s.online(ctx, payment)
			if err != nil {
				return err
			}
			if !available {
				return ErrStripeOffline
			}
			stored, err := methodsValue(payment.Methods)
			if err != nil {
				return err
			}
			if stored != nil {
				methods = stored
			}
			if payment.IntentID != nil && *payment.IntentID != "" && !payment.Paid {
				session, err := s.session(ctx, &stripe)
				if err != nil {
					return err
				}
				intent, err := session.RetrievePaymentIntent(ctx, *payment.IntentID)
				if err != nil {
					return err
				}
				done, err := succeeded(intent)
				if err != nil {
					return err
				}
				if done {
					targets = append(targets, payment)
				}
			}
		}
		return nil
	}()
	if err := s.marks(ctx, identity, request, targets, true, stopped); err != nil {
		return nil, err
	}
	unpaidIDs, err := s.store.PaymentIDs(ctx, ids, true, query)
	if err != nil {
		return nil, err
	}
	var unpaid []*domain.StripePayment
	var amounts []string
	metadata := domain.RequestObject{}
	description := ""
	for _, id := range unpaidIDs {
		payment, err := s.store.PaymentByID(ctx, id, query)
		if err != nil {
			return nil, err
		}
		if payment == nil {
			return nil, domain.ErrStripeServer
		}
		unpaid = append(unpaid, payment)
		amounts = append(amounts, payment.Amount)
	}
	amount, err := cents(amounts...)
	if err != nil {
		return nil, err
	}
	for _, payment := range unpaid {
		metadata = append(metadata, domain.RequestField{Name: payment.ID, Value: payment.Amount})
		if payment.AssociateID == nil {
			return nil, domain.ErrStripeServer
		}
		first, err := prefix(payment.AssociateFirst)
		if err != nil {
			return nil, err
		}
		last, err := prefix(payment.AssociateLast)
		if err != nil {
			return nil, err
		}
		description += first + "-" + last + "-€" + payment.Amount + "|"
	}
	session, err := s.session(ctx, &stripe)
	if err != nil {
		return nil, err
	}
	intent, err := session.CreatePaymentIntent(ctx, []stripeapi.Param{{Key: "amount", Value: amount}, {Key: "currency", Value: "eur"},
		{Key: "description", Value: description}, {Key: "payment_method_types", Value: methods}, {Key: "metadata", Value: metadata}})
	if err != nil {
		return nil, err
	}
	identifier, err := stripeID(intent)
	if err != nil {
		return nil, err
	}
	for _, payment := range unpaid {
		if err := s.saveIntent(ctx, identity, request, payment, &identifier); err != nil {
			return nil, err
		}
	}
	secret, err := clientSecret(intent)
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": map[string]any{"client_secret": secret, "info": map[string]any{"checkout_info": checkout}},
		"payments": rendered}, nil
}

// ---------------------------------------------------------------------------
// stripe/webhook
// ---------------------------------------------------------------------------

// WebhookSecret is stripe_webhook_secret().
func (s *StripeRoutes) WebhookSecret(ctx context.Context) (string, error) {
	values, err := s.effective(ctx)
	if err != nil {
		return "", err
	}
	value, err := enabledValue(values, "webhook_secret")
	if err != nil {
		return "", err
	}
	if !dto.PythonTruthy(value) {
		return "", ErrStripeWebhookUnconfigured
	}
	secret, ok := value.(string)
	if !ok {
		return "", domain.ErrStripeServer
	}
	return secret, nil
}

// Webhook follows stripe_webhook after the secret: construct_event, then the
// event type. charge.succeeded marks the unpaid payments holding the intent;
// its exceptions are swallowed (200).
func (s *StripeRoutes) Webhook(ctx context.Context, identity domain.ResolvedIdentity, request StripeRequest, payload []byte, signature *string) error {
	secret, err := s.WebhookSecret(ctx)
	if err != nil {
		return err
	}
	event, err := stripeapi.ConstructEvent(payload, signature, secret, s.now())
	switch {
	case errors.Is(err, stripeapi.ErrWebhookPayload), errors.Is(err, stripeapi.ErrWebhookSignature):
		return ErrStripeWebhookRejected
	case err != nil:
		return domain.ErrStripeServer
	}
	kind, present := event.Lookup("type")
	if !present {
		return domain.ErrStripeServer
	}
	if kind != "charge.succeeded" {
		// checkout.session.completed, the platform billing events and anything else: 200.
		return nil
	}
	match, ok, err := chargeIntent(event)
	if err != nil || !ok {
		return err
	}
	payments, err := s.store.PaymentsByIntent(ctx, match, stripeQuery(identity, supplierGroup(request.GroupHeader)))
	if err != nil {
		s.log.Error("stripe webhook: charge.succeeded", "error", err)
		return nil
	}
	if len(payments) == 0 {
		return ErrStripeWebhookRejected
	}
	var targets []*domain.StripePayment
	for index := range payments {
		if !payments[index].Paid {
			targets = append(targets, &payments[index])
		}
	}
	err = s.marks(ctx, identity, request, targets, false, nil)
	if errors.Is(err, domain.ErrStripeNotPorted) {
		return err
	}
	if err != nil {
		s.log.Error("stripe webhook: charge.succeeded", "error", err)
	}
	return nil
}

// chargeIntent is event['data']['object']['payment_intent'] as a lookup value;
// ok=false is an exception the view swallows (200).
func chargeIntent(event domain.RequestObject) (domain.StripeIntentMatch, bool, error) {
	data, present := event.Lookup("data")
	object, isObject := data.(domain.RequestObject)
	if !present || !isObject {
		return domain.StripeIntentMatch{}, false, nil
	}
	charge, present := object.Lookup("object")
	fields, isObject := charge.(domain.RequestObject)
	if !present || !isObject {
		return domain.StripeIntentMatch{}, false, nil
	}
	value, present := fields.Lookup("payment_intent")
	if !present {
		return domain.StripeIntentMatch{}, false, nil
	}
	switch v := value.(type) {
	case nil:
		return domain.StripeIntentMatch{Null: true}, true, nil
	case string:
		return domain.StripeIntentMatch{Value: v}, true, nil
	case bool:
		return domain.StripeIntentMatch{Value: map[bool]string{true: "True", false: "False"}[v]}, true, nil
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			return domain.StripeIntentMatch{Value: string(v)}, true, nil
		}
	}
	// CharField.to_python's str() of other values is not ported.
	return domain.StripeIntentMatch{}, false, domain.ErrStripeNotPorted
}

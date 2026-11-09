package service

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/google/uuid"
)

// BakneySSOTransaction is the request transaction (SecureView.dispatch is
// transaction.atomic); see repository.BakneySSOTransaction.
type BakneySSOTransaction interface {
	LockConfiguration(context.Context) (*domain.BakneyConfiguration, error)
	Pairing(context.Context, int64) (*domain.BakneyPairing, error)
	CreatePairing(context.Context, int64, string, string, time.Time) (*domain.BakneyPairing, error)
	SavePairing(context.Context, *domain.BakneyPairing, time.Time) error
	Denomination(context.Context, string) (string, error)
	RevocationPending(context.Context) (bool, error)
	QueueRevocation(context.Context, domain.BakneyRevocation, time.Time) error
	DeleteLogins(context.Context, string) error
	DeleteChallenges(context.Context, string) error
	ChallengeExists(context.Context, string, string, time.Time) (bool, error)
	PutChallenge(context.Context, string, string, time.Time) error
	ConsumeNonce(context.Context, string, time.Time) (bool, error)
	LockedLogin(context.Context, string, string) (*domain.BakneyLogin, error)
	DeleteLoginDigest(context.Context, string) error
	CreateLogin(context.Context, domain.BakneyLogin) error
	SaveLogin(context.Context, domain.BakneyLogin) error
	SetLoginStage(context.Context, string, string) error
	ActiveMember(context.Context, string) (*domain.BakneyMember, error)
	Membership(context.Context, string) (*domain.BakneyMembership, error)
	Linked(context.Context, string, string, string) (bool, error)
	SetLastLogin(context.Context, string, time.Time) error
	Revocations(context.Context) ([]domain.BakneyRevocation, error)
	DeleteRevocation(context.Context, string) error
	DeleteExpired(context.Context, string, time.Time) error
	PairingNeedsSync(context.Context) (bool, error)
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}

type BakneySSOStore interface {
	Begin(context.Context) (BakneySSOTransaction, error)
	BeginJob(context.Context, domain.Job) (BakneySSOTransaction, error)
}

// BakneySSOLogins issue the local session of a ready login (JWTTokenService).
type BakneySSOLogins interface {
	ByID(context.Context, string) (domain.Account, error)
}
type BakneySSOTokenIssuer interface {
	Issue(string, int, string) (jwt.Pair, error)
}
type BakneySSOResponses interface {
	Build(context.Context, domain.Account, jwt.Pair, domain.LoginAuditContext) (*dto.LoginResponse, error)
}
type BakneySSOActors interface {
	Authenticate(context.Context, string) (*domain.Account, error)
}

type BakneySSO struct {
	store     BakneySSOStore
	settings  BakneySSOSettings
	authority *BakneyAuthorityClient
	accounts  BakneySSOLogins
	tokens    BakneySSOTokenIssuer
	responses BakneySSOResponses
	actors    BakneySSOActors
	now       func() time.Time
}

func NewBakneySSO(store BakneySSOStore, settings BakneySSOSettings, authority *BakneyAuthorityClient, accounts BakneySSOLogins,
	tokens BakneySSOTokenIssuer, responses BakneySSOResponses, actors BakneySSOActors, now func() time.Time) *BakneySSO {
	return &BakneySSO{store: store, settings: settings, authority: authority, accounts: accounts, tokens: tokens, responses: responses,
		actors: actors, now: now}
}

// BakneySSOBodyError carries a request.data parse failure (ParseError,
// UnsupportedMediaType): a handled response, so the transaction commits.
type BakneySSOBodyError struct{ Err error }

func (e *BakneySSOBodyError) Error() string { return e.Err.Error() }
func (e *BakneySSOBodyError) Unwrap() error { return e.Err }

// BakneySSOReply is one view response: JSON or a redirect, with cookies.
type BakneySSOReply struct {
	Status   int
	JSON     any
	Location string
	Cookies  []string
}

// BakneyBrowser is what the browser views read from the request.
type BakneyBrowser struct {
	Origin        string // scheme://host as build_absolute_uri('/') without the slash
	Query         [][2]string
	Cookies       map[string]string
	Authorization string
	OriginHeader  string
	OriginPresent bool
	CSRF          string
	Audit         domain.LoginAuditContext
}

func (s *BakneySSO) clock() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// run is transaction.atomic around the view: a handled response (including
// an SSOError or a request.data error) commits, anything else rolls back.
func (s *BakneySSO) run(ctx context.Context, view func(BakneySSOTransaction) (*BakneySSOReply, error)) (*BakneySSOReply, error) {
	tx, err := s.store.Begin(ctx)
	if err != nil {
		return nil, err
	}
	reply, err := view(tx)
	var ssoErr *BakneySSOError
	var bodyErr *BakneySSOBodyError
	if err != nil && !errors.As(err, &ssoErr) && !errors.As(err, &bodyErr) {
		_ = tx.Rollback()
		return nil, err
	}
	if commitErr := tx.Commit(); commitErr != nil {
		return nil, commitErr
	}
	return reply, err
}

// ---------------------------------------------------------------------------
// service.py
// ---------------------------------------------------------------------------

type bakneyState struct {
	tx      BakneySSOTransaction
	config  *domain.BakneyConfiguration
	pairing *domain.BakneyPairing
}

// lockedPairing is service.locked_pairing.
func (s *BakneySSO) lockedPairing(ctx context.Context, tx BakneySSOTransaction) (*bakneyState, error) {
	config, err := tx.LockConfiguration(ctx)
	if err != nil {
		return nil, err
	}
	if config == nil || !config.SelfHosted || config.PrimaryAssociationID == nil {
		return nil, ssoError("configuration_required", 409)
	}
	pairing, err := tx.Pairing(ctx, config.ID)
	if err == nil && pairing == nil {
		pairing, err = tx.CreatePairing(ctx, config.ID, uuid.NewString(), uuid.NewString(), s.clock())
	}
	if err != nil {
		return nil, err
	}
	return &bakneyState{tx: tx, config: config, pairing: pairing}, nil
}

func bakneyText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

type bakneyBindingValues struct{ instanceID, pairingID, associationID, origin, callbackURI string }

// binding is service.binding.
func (st *bakneyState) binding() bakneyBindingValues {
	p := st.pairing
	return bakneyBindingValues{instanceID: p.InstanceID, pairingID: bakneyText(p.RemotePairingID), associationID: bakneyText(p.AssociationID),
		origin: p.Origin, callbackURI: p.Origin + BakneySSOCallback}
}

// checkBinding is service.check_binding.
func (s *BakneySSO) checkBinding(st *bakneyState) error {
	p := st.pairing
	if p.SecretEncrypted == "" {
		return ssoError("revalidation_required", 409)
	}
	authority, err := bakneyAuthority(s.settings)
	if err != nil {
		return err
	}
	if p.Authority != authority {
		return ssoError("revalidation_required", 409)
	}
	ui, err := bakneyUIOrigin(s.settings)
	if err != nil {
		return err
	}
	if p.UIOrigin != ui {
		return ssoError("revalidation_required", 409)
	}
	origin, err := bakneyOrigin(s.settings)
	if err != nil {
		return err
	}
	if p.Origin != origin || bakneyText(p.AssociationID) != bakneyText(st.config.PrimaryAssociationID) {
		return ssoError("revalidation_required", 409)
	}
	return nil
}

// snapshot is service.snapshot.
func (s *BakneySSO) snapshot(ctx context.Context, st *bakneyState) (map[string]any, error) {
	p := st.pairing
	valid := true
	if err := s.checkBinding(st); err != nil {
		var ssoErr *BakneySSOError
		if !errors.As(err, &ssoErr) {
			return nil, err
		}
		valid = false
	}
	state := p.State
	if p.SecretEncrypted != "" && !valid {
		state = "revalidation_required"
	}
	var association any
	if p.AssociationName != "" {
		association = map[string]any{"id": bakneyText(p.AssociationID), "name": p.AssociationName}
	}
	pending, err := st.tx.RevocationPending(ctx)
	if err != nil {
		return nil, err
	}
	var checked any
	if p.CheckedAt != nil {
		checked = drfDateTime(*p.CheckedAt)
	}
	return map[string]any{"protocol": BakneySSOVersion, "instance_id": p.InstanceID, "pairing_id": p.PairingID, "origin": p.Origin,
		"state": state, "association": association, "forwarding_enabled": valid && p.State == "paired" && p.ForwardingEnabled,
		"checked_at": checked, "notification_pending": pending}, nil
}

// drfDateTime is DRF's JSONEncoder for an aware UTC datetime.
func drfDateTime(value time.Time) string {
	value = value.UTC()
	text := value.Format("2006-01-02T15:04:05")
	if micro := value.Nanosecond() / 1000; micro != 0 {
		text += "." + leftPadMicro(micro)
	}
	return text + "Z"
}

func leftPadMicro(micro int) string {
	digits := []byte("000000")
	for i := 5; i >= 0; i-- {
		digits[i] = byte('0' + micro%10)
		micro /= 10
	}
	return string(digits)
}

// queueRevocation is service.queue_revocation.
func (s *BakneySSO) queueRevocation(ctx context.Context, st *bakneyState) error {
	p := st.pairing
	if p.SecretEncrypted != "" && p.RemotePairingID != nil {
		if err := st.tx.QueueRevocation(ctx, domain.BakneyRevocation{PairingID: p.PairingID, RemotePairingID: p.RemotePairingID,
			Authority: p.Authority, SecretEncrypted: p.SecretEncrypted}, s.clock()); err != nil {
			return err
		}
	}
	if err := st.tx.DeleteLogins(ctx, p.PairingID); err != nil {
		return err
	}
	return st.tx.DeleteChallenges(ctx, p.PairingID)
}

// rotate is service.rotate; it returns the new plain secret.
func (s *BakneySSO) rotate(ctx context.Context, st *bakneyState) (string, error) {
	target, err := bakneyOrigin(s.settings)
	if err != nil {
		return "", err
	}
	issuer, err := bakneyAuthority(s.settings)
	if err != nil {
		return "", err
	}
	ui, err := bakneyUIOrigin(s.settings)
	if err != nil {
		return "", err
	}
	if err := s.queueRevocation(ctx, st); err != nil {
		return "", err
	}
	secret, err := tokenURLSafe()
	if err != nil {
		return "", err
	}
	encrypted, err := bakneyEncrypt(secret, s.settings, s.clock())
	if err != nil {
		return "", err
	}
	p := st.pairing
	p.PairingID = uuid.NewString()
	p.SecretEncrypted = encrypted
	p.Authority, p.Origin, p.UIOrigin = issuer, target, ui
	p.RemotePairingID, p.RemoteGeneration = nil, nil
	p.AssociationID = st.config.PrimaryAssociationID
	p.AssociationName = ""
	p.State, p.ForwardingEnabled = "generated", false
	p.CheckedAt = nil
	return secret, st.tx.SavePairing(ctx, p, s.clock())
}

// disconnect is service.disconnect.
func (s *BakneySSO) disconnect(ctx context.Context, st *bakneyState) error {
	if err := s.queueRevocation(ctx, st); err != nil {
		return err
	}
	p := st.pairing
	p.SecretEncrypted = ""
	p.State, p.ForwardingEnabled = "disconnected", false
	return st.tx.SavePairing(ctx, p, s.clock())
}

// upstream is protocol.upstream for a pairing (status/token actions).
func (s *BakneySSO) upstream(ctx context.Context, p *domain.BakneyPairing, action string, payload [][2]string) (domain.RequestObject, error) {
	return s.upstreamTo(ctx, domain.BakneyRevocation{RemotePairingID: p.RemotePairingID, Authority: p.Authority,
		SecretEncrypted: p.SecretEncrypted}, action, payload, false)
}

// upstreamTo is protocol.upstream; revocation=True with the disconnect action
// skips the configured-authority check (a queued revocation keeps the
// authority it was paired with).
func (s *BakneySSO) upstreamTo(ctx context.Context, p domain.BakneyRevocation, action string, payload [][2]string, revocation bool) (domain.RequestObject, error) {
	if !(revocation && action == "disconnect") {
		authority, err := bakneyAuthority(s.settings)
		if err != nil {
			return nil, err
		}
		if p.Authority != authority {
			return nil, ssoError("configuration_required", 409)
		}
	}
	parts, err := pythonURLSplit(p.Authority)
	if err != nil {
		return nil, err
	}
	base, err := normalizeBakneyOrigin(parts.scheme+"://"+parts.netloc, s.settings)
	if err != nil {
		return nil, err
	}
	if base+parts.path != p.Authority {
		return nil, ssoError("configuration_required", 409)
	}
	if p.RemotePairingID == nil {
		return nil, ssoError("invalid_pairing", 409)
	}
	secret, err := bakneyDecrypt(p.SecretEncrypted, s.settings)
	if err != nil {
		return nil, err
	}
	keys, values := []string{}, map[string]string{}
	for _, item := range payload {
		keys, values[item[0]] = append(keys, item[0]), item[1]
	}
	keys, values["pairing_id"], values["secret"] = append(keys, "pairing_id", "secret"), *p.RemotePairingID, secret
	endpoint := "status"
	switch action {
	case "status", "disconnect":
		keys, values["action"] = append(keys, "action"), action
	default:
		endpoint = action
	}
	return s.authority.post(ctx, p.Authority+"/pairing/v1/"+endpoint, pythonJSONStrings(keys, values))
}

func lookupText(object domain.RequestObject, key string) (any, bool) {
	value, present := object.Lookup(key)
	return value, present
}

// differs is `data.get(key) != value` for a str (or None) expected value.
func differs(object domain.RequestObject, key string, expected *string) bool {
	value, present := lookupText(object, key)
	if expected == nil {
		return present && value != nil
	}
	text, ok := value.(string)
	return !present || !ok || text != *expected
}

func textPointer(value string) *string { return &value }

// synchronize is service.synchronize.
func (s *BakneySSO) synchronize(ctx context.Context, st *bakneyState) error {
	if err := s.checkBinding(st); err != nil {
		return err
	}
	p := st.pairing
	if (p.State != "pending" && p.State != "paired") || p.RemotePairingID == nil {
		return ssoError("invalid_pairing", 409)
	}
	data, err := s.upstream(ctx, p, "status", nil)
	if err != nil {
		var ssoErr *BakneySSOError
		if errors.As(err, &ssoErr) && ssoErr.Code == "pairing_rejected" {
			if err := s.disconnect(ctx, st); err != nil {
				return err
			}
		}
		return err
	}
	expected := st.binding()
	status, _ := data.Lookup("status")
	instance := textPointer(expected.instanceID)
	if status == "pending" {
		instance = nil
	}
	for _, item := range []struct {
		key   string
		value *string
	}{{"instance_id", instance}, {"pairing_id", textPointer(expected.pairingID)}, {"association_id", textPointer(expected.associationID)},
		{"origin", textPointer(expected.origin)}, {"callback_uri", textPointer(expected.callbackURI)}} {
		if differs(data, item.key, item.value) {
			return ssoError("invalid_response", 502)
		}
	}
	generation, _ := data.Lookup("generation")
	forwarding, _ := data.Lookup("forwarding")
	_, isBool := forwarding.(bool)
	if !canonicalUUID(generation) || !isBool || (status != "pending" && status != "verified" && status != "revoked") {
		return ssoError("invalid_response", 502)
	}
	switch status {
	case "revoked":
		return s.disconnect(ctx, st)
	case "pending":
		if bakneyText(p.RemoteGeneration) != generation.(string) {
			return ssoError("revalidation_required", 409)
		}
		p.State, p.ForwardingEnabled = "pending", false
		return st.tx.SavePairing(ctx, p, s.clock())
	}
	name, err := st.tx.Denomination(ctx, *st.config.PrimaryAssociationID)
	if err != nil {
		return err
	}
	now := s.clock()
	text := generation.(string)
	p.State, p.ForwardingEnabled, p.RemoteGeneration = "paired", forwarding.(bool), &text
	p.AssociationName, p.CheckedAt = name, &now
	return st.tx.SavePairing(ctx, p, now)
}

// requireForwarding is service.require_forwarding.
func (s *BakneySSO) requireForwarding(ctx context.Context, st *bakneyState) error {
	if err := s.synchronize(ctx, st); err != nil {
		return err
	}
	if st.pairing.State != "paired" || !st.pairing.ForwardingEnabled {
		return ssoError("forwarding_disabled", 403)
	}
	return nil
}

// pythonUUIDText is uuid.UUID(str(value)) for a parsed JSON value; ok is
// false when Python raises ValueError/TypeError.
func pythonUUIDText(value any) (string, bool) {
	var text string
	switch v := value.(type) {
	case string:
		text = v
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "", false
		}
		number, ok := new(big.Int).SetString(string(v), 10)
		if !ok {
			return "", false
		}
		text = number.String()
	default:
		return "", false
	}
	text = strings.ReplaceAll(strings.ReplaceAll(text, "urn:", ""), "uuid:", "")
	text = strings.ReplaceAll(strings.Trim(text, "{}"), "-", "")
	if len(text) != 32 {
		return "", false
	}
	parsed, err := uuid.Parse(text)
	if err != nil {
		return "", false
	}
	return parsed.String(), true
}

// localUser is service.local_user.
func (s *BakneySSO) localUser(ctx context.Context, st *bakneyState, id string, valid bool) (*domain.BakneyMember, error) {
	if !valid {
		return nil, ssoError("account_unavailable", 403)
	}
	user, err := st.tx.ActiveMember(ctx, id)
	if err != nil {
		return nil, err
	}
	if user == nil {
		return nil, ssoError("account_unavailable", 403)
	}
	if (user.Role != 2 && user.Role != 3) || user.Superuser || user.Staff {
		return nil, ssoError("account_not_eligible", 403)
	}
	var membership *domain.BakneyMembership
	if st.pairing.AssociationID != nil {
		if membership, err = st.tx.Membership(ctx, *st.pairing.AssociationID); err != nil {
			return nil, err
		}
	}
	if membership == nil || membership.Deleted || !membership.OwnerActive || membership.OwnerDeleted || membership.OwnerRole != 1 {
		return nil, ssoError("membership_required", 403)
	}
	if user.ConnectedUserID == nil || *user.ConnectedUserID != membership.OwnerID {
		linked, err := st.tx.Linked(ctx, user.ID, membership.ID, membership.OwnerID)
		if err != nil {
			return nil, err
		}
		if !linked {
			return nil, ssoError("membership_required", 403)
		}
	}
	return user, nil
}

// redeemedUser is service.redeemed_user.
func (s *BakneySSO) redeemedUser(ctx context.Context, st *bakneyState, data domain.RequestObject) (*domain.BakneyMember, error) {
	expected := st.binding()
	for _, item := range [][2]string{{"pairing_id", expected.pairingID}, {"instance_id", expected.instanceID},
		{"association_id", expected.associationID}} {
		if differs(data, item[0], textPointer(item[1])) {
			return nil, ssoError("invalid_response", 502)
		}
	}
	amr, _ := data.Lookup("amr")
	list, ok := amr.([]any)
	if !ok || len(list) != 1 || (list[0] != "mfa" && list[0] != "authenticated") {
		return nil, ssoError("invalid_response", 502)
	}
	authTime, _ := data.Lookup("auth_time")
	value, isInt := bakneyPythonInt(authTime)
	if !isInt {
		return nil, ssoError("invalid_response", 502)
	}
	limit := new(big.Float).SetFloat64(float64(s.now().UnixNano())/1e9 + 60)
	if value.Sign() <= 0 || new(big.Float).SetInt(value).Cmp(limit) > 0 {
		return nil, ssoError("invalid_response", 502)
	}
	userID, _ := data.Lookup("user_id")
	id, valid := pythonUUIDText(userID)
	return s.localUser(ctx, st, id, valid)
}

// consumeNonce is service.consume_nonce.
func consumeNonce(ctx context.Context, tx BakneySSOTransaction, digest string, expires time.Time) error {
	fresh, err := tx.ConsumeNonce(ctx, digest, expires)
	if err != nil {
		return err
	}
	if !fresh {
		return ssoError("replayed_request", 409)
	}
	return nil
}

// ---------------------------------------------------------------------------
// PairingAdminView
// ---------------------------------------------------------------------------

// AdminRead is PairingAdminView.get.
func (s *BakneySSO) AdminRead(ctx context.Context) (*BakneySSOReply, error) {
	return s.run(ctx, func(tx BakneySSOTransaction) (*BakneySSOReply, error) {
		st, err := s.lockedPairing(ctx, tx)
		if err != nil {
			return nil, err
		}
		var syncError any
		if st.pairing.RemotePairingID != nil && (st.pairing.State == "pending" || st.pairing.State == "paired") {
			if err := s.synchronize(ctx, st); err != nil {
				var ssoErr *BakneySSOError
				if !errors.As(err, &ssoErr) {
					return nil, err
				}
				syncError = ssoErr.Code
			}
		}
		body, err := s.snapshot(ctx, st)
		if err != nil {
			return nil, err
		}
		body["sync_error"] = syncError
		return &BakneySSOReply{Status: 200, JSON: body}, nil
	})
}

// AdminWrite is PairingAdminView.post; data reads request.data.
func (s *BakneySSO) AdminWrite(ctx context.Context, data func() (any, error)) (*BakneySSOReply, error) {
	return s.run(ctx, func(tx BakneySSOTransaction) (*BakneySSOReply, error) {
		st, err := s.lockedPairing(ctx, tx)
		if err != nil {
			return nil, err
		}
		body, err := data()
		if err != nil {
			return nil, &BakneySSOBodyError{Err: err}
		}
		object, ok := body.(domain.RequestObject)
		if !ok || !sameKeys(object, "action", "pairing_id") {
			return nil, ssoError("stale_pairing", 409)
		}
		if value, _ := object.Lookup("pairing_id"); value != st.pairing.PairingID {
			return nil, ssoError("stale_pairing", 409)
		}
		action, _ := object.Lookup("action")
		switch action {
		case "generate":
			secret, err := s.rotate(ctx, st)
			if err != nil {
				return nil, err
			}
			snapshot, err := s.snapshot(ctx, st)
			if err != nil {
				return nil, err
			}
			snapshot["secret"] = secret
			return &BakneySSOReply{Status: 200, JSON: snapshot}, nil
		case "disconnect":
			err = s.disconnect(ctx, st)
		case "sync":
			err = s.synchronize(ctx, st)
		default:
			err = ssoError("invalid_request", 400)
		}
		if err != nil {
			return nil, err
		}
		snapshot, err := s.snapshot(ctx, st)
		if err != nil {
			return nil, err
		}
		return &BakneySSOReply{Status: 200, JSON: snapshot}, nil
	})
}

// sameKeys is `set(data) == {...}`.
func sameKeys(object domain.RequestObject, keys ...string) bool {
	seen := map[string]bool{}
	for _, field := range object {
		seen[field.Name] = true
	}
	if len(seen) != len(keys) {
		return false
	}
	for _, key := range keys {
		if !seen[key] {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------------------
// PairingChallengeView / PairingCommitView
// ---------------------------------------------------------------------------

var bakneyNonce = regexp.MustCompile(`^[A-Za-z0-9_-]{43}$`)
var bakneyProof = regexp.MustCompile(`^[0-9a-f]{64}$`)

// Handshake is PairingChallengeView.post (commit=false) and PairingCommitView.post.
func (s *BakneySSO) Handshake(ctx context.Context, commit bool, data func() (any, error)) (*BakneySSOReply, error) {
	return s.run(ctx, func(tx BakneySSOTransaction) (*BakneySSOReply, error) {
		st, err := s.lockedPairing(ctx, tx)
		if err != nil {
			return nil, err
		}
		if err := s.checkBinding(st); err != nil {
			return nil, err
		}
		p := st.pairing
		if (p.State != "generated" && p.State != "pending") || p.RemotePairingID != nil {
			return nil, ssoError("invalid_pairing", 409)
		}
		body, err := data()
		if err != nil {
			return nil, &BakneySSOBodyError{Err: err}
		}
		keys := []string{"protocol", "pairing_id", "association_id", "instance_id", "origin", "callback_uri", "generation", "nonce", "expires_at"}
		if commit {
			keys = append(keys, "proof")
		}
		object, ok := body.(domain.RequestObject)
		if !ok || !sameKeys(object, keys...) {
			return nil, ssoError("invalid_request", 400)
		}
		field := func(key string) any { value, _ := object.Lookup(key); return value }
		instance := ""
		if commit {
			instance = p.InstanceID
		}
		invalid := ssoError("invalid_request", 400)
		if field("protocol") != BakneySSOVersion || !canonicalUUID(field("pairing_id")) || !canonicalUUID(field("generation")) ||
			field("association_id") != bakneyText(p.AssociationID) || field("instance_id") != instance || field("origin") != p.Origin ||
			field("callback_uri") != p.Origin+BakneySSOCallback {
			return nil, invalid
		}
		nonce, ok := field("nonce").(string)
		if !ok || !bakneyNonce.MatchString(nonce) {
			return nil, invalid
		}
		expiresAt, ok := bakneyPythonInt(field("expires_at"))
		if !ok {
			return nil, invalid
		}
		// int - float: the int converts to float first (OverflowError when too large).
		asFloat, _ := new(big.Float).SetInt(expiresAt).Float64()
		if math.IsInf(asFloat, 0) {
			return nil, errors.New("int too large to convert to float")
		}
		difference := asFloat - float64(s.now().UnixNano())/1e9
		if !(difference > 0 && difference <= 120) {
			return nil, invalid
		}
		binding := bakneyBinding{PairingID: field("pairing_id").(string), AssociationID: field("association_id").(string),
			InstanceID: p.InstanceID, Origin: p.Origin, CallbackURI: p.Origin + BakneySSOCallback, Generation: field("generation").(string),
			Nonce: nonce, ExpiresAt: expiresAt.String()}
		context_, err := bakneyDigest(strings.Join([]string{p.PairingID, binding.PairingID, binding.AssociationID, binding.InstanceID,
			binding.Origin, binding.CallbackURI, binding.Generation}, "\n"))
		if err != nil {
			return nil, err
		}
		expiry := time.Unix(expiresAt.Int64(), 0).UTC()
		secret, err := bakneyDecrypt(p.SecretEncrypted, s.settings)
		if err != nil {
			return nil, err
		}
		phase := "challenge"
		if commit {
			phase = "ack"
			proof, ok := field("proof").(string)
			expected, err := bakneySignature(secret, "commit", binding)
			if err != nil {
				return nil, err
			}
			if !ok || !bakneyProof.MatchString(proof) || subtle.ConstantTimeCompare([]byte(proof), []byte(expected)) != 1 {
				return nil, ssoError("invalid_proof", 403)
			}
			found, err := tx.ChallengeExists(ctx, context_, p.PairingID, s.clock())
			if err != nil {
				return nil, err
			}
			if !found {
				return nil, ssoError("invalid_pairing", 409)
			}
			nonceDigest, err := bakneyDigest(p.PairingID + ":commit:" + nonce)
			if err != nil {
				return nil, err
			}
			if err := consumeNonce(ctx, tx, nonceDigest, expiry); err != nil {
				return nil, err
			}
			remote, generation := binding.PairingID, binding.Generation
			p.RemotePairingID, p.RemoteGeneration, p.State = &remote, &generation, "pending"
			if err := tx.SavePairing(ctx, p, s.clock()); err != nil {
				return nil, err
			}
			if err := tx.DeleteChallenges(ctx, p.PairingID); err != nil {
				return nil, err
			}
		} else {
			nonceDigest, err := bakneyDigest(p.PairingID + ":challenge:" + nonce)
			if err != nil {
				return nil, err
			}
			if err := consumeNonce(ctx, tx, nonceDigest, expiry); err != nil {
				return nil, err
			}
			if err := tx.PutChallenge(ctx, context_, p.PairingID, expiry); err != nil {
				return nil, err
			}
			p.State = "pending"
			if err := tx.SavePairing(ctx, p, s.clock()); err != nil {
				return nil, err
			}
		}
		proof, err := bakneySignature(secret, phase, binding)
		if err != nil {
			return nil, err
		}
		return &BakneySSOReply{Status: 200, JSON: map[string]any{"protocol": BakneySSOVersion, "instance_id": p.InstanceID, "proof": proof}}, nil
	})
}

// ---------------------------------------------------------------------------
// Browser handoff
// ---------------------------------------------------------------------------

// browserCookie is protocol.browser_cookie: the cookie name and Secure flag.
func (s *BakneySSO) browserCookie(p *domain.BakneyPairing) (string, bool) {
	parts, _ := pythonURLSplit(p.Origin)
	secure := !(s.settings.developmentHTTP() && parts.scheme == "http")
	if secure {
		return bakneyCookie, true
	}
	return bakneyDevCookie, false
}

// djangoCookie is HttpResponse.set_cookie's Set-Cookie value (max_age set).
func djangoCookie(name, value string, maxAge int, secure bool, sameSite string, now time.Time) string {
	expires := time.Unix(now.Unix()+int64(maxAge), 0).UTC().Format("Mon, 02 Jan 2006 15:04:05 GMT")
	cookie := name + "=" + value + "; expires=" + expires + "; HttpOnly; Max-Age=" + itoa(maxAge) + "; Path=/; SameSite=" + sameSite
	if secure {
		cookie += "; Secure"
	}
	return cookie
}

func itoa(value int) string { return big.NewInt(int64(value)).String() }

// browserOrigin is views.browser_origin.
func (s *BakneySSO) browserOrigin(st *bakneyState, browser BakneyBrowser) error {
	origin, err := normalizeBakneyOrigin(browser.Origin+"/", s.settings)
	if err != nil {
		return err
	}
	if origin != st.pairing.Origin {
		return ssoError("revalidation_required", 409)
	}
	return nil
}

// browserLogin is views.browser_login.
func (s *BakneySSO) browserLogin(ctx context.Context, st *bakneyState, browser BakneyBrowser) (*domain.BakneyLogin, string, error) {
	name, _ := s.browserCookie(st.pairing)
	cookie := browser.Cookies[name]
	if !bakneyOpaque.MatchString(cookie) {
		return nil, "", ssoError("handoff_expired", 400)
	}
	digest, err := bakneyDigest(cookie)
	if err != nil {
		return nil, "", err
	}
	login, err := st.tx.LockedLogin(ctx, digest, st.pairing.PairingID)
	if err != nil {
		return nil, "", err
	}
	if login == nil || !login.ExpiresAt.After(s.now()) {
		return nil, "", ssoError("handoff_expired", 400)
	}
	return login, cookie, nil
}

func queryCounts(query [][2]string) (map[string][]string, []string) {
	values, order := map[string][]string{}, []string{}
	for _, pair := range query {
		if _, seen := values[pair[0]]; !seen {
			order = append(order, pair[0])
		}
		values[pair[0]] = append(values[pair[0]], pair[1])
	}
	return values, order
}

// redirectError is the handoff views' `except SSOError` branch.
func redirectError(err error) (*BakneySSOReply, error) {
	var ssoErr *BakneySSOError
	if errors.As(err, &ssoErr) {
		return &BakneySSOReply{Status: 302, Location: "/#/bakney-login?error=" + ssoErr.Code}, nil
	}
	return nil, err
}

// LoginStart is LoginStartView.get.
func (s *BakneySSO) LoginStart(ctx context.Context, browser BakneyBrowser) (*BakneySSOReply, error) {
	return s.run(ctx, func(tx BakneySSOTransaction) (*BakneySSOReply, error) {
		reply, err := s.loginStart(ctx, tx, browser)
		if err != nil {
			return redirectError(err)
		}
		return reply, nil
	})
}

func (s *BakneySSO) loginStart(ctx context.Context, tx BakneySSOTransaction, browser BakneyBrowser) (*BakneySSOReply, error) {
	st, err := s.lockedPairing(ctx, tx)
	if err != nil {
		return nil, err
	}
	if err := s.browserOrigin(st, browser); err != nil {
		return nil, err
	}
	if err := s.requireForwarding(ctx, st); err != nil {
		return nil, err
	}
	p := st.pairing
	values, order := queryCounts(browser.Query)
	attempt := ""
	valid := len(order) == 3 && values["protocol"] != nil && values["pairing_id"] != nil && values["attempt_id"] != nil
	if valid {
		for _, key := range order {
			valid = valid && len(values[key]) == 1
		}
	}
	if valid {
		attempt = values["attempt_id"][0]
		valid = values["protocol"][0] == BakneySSOVersion && values["pairing_id"][0] == bakneyText(p.RemotePairingID) && canonicalUUID(attempt)
	}
	if !valid {
		return nil, ssoError("invalid_handoff", 400)
	}
	secrets := make([]string, 3)
	for index := range secrets {
		if secrets[index], err = tokenURLSafe(); err != nil {
			return nil, err
		}
	}
	browserSecret, state, verifier := secrets[0], secrets[1], secrets[2]
	name, secure := s.browserCookie(p)
	if previous := browser.Cookies[name]; bakneyOpaque.MatchString(previous) {
		digest, err := bakneyDigest(previous)
		if err != nil {
			return nil, err
		}
		if err := tx.DeleteLoginDigest(ctx, digest); err != nil {
			return nil, err
		}
	}
	digest, _ := bakneyDigest(browserSecret)
	now := s.clock()
	encrypted, err := bakneyEncrypt(verifier, s.settings, now)
	if err != nil {
		return nil, err
	}
	if err := tx.CreateLogin(ctx, domain.BakneyLogin{Digest: digest, PairingID: p.PairingID, State: state, RemoteGeneration: p.RemoteGeneration,
		AttemptID: &attempt, VerifierEncrypted: encrypted, Stage: "started", ExpiresAt: now.Add(5 * time.Minute)}); err != nil {
		return nil, err
	}
	challenge := sha256.Sum256([]byte(verifier))
	query := "attempt_id=" + url.QueryEscape(attempt) + "&callback_uri=" + url.QueryEscape(p.Origin+BakneySSOCallback) +
		"&state=" + url.QueryEscape(state) + "&code_challenge_method=S256&code_challenge=" +
		url.QueryEscape(base64.RawURLEncoding.EncodeToString(challenge[:]))
	return &BakneySSOReply{Status: 302, Location: p.UIOrigin + "/handoff.html#" + query,
		Cookies: []string{djangoCookie(name, browserSecret, 300, secure, "Lax", time.Now())}}, nil
}

// Callback is LoginCallbackView.get.
func (s *BakneySSO) Callback(ctx context.Context, browser BakneyBrowser) (*BakneySSOReply, error) {
	return s.run(ctx, func(tx BakneySSOTransaction) (*BakneySSOReply, error) {
		reply, err := s.callback(ctx, tx, browser)
		if err != nil {
			return redirectError(err)
		}
		return reply, nil
	})
}

func (s *BakneySSO) callback(ctx context.Context, tx BakneySSOTransaction, browser BakneyBrowser) (*BakneySSOReply, error) {
	st, err := s.lockedPairing(ctx, tx)
	if err != nil {
		return nil, err
	}
	if err := s.browserOrigin(st, browser); err != nil {
		return nil, err
	}
	if err := s.checkBinding(st); err != nil {
		return nil, err
	}
	login, cookie, err := s.browserLogin(ctx, st, browser)
	if err != nil {
		return nil, err
	}
	values, order := queryCounts(browser.Query)
	last := func(key string) string {
		if items := values[key]; len(items) > 0 {
			return items[len(items)-1]
		}
		return ""
	}
	state, code := last("state"), last("code")
	if len(order) != 2 || values["code"] == nil || values["state"] == nil || len(values["code"]) != 1 || len(values["state"]) != 1 ||
		!bakneyOpaque.MatchString(code) || !bakneyOpaque.MatchString(state) ||
		subtle.ConstantTimeCompare([]byte(login.State), []byte(state)) != 1 || login.Stage != "started" {
		return nil, ssoError("invalid_handoff", 400)
	}
	login.Stage = "failed" // Consumed before redemption.
	verifier, err := bakneyDecrypt(login.VerifierEncrypted, s.settings)
	if err != nil {
		return nil, err
	}
	login.VerifierEncrypted = ""
	if err := tx.SaveLogin(ctx, *login); err != nil {
		return nil, err
	}
	if err := s.requireForwarding(ctx, st); err != nil {
		return nil, err
	}
	if bakneyText(login.RemoteGeneration) != bakneyText(st.pairing.RemoteGeneration) {
		return nil, ssoError("invalid_handoff", 400)
	}
	data, err := s.upstream(ctx, st.pairing, "token", [][2]string{{"callback_uri", st.pairing.Origin + BakneySSOCallback}, {"code", code},
		{"code_verifier", verifier}})
	if err != nil {
		return nil, err
	}
	user, err := s.redeemedUser(ctx, st, data)
	if err != nil {
		return nil, err
	}
	login.UserID, login.Stage = &user.ID, "ready"
	login.ExpiresAt = s.clock().Add(2 * time.Minute)
	if err := tx.SaveLogin(ctx, *login); err != nil {
		return nil, err
	}
	name, secure := s.browserCookie(st.pairing)
	return &BakneySSOReply{Status: 302, Location: "/#/bakney-login",
		Cookies: []string{djangoCookie(name, cookie, 120, secure, "Lax", time.Now())}}, nil
}

// currentUsers is views.current_users: the users of the BKN_AUTH cookie and
// the Bearer header, sorted; invalid credentials are ignored.
func (s *BakneySSO) currentUsers(ctx context.Context, browser BakneyBrowser) ([]string, error) {
	candidates := []string{browser.Cookies["BKN_AUTH"]}
	if strings.HasPrefix(browser.Authorization, "Bearer ") {
		candidates = append(candidates, browser.Authorization[7:])
	}
	users := map[string]bool{}
	for _, raw := range candidates {
		if raw == "" || strings.IndexFunc(raw, pythonIsSpace) >= 0 {
			continue
		}
		actor, err := s.actors.Authenticate(ctx, "Bearer "+raw)
		var failure *domain.AuthenticationFailure
		if errors.As(err, &failure) || err == nil && actor == nil {
			continue
		}
		if err != nil {
			return nil, err
		}
		users[actor.ID] = true
	}
	result := make([]string, 0, len(users))
	for id := range users {
		result = append(result, id)
	}
	sort.Strings(result)
	return result, nil
}

func without(values []string, excluded string) []string {
	result := []string{}
	for _, value := range values {
		if value != excluded {
			result = append(result, value)
		}
	}
	return result
}

// SessionRead is LoginSessionView.get.
func (s *BakneySSO) SessionRead(ctx context.Context, browser BakneyBrowser) (*BakneySSOReply, error) {
	return s.run(ctx, func(tx BakneySSOTransaction) (*BakneySSOReply, error) {
		st, login, _, err := s.sessionLogin(ctx, tx, browser)
		if err != nil {
			return nil, err
		}
		if login.Stage != "ready" {
			return nil, ssoError("handoff_expired", 400)
		}
		user, err := s.localUser(ctx, st, bakneyText(login.UserID), login.UserID != nil)
		if err != nil {
			return nil, err
		}
		users, err := s.currentUsers(ctx, browser)
		if err != nil {
			return nil, err
		}
		name := user.FirstName
		if name == "" {
			name = user.Username
		}
		return &BakneySSOReply{Status: 200, JSON: map[string]any{"user": map[string]any{"id": user.ID, "name": name}, "csrf_token": login.State,
			"confirm_for": without(users, user.ID)}}, nil
	})
}

func (s *BakneySSO) sessionLogin(ctx context.Context, tx BakneySSOTransaction, browser BakneyBrowser) (*bakneyState, *domain.BakneyLogin, string, error) {
	st, err := s.lockedPairing(ctx, tx)
	if err != nil {
		return nil, nil, "", err
	}
	if err := s.browserOrigin(st, browser); err != nil {
		return nil, nil, "", err
	}
	if err := s.checkBinding(st); err != nil {
		return nil, nil, "", err
	}
	login, cookie, err := s.browserLogin(ctx, st, browser)
	return st, login, cookie, err
}

// SessionLogin is LoginSessionView.post.
func (s *BakneySSO) SessionLogin(ctx context.Context, browser BakneyBrowser, data func() (any, error)) (*BakneySSOReply, error) {
	return s.run(ctx, func(tx BakneySSOTransaction) (*BakneySSOReply, error) {
		st, login, _, err := s.sessionLogin(ctx, tx, browser)
		if err != nil {
			return nil, err
		}
		if !browser.OriginPresent || browser.OriginHeader != st.pairing.Origin ||
			subtle.ConstantTimeCompare([]byte(browser.CSRF), []byte(login.State)) != 1 {
			return nil, ssoError("invalid_handoff", 403)
		}
		if login.Stage != "ready" {
			return nil, ssoError("handoff_expired", 400)
		}
		body, err := data()
		if err != nil {
			return nil, &BakneySSOBodyError{Err: err}
		}
		object, ok := body.(domain.RequestObject)
		if !ok || !sameKeys(object, "confirm_for") {
			return nil, ssoError("invalid_request", 400)
		}
		users, err := s.currentUsers(ctx, browser)
		if err != nil {
			return nil, err
		}
		others := without(users, bakneyText(login.UserID))
		confirm, _ := object.Lookup("confirm_for")
		if !sameStrings(confirm, others) {
			return nil, ssoError("account_switch_required", 409)
		}
		if err := s.requireForwarding(ctx, st); err != nil {
			return nil, err
		}
		if bakneyText(login.RemoteGeneration) != bakneyText(st.pairing.RemoteGeneration) {
			return nil, ssoError("invalid_handoff", 400)
		}
		user, err := s.localUser(ctx, st, bakneyText(login.UserID), login.UserID != nil)
		if err != nil {
			return nil, err
		}
		account, err := s.accounts.ByID(ctx, user.ID)
		if err != nil {
			return nil, err
		}
		pair, err := s.tokens.Issue(account.ID, account.Role, account.Email)
		if err != nil {
			return nil, err
		}
		content, err := s.responses.Build(ctx, account, pair, browser.Audit)
		if err != nil {
			return nil, err
		}
		if err := tx.SetLastLogin(ctx, user.ID, s.clock()); err != nil {
			return nil, err
		}
		if err := tx.SetLoginStage(ctx, login.Digest, "consumed"); err != nil {
			return nil, err
		}
		name, secure := s.browserCookie(st.pairing)
		now := time.Now()
		return &BakneySSOReply{Status: 200, JSON: content, Cookies: []string{
			djangoCookie("BKN_AUTH", pair.AccessToken, int(pair.ExpiresIn), secure, "Strict", now),
			name + `=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/; SameSite=Lax` + deletedCookieSecure(name)}}, nil
	})
}

// deletedCookieSecure is delete_cookie's Secure flag for prefixed names.
func deletedCookieSecure(name string) string {
	if strings.HasPrefix(name, "__Secure-") || strings.HasPrefix(name, "__Host-") {
		return "; Secure"
	}
	return ""
}

// sameStrings is `request.data['confirm_for'] != others` negated.
func sameStrings(value any, expected []string) bool {
	list, ok := value.([]any)
	if !ok || len(list) != len(expected) {
		return false
	}
	for index, item := range list {
		if item != expected[index] {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------------------
// tasks.maintain_bakney_pairings
// ---------------------------------------------------------------------------

// Maintain is the maintain_bakney_pairings task: deliver_revocations, then
// one synchronization of a pending or paired pairing. Django runs the
// revocation deletes and cleanups in autocommit (each its own fenced
// transaction here) and the synchronization in one atomic block whose
// SSOError is swallowed (its effects, such as a disconnect, are kept).
func (s *BakneySSO) Maintain(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" ||
		job.Kind != domain.JobMaintainBakneyPairings || job.Version != 1 || len(job.Payload) > 65536 ||
		json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	effect := func(apply func(BakneySSOTransaction) error) error {
		tx, err := s.store.BeginJob(ctx, job)
		if err != nil {
			return err
		}
		if err := apply(tx); err != nil {
			_ = tx.Rollback()
			return err
		}
		return tx.Commit()
	}
	var revocations []domain.BakneyRevocation
	if err := effect(func(tx BakneySSOTransaction) (err error) { revocations, err = tx.Revocations(ctx); return err }); err != nil {
		return err
	}
	for _, notification := range revocations {
		result, err := s.upstreamTo(ctx, notification, "disconnect", nil, true)
		var ssoErr *BakneySSOError
		switch {
		case errors.As(err, &ssoErr):
			if ssoErr.Code != "pairing_rejected" {
				continue
			}
		case err != nil:
			return err
		default:
			status, _ := result.Lookup("status")
			remote, _ := result.Lookup("pairing_id")
			if status != "revoked" || remote != bakneyText(notification.RemotePairingID) {
				continue
			}
		}
		if err := effect(func(tx BakneySSOTransaction) error { return tx.DeleteRevocation(ctx, notification.PairingID) }); err != nil {
			return err
		}
	}
	for _, cleanup := range []struct {
		table string
		grace time.Duration
	}{{"challenges", 0}, {"nonces", 5 * time.Minute}, {"logins", 0}} {
		cutoff := s.clock().Add(-cleanup.grace)
		if err := effect(func(tx BakneySSOTransaction) error { return tx.DeleteExpired(ctx, cleanup.table, cutoff) }); err != nil {
			return err
		}
	}
	var pending bool
	if err := effect(func(tx BakneySSOTransaction) (err error) { pending, err = tx.PairingNeedsSync(ctx); return err }); err != nil {
		return err
	}
	// The job completes in the transaction of its last effect.
	return effect(func(tx BakneySSOTransaction) error {
		if pending {
			st, err := s.lockedPairing(ctx, tx)
			if err == nil && (st.pairing.State == "pending" || st.pairing.State == "paired") && st.pairing.RemotePairingID != nil {
				err = s.synchronize(ctx, st)
			}
			var ssoErr *BakneySSOError
			if err != nil && !errors.As(err, &ssoErr) {
				return err
			}
			// An SSOError is swallowed: retain the last checked_at; login always requires a live check.
		}
		return tx.CompleteJob(ctx, job.ID, job.LeaseToken)
	})
}

package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/codicefiscale"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// SubscriptionMisc ports subscription_calculate_tax_code, subscription_import_status,
// get_associations_for_federation, subscription_generate_token_link,
// validate_token_link_and_get_subscriptions and subscription_delete_document
// (subscriptions_views.py).
type SubscriptionMisc struct {
	store domain.SubscriptionMiscStore
	reads *SubscriptionReads
	now   func() time.Time
	ids   func() string
	// links asks the paired server about unknown link tokens (nil: such lookups are refused).
	links PublicTokenSource
}

// WithPublicLinks sets the paired server lookup for unknown link tokens.
func (s *SubscriptionMisc) WithPublicLinks(links PublicTokenSource) *SubscriptionMisc {
	s.links = links
	return s
}

func NewSubscriptionMisc(store domain.SubscriptionMiscStore, reads *SubscriptionReads, now func() time.Time, ids func() string) *SubscriptionMisc {
	return &SubscriptionMisc{store: store, reads: reads, now: now, ids: ids}
}

var (
	// ErrSubscriptionMiscMissingData is calculate-tax-code's 400 'alcuni dati sono mancanti.'.
	ErrSubscriptionMiscMissingData = errors.New("tax code data missing")
	// ErrSubscriptionMiscInvalidData is calculate-tax-code's 400 'alcuni dati non sono validi.'.
	ErrSubscriptionMiscInvalidData = errors.New("tax code data invalid")
	// ErrSubscriptionMiscUnmapped is calculate-tax-code's 400 'Città non mappata.'.
	ErrSubscriptionMiscUnmapped = errors.New("tax code birthplace not mapped")
	// ErrSubscriptionMiscGymRequired is generate-token-link's 400 'gym_name is required'.
	ErrSubscriptionMiscGymRequired = errors.New("gym name required")
	// ErrSubscriptionMiscUUIDMissing is is_valid_uuid(None): uuid.UUID's TypeError message.
	ErrSubscriptionMiscUUIDMissing = &PaymentInfoUUIDError{Detail: "one of the hex, bytes, bytes_le, fields, or int arguments must be given"}
)

func subscriptionMiscNotAssociation() error {
	return &domain.PermissionDenied{Detail: "User is not a sport association"}
}

func (s *SubscriptionMisc) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// association is request.user.sport_association (athletes: PermissionDenied;
// no live association: DoesNotExist, a 500).
func (s *SubscriptionMisc) association(ctx context.Context, tx domain.SubscriptionMiscTransaction, identity domain.ResolvedIdentity) (string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return "", subscriptionMiscNotAssociation()
	}
	return tx.Association(ctx, identity.ScopeUser.ID)
}

// object is request.data as a dict: an empty body parses to an empty dict;
// any other non-dict body makes .keys()/.get() an AttributeError (ok false).
func subscriptionMiscObject(body any) (domain.RequestObject, bool) {
	switch value := body.(type) {
	case nil:
		return domain.RequestObject{}, true
	case domain.RequestObject:
		return value, true
	}
	return nil, false
}

// TaxCode follows subscription_calculate_tax_code.
func (s *SubscriptionMisc) TaxCode(body any) (any, error) {
	data, ok := subscriptionMiscObject(body)
	if !ok {
		return nil, domain.ErrSubscriptionMiscServer
	}
	values := map[string]any{}
	for _, key := range []string{"first_name", "sex", "last_name", "born_date", "born_city"} {
		value, present := data.Lookup(key)
		if !present {
			return domain.RequestObject{{Name: "tax_code", Value: nil}}, nil
		}
		values[key] = value
	}
	blank := func(value any) bool { return value == nil || value == "" }
	if blank(values["last_name"]) || blank(values["first_name"]) || values["sex"] == nil {
		return nil, ErrSubscriptionMiscMissingData
	}
	sex, isText := values["sex"].(string)
	if !isText {
		return nil, domain.ErrSubscriptionMiscServer // data['sex'].upper(): AttributeError
	}
	if sex != "m" && sex != "M" && sex != "f" && sex != "F" {
		return nil, ErrSubscriptionMiscMissingData
	}
	if blank(values["born_date"]) || blank(values["born_city"]) {
		return nil, ErrSubscriptionMiscMissingData
	}
	// codicefiscale.encode raises (TypeError, ValueError) for any non-string
	// name, date or birthplace before it could report an unmapped place.
	text := map[string]string{}
	for _, key := range []string{"last_name", "first_name", "born_date", "born_city"} {
		value, isText := values[key].(string)
		if !isText {
			return nil, ErrSubscriptionMiscInvalidData
		}
		text[key] = value
	}
	code, err := codicefiscale.Encode(text["last_name"], text["first_name"], sex, text["born_date"], text["born_city"], s.now())
	switch {
	case errors.Is(err, codicefiscale.ErrNotMapped):
		return nil, ErrSubscriptionMiscUnmapped
	case errors.Is(err, codicefiscale.ErrInvalid):
		return nil, ErrSubscriptionMiscInvalidData
	case err != nil:
		return nil, domain.ErrSubscriptionMiscNotPorted
	}
	return domain.RequestObject{{Name: "tax_code", Value: code}}, nil
}

// ImportStatus follows subscription_import_status.
func (s *SubscriptionMisc) ImportStatus(ctx context.Context, identity domain.ResolvedIdentity) (any, error) {
	tx, err := s.store.BeginSubscriptionMisc(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return nil, err
	}
	found, doing, err := tx.ImportDoing(ctx, association)
	if err != nil {
		return nil, err
	}
	var importing any = false
	if found {
		importing = nil
		if doing != nil {
			importing = *doing
		}
	}
	return domain.RequestObject{{Name: "importing", Value: importing}}, nil
}

// subscriptionMiscLike is '%' + connection.ops.prep_for_like_query(value) + '%'.
func subscriptionMiscLike(value string) string {
	replacer := strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)
	return "%" + replacer.Replace(value) + "%"
}

// subscriptionMiscHashKey is the set identity of a hashable parsed JSON value
// (Python equality: 1 == 1.0 == True); ok false for dicts and lists.
func subscriptionMiscHashKey(value any) (string, bool) {
	switch v := value.(type) {
	case nil:
		return "none", true
	case string:
		return "s:" + v, true
	case bool:
		if v {
			return "n:1", true
		}
		return "n:0", true
	case json.Number:
		rat := new(big.Rat)
		text := string(v)
		if strings.ContainsAny(text, ".eE") {
			float, err := v.Float64()
			if err != nil || math.IsInf(float, 0) {
				return "inf:" + text, true
			}
			rat.SetFloat64(float)
		} else if _, ok := rat.SetString(text); !ok {
			return "", false
		}
		return "n:" + rat.RatString(), true
	}
	return "", false
}

// Federation follows get_associations_for_federation.
func (s *SubscriptionMisc) Federation(ctx context.Context, identity domain.ResolvedIdentity, groupHeader string) (any, error) {
	tx, err := s.store.BeginSubscriptionMisc(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return nil, err
	}
	patterns := []string{subscriptionMiscLike(`"type_of_associate": "asd-o-ssd"`), subscriptionMiscLike(`"type_of_associate": "scuola-asc"`)}
	rows, err := tx.Federation(ctx, association, patterns, supplierGroup(groupHeader), paymentReadsScope(identity))
	if err != nil {
		return nil, err
	}
	associations := []any{}
	seen := map[string]bool{}
	for _, row := range rows {
		// Every exception of the loop body is logged and skipped.
		if row.CustomData == nil {
			continue
		}
		parsed, err := dto.ParseProfileTablesJSON([]byte(*row.CustomData))
		if err != nil {
			return nil, err
		}
		custom, isDict := parsed.(domain.RequestObject)
		if !isDict {
			continue
		}
		denomination, present := custom.Lookup("denomination")
		if !present {
			continue
		}
		key, hashable := subscriptionMiscHashKey(denomination)
		if !hashable || seen[key] {
			continue
		}
		seen[key] = true
		associations = append(associations, domain.RequestObject{{Name: "id", Value: row.ID}, {Name: "denomination", Value: denomination}})
	}
	return associations, nil
}

// GenerateLink follows subscription_generate_token_link.
func (s *SubscriptionMisc) GenerateLink(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error) {
	data, ok := subscriptionMiscObject(body)
	if !ok {
		return nil, domain.ErrSubscriptionMiscServer // request.data.get: AttributeError
	}
	gym, _ := data.Lookup("gym_name")
	if gym == nil {
		return nil, ErrSubscriptionMiscGymRequired
	}
	tx, err := s.store.BeginSubscriptionMisc(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := s.association(ctx, tx, identity)
	if err != nil {
		return nil, err
	}
	// CharField(max_length=255) stores str(value); PostgreSQL rejects a NUL,
	// psycopg a lone surrogate, varchar(255) a longer text (no row is written).
	// str() of an uploaded file is its name.
	stored := dto.PythonStr(gym)
	echoed := gym
	if upload, isUpload := gym.(domain.RequestUpload); isUpload {
		if !upload.Whole && upload.Size > 0 {
			return nil, domain.ErrSubscriptionMiscNotPorted // the lines of a file over one chunk are not retained
		}
		stored = upload.Name
		// The response carries the in-memory file: DRF's encoder iterates it
		// (File.__iter__ is bytes.splitlines(True) per chunk) and decodes each line.
		echoed = subscriptionMiscFileLines(upload.Content)
	}
	if !utf8.ValidString(stored) || strings.ContainsRune(stored, 0) || utf8.RuneCountInString(stored) > 255 {
		return nil, domain.ErrSubscriptionMiscServer
	}
	now := s.at()
	expires := now.Add(48 * time.Hour)
	token, id := s.ids(), s.ids()
	if err = tx.CreateLink(ctx, id, association, token, stored, expires, now, paymentCategoryAuditContext(identity, audit), now); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	result := domain.RequestObject{{Name: "token", Value: token}, {Name: "gym_name", Value: echoed},
		{Name: "expires_at", Value: dto.DRFUTCDateTime(expires)}}
	if upload, isUpload := gym.(domain.RequestUpload); (isUpload && !utf8.ValidString(upload.Content)) || !dto.AssociateDraftRenderable(result) {
		return nil, domain.ErrSubscriptionMiscServer // the renderer fails after the link is saved
	}
	return result, nil
}

// subscriptionMiscAudit is the auditlog context of a request that may be anonymous.
func subscriptionMiscAudit(identity domain.ResolvedIdentity, audit domain.LoginAuditContext) domain.LoginAuditContext {
	if identity.Actor == nil {
		audit.ActorID, audit.ActorEmail, audit.AdditionalData = nil, nil, nil
		return audit
	}
	return paymentCategoryAuditContext(identity, audit)
}

// subscriptionMiscCanonical is source_token's canonical_uuid(token).
func subscriptionMiscCanonical(token string) bool {
	parsed, err := paymentInfoParseUUID(token)
	return err == nil && parsed.String() == token
}

// ValidateLink follows validate_token_link_and_get_subscriptions.
func (s *SubscriptionMisc) ValidateLink(ctx context.Context, identity domain.ResolvedIdentity, query domain.RequestObject, groupHeader string,
	audit domain.LoginAuditContext) (any, error) {
	raw, present := query.Lookup("token")
	token, _ := raw.(string)
	if !present {
		return nil, ErrSubscriptionMiscUUIDMissing
	}
	if _, err := paymentInfoParseUUID(token); err != nil {
		return nil, err
	}
	now := s.at()
	tx, err := s.store.BeginSubscriptionMisc(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	link, err := tx.LiveLink(ctx, token, now)
	if err != nil {
		return nil, err
	}
	if link == nil && subscriptionMiscCanonical(token) && s.links == nil {
		// source_token asks the paired server for unknown tokens.
		lookup, err := tx.PairingLookup(ctx)
		if err != nil {
			return nil, err
		}
		if lookup {
			return nil, domain.ErrSubscriptionMiscNotPorted
		}
	}
	var actorAssociation *string
	if identity.Actor != nil {
		if actorAssociation, err = tx.ActorAssociation(ctx, identity.Actor.ID); err != nil {
			return nil, err
		}
	}
	if err = tx.DeleteExpiredLinks(ctx, now, actorAssociation, subscriptionMiscAudit(identity, audit), now); err != nil {
		return nil, err
	}
	// Requests are not atomic: the deletions stay whatever happens next.
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	if link == nil && s.links != nil {
		granted, err := s.links.SourceToken(ctx, "cumulative", token, "", nil)
		if err != nil {
			return nil, err
		}
		if granted != nil {
			// An unsaved CumulativeSubscriptionGymLinks.
			link = &domain.SubscriptionMiscLink{AssociationID: granted.AssociationID, GymName: &granted.GymName}
		}
	}
	if link == nil {
		return domain.RequestObject{{Name: "expired", Value: true}}, nil
	}
	if link.GymName == nil {
		return nil, domain.ErrSubscriptionMiscServer // len(None)
	}
	return s.matches(ctx, identity, *link, groupHeader, now)
}

func (s *SubscriptionMisc) matches(ctx context.Context, identity domain.ResolvedIdentity, link domain.SubscriptionMiscLink, groupHeader string,
	now time.Time) (any, error) {
	group, scope := supplierGroup(groupHeader), paymentReadsScope(identity)
	tx, err := s.store.BeginSubscriptionMisc(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	year, month, day := now.UTC().Date()
	today := time.Date(year, month, day, 0, 0, 0, 0, time.UTC)
	periods, err := tx.ActivePeriods(ctx, link.AssociationID, today, group, scope)
	if err != nil {
		return nil, err
	}
	// Subscription.active: current_year() == 'corrente'; None ids are dropped from the IN list.
	excluded := []string{}
	for _, period := range periods {
		active := period.Start == nil || period.End == nil ||
			(!period.Start.After(today) && !today.After(*period.End))
		if active && period.AssociateID != nil {
			excluded = append(excluded, *period.AssociateID)
		}
	}
	gym := []rune(*link.GymName)
	needle := gym
	if len(gym) > 3 {
		needle = gym[:3]
	}
	cutoff := now.UTC().Add(-180 * 24 * time.Hour)
	cutoffDay := time.Date(cutoff.Year(), cutoff.Month(), cutoff.Day(), 0, 0, 0, 0, time.UTC)
	candidates, err := tx.Candidates(ctx, link.AssociationID, subscriptionMiscLike(string(needle)), cutoffDay, excluded, group, scope)
	if err != nil {
		return nil, err
	}
	lowered := pythonLower(*link.GymName)
	customs := map[string]domain.RequestObject{}
	matched := []string{}
	for _, candidate := range candidates {
		parsed, err := dto.ParseProfileTablesJSON([]byte(*candidate.CustomData))
		if err != nil {
			return nil, err
		}
		custom, isDict := parsed.(domain.RequestObject)
		if !isDict {
			return nil, domain.ErrSubscriptionMiscServer // custom_data.keys(): AttributeError
		}
		name, present := custom.Lookup("school_name")
		if !present {
			if name, present = custom.Lookup("denomination"); !present {
				continue
			}
		}
		text, isText := name.(string)
		if !isText {
			return nil, domain.ErrSubscriptionMiscServer // .lower(): AttributeError
		}
		if fuzzRatio(lowered, pythonLower(text)) > 70 {
			matched = append(matched, candidate.ID)
			customs[candidate.ID] = custom
		}
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	items, err := s.reads.Serialized(ctx, matched, group)
	if err != nil {
		return nil, err
	}
	var defaults any
	for _, id := range matched {
		custom := customs[id]
		school, present := custom.Lookup("school_name")
		if !present {
			continue
		}
		get := func(key string) any {
			if value, ok := custom.Lookup(key); ok {
				return value
			}
			return ""
		}
		defaults = domain.RequestObject{{Name: "school_name", Value: school}, {Name: "school_teacher_name", Value: get("school_teacher_name")},
			{Name: "responsible_instructor_name", Value: get("responsible_instructor_name")},
			{Name: "principal_style", Value: get("principal_style")}, {Name: "type_of_associate", Value: "atleta"},
			{Name: "denomination", Value: get("denomination")}, {Name: "type", Value: json.Number("2")}}
		break
	}
	return domain.RequestObject{{Name: "expired", Value: false}, {Name: "subscriptions", Value: items}, {Name: "default_custom_data", Value: defaults}}, nil
}

// DeleteDocument follows subscription_delete_document.
func (s *SubscriptionMisc) DeleteDocument(ctx context.Context, identity domain.ResolvedIdentity, uid, fileID, groupHeader string,
	audit domain.LoginAuditContext) error {
	subscription, err := paymentInfoParseUUID(uid)
	if err != nil {
		return err
	}
	file, err := paymentInfoParseUUID(fileID)
	if err != nil {
		return err
	}
	scope := paymentCategoryAssociationScope(identity)
	tx, err := s.store.BeginSubscriptionMisc(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.ScopedFirstAssociation(ctx, identity.ScopeUser.ID, scope)
	if err != nil {
		return err
	}
	if association == nil {
		return &domain.PermissionDenied{Detail: "User not allowed."}
	}
	found, err := tx.Subscription(ctx, subscription.String(), *association, supplierGroup(groupHeader), scope)
	if err != nil {
		return err
	}
	if !found {
		return domain.ErrSubscriptionMiscServer // TypeError("subscription not found!")
	}
	row, err := tx.File(ctx, file.String(), subscription.String())
	if err != nil {
		return err
	}
	if row == nil {
		return domain.ErrSubscriptionMiscServer // TypeError("subscription file not found!")
	}
	actorAssociation, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	auditContext, at := paymentCategoryAuditContext(identity, audit), s.at()
	if scope != nil {
		if _, err = tx.DeleteFile(ctx, row.ID, actorAssociation, auditContext, at); err != nil {
			return err
		}
		return tx.Commit()
	}
	// A document other rows still reference stays with them: only this link goes.
	shared, err := tx.DocumentShared(ctx, row.DocumentID, row.ID)
	if err != nil {
		return err
	}
	if shared {
		if _, err = tx.DeleteFile(ctx, row.ID, actorAssociation, auditContext, at); err != nil {
			return err
		}
		return tx.Commit()
	}
	changes, err := tx.DeleteFile(ctx, row.ID, actorAssociation, auditContext, at)
	if err != nil {
		return err
	}
	if err = tx.DeleteDocument(ctx, row.DocumentID, actorAssociation, auditContext, at); err != nil {
		return err
	}
	// subscription_file.delete() on the view's instance: the row is gone, the
	// post_delete signal still records it.
	if err = tx.RecordFileDelete(ctx, row.ID, changes, actorAssociation, auditContext, at); err != nil {
		return err
	}
	return tx.Commit()
}

// pythonLower is str.lower(): Go's simple mappings plus the special casing of
// U+0130 (i + combining dot) and the final sigma.
func pythonLower(value string) string {
	runes := []rune(value)
	var out strings.Builder
	cased := func(r rune) bool { return unicode.IsUpper(r) || unicode.IsLower(r) || unicode.IsTitle(r) }
	ignorable := func(r rune) bool {
		return unicode.In(r, unicode.Mn, unicode.Me, unicode.Cf, unicode.Lm, unicode.Sk) || strings.ContainsRune("'.:·’‘․‧", r)
	}
	for i, r := range runes {
		switch r {
		case 0x130:
			out.WriteString("i̇")
			continue
		case 0x3a3:
			before, after := false, false
			for j := i - 1; j >= 0; j-- {
				if !ignorable(runes[j]) {
					before = cased(runes[j])
					break
				}
			}
			for j := i + 1; j < len(runes); j++ {
				if !ignorable(runes[j]) {
					after = cased(runes[j])
					break
				}
			}
			if before && !after {
				out.WriteRune(0x3c2)
				continue
			}
		}
		out.WriteRune(unicode.ToLower(r))
	}
	return out.String()
}

// fuzzRatio is fuzzywuzzy's fuzz.ratio with difflib's SequenceMatcher (no
// python-Levenshtein): 100 for equal strings, 0 when one is empty, otherwise
// round(100 * ratio()) with banker's rounding.
func fuzzRatio(first, second string) int {
	if first == second {
		return 100
	}
	a, b := []rune(first), []rune(second)
	if len(a) == 0 || len(b) == 0 {
		return 0
	}
	matches := sequenceMatches(a, b)
	ratio := 2.0 * float64(matches) / float64(len(a)+len(b))
	return int(math.RoundToEven(100 * ratio))
}

// sequenceMatches is the total size of difflib.SequenceMatcher(None, a, b)'s
// matching blocks (autojunk on, no junk function).
func sequenceMatches(a, b []rune) int {
	b2j := map[rune][]int{}
	for i, r := range b {
		b2j[r] = append(b2j[r], i)
	}
	if n := len(b); n >= 200 {
		limit := n/100 + 1
		for r, indexes := range b2j {
			if len(indexes) > limit {
				delete(b2j, r)
			}
		}
	}
	longest := func(alo, ahi, blo, bhi int) (int, int, int) {
		besti, bestj, bestsize := alo, blo, 0
		j2len := map[int]int{}
		for i := alo; i < ahi; i++ {
			next := map[int]int{}
			for _, j := range b2j[a[i]] {
				if j < blo {
					continue
				}
				if j >= bhi {
					break
				}
				k := j2len[j-1] + 1
				next[j] = k
				if k > bestsize {
					besti, bestj, bestsize = i-k+1, j-k+1, k
				}
			}
			j2len = next
		}
		for besti > alo && bestj > blo && a[besti-1] == b[bestj-1] {
			besti, bestj, bestsize = besti-1, bestj-1, bestsize+1
		}
		for besti+bestsize < ahi && bestj+bestsize < bhi && a[besti+bestsize] == b[bestj+bestsize] {
			bestsize++
		}
		return besti, bestj, bestsize
	}
	total := 0
	queue := [][4]int{{0, len(a), 0, len(b)}}
	for len(queue) > 0 {
		span := queue[len(queue)-1]
		queue = queue[:len(queue)-1]
		i, j, k := longest(span[0], span[1], span[2], span[3])
		if k == 0 {
			continue
		}
		total += k
		if span[0] < i && span[2] < j {
			queue = append(queue, [4]int{span[0], i, span[2], j})
		}
		if i+k < span[1] && j+k < span[3] {
			queue = append(queue, [4]int{i + k, span[1], j + k, span[3]})
		}
	}
	return total
}

// subscriptionMiscFileLines is tuple(file) for a one-chunk file: the lines
// with their endings (\n, \r or \r\n); a line that is no UTF-8 stays a
// byte string the renderer cannot encode (AssociateDraftRenderable fails).
func subscriptionMiscFileLines(content string) []any {
	lines := []any{}
	start := 0
	for i := 0; i < len(content); i++ {
		switch content[i] {
		case '\n':
		case '\r':
			if i+1 < len(content) && content[i+1] == '\n' {
				i++
			}
		default:
			continue
		}
		lines = append(lines, content[start:i+1])
		start = i + 1
	}
	if start < len(content) {
		lines = append(lines, content[start:])
	}
	return lines
}

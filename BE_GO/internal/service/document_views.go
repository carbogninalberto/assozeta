package service

import (
	"context"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/djtemplate"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/mentions"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// HTML views of BE/docmanager/views/printing_views.py.
const (
	ViewSubscription = "subscription-view"
	ViewPreview      = "subscription-preview"
	ViewCompensation = "compensation-view"
	ViewTemplate     = "template-view"
	ViewMedical      = "medical-appointment-view"
)

var (
	// ErrDocumentViewServer is an exception the view does not handle (the 500 page).
	ErrDocumentViewServer = errors.New("document view: unhandled exception")
	// errDocumentViewSignature is a view built without the signature storage:
	// cmd/server/main.go always calls WithSignatureObjects. The 500 page, never
	// a handoff.
	errDocumentViewSignature = refusal.AppState("document view: signature image not read")
	// errDocumentViewCollaboratorOwner is an association whose user is a
	// collaborator: the writers create it for an ASSOCIATION-role owner
	// (BE/instance/views.py:200-209, the import's owner user,
	// BE/application/services/import_service.py:1059-1063). The 500 page.
	errDocumentViewCollaboratorOwner = refusal.AppState("document view: collaborator account as an association's user")
)

// PreviewRenderer is pdfrender.Client's call with the request's
// Authorization header.
type PreviewRenderer interface {
	Render(ctx context.Context, url string, authorization *string) ([]byte, error)
}

type DocumentViews struct {
	store            domain.DocumentViewStore
	renderer         PreviewRenderer
	signatures       domain.DocumentViewObjects
	signatureFetcher LegacySignatureFetcher
	bypassToken      string
	now              func() time.Time
}

func NewDocumentViews(store domain.DocumentViewStore, renderer PreviewRenderer, bypassToken string, now func() time.Time) *DocumentViews {
	return &DocumentViews{store: store, renderer: renderer, bypassToken: bypassToken, now: now}
}

// WithSignatureObjects configures default_storage for get_signature_base64.
// Without it a subscription that names a stored signature stays refused.
func (s *DocumentViews) WithSignatureObjects(objects domain.DocumentViewObjects) *DocumentViews {
	s.signatures = objects
	return s
}

// WithLegacySignatureFetcher configures the HTTP read of a subscription whose
// signature_url is set. cmd/server always calls it.
func (s *DocumentViews) WithLegacySignatureFetcher(f LegacySignatureFetcher) *DocumentViews {
	s.signatureFetcher = f
	return s
}

// DocumentViewRequest is one call of an HTML view.
type DocumentViewRequest struct {
	View, UID, Path string
	Query           domain.RequestObject
	Group           string
	Language        string
	// Authorization is request.headers.get('authorization'): the preview's
	// PDF is rendered as the requester.
	Authorization *string
	// Promotion admits get_main_tutor's saved relation; nil answers the 500
	// page instead (the handlers always supply it).
	Promotion *DocumentViewPromotion
}

// DocumentViewPromotion is what get_main_tutor's audited save needs: the
// request's audit context and the write admission.
type DocumentViewPromotion struct {
	Admit func() (context.Context, domain.LoginAuditContext, error)
}

type viewPromotionKey struct{}

type viewPromotion struct {
	identity domain.ResolvedIdentity
	request  *DocumentViewPromotion
}

// withViewPromotion makes the request's promotion available to get_main_tutor.
// A request the renderer's header admits has no account: Django still performs
// the save, its auditlog entry with no actor.
func withViewPromotion(ctx context.Context, identity domain.ResolvedIdentity, promotion *DocumentViewPromotion) context.Context {
	if promotion == nil {
		return ctx
	}
	return context.WithValue(ctx, viewPromotionKey{}, &viewPromotion{identity: identity, request: promotion})
}

// viewPromotionAuditContext is paymentCategoryAuditContext for a request that
// may have no account: Django's auditlog entry then carries no actor, address
// and correlation id as the request supplied them.
func viewPromotionAuditContext(identity domain.ResolvedIdentity, audit domain.LoginAuditContext) domain.LoginAuditContext {
	if identity.Actor == nil {
		audit.ActorID, audit.ActorEmail, audit.AdditionalData = nil, nil, nil
		return audit
	}
	return paymentCategoryAuditContext(identity, audit)
}

// viewPromotionActorID is the audit actor's id, nil without an account.
func viewPromotionActorID(identity domain.ResolvedIdentity) *string {
	if identity.Actor == nil {
		return nil
	}
	id := identity.Actor.ID
	return &id
}

// mainTutor is Associate.get_main_tutor(): without a primary relation the
// first one is promoted and saved as the view renders.
func (s *DocumentViews) mainTutor(ctx context.Context, associate string) (*domain.ViewAssociate, error) {
	tutor, err := s.store.MainTutor(ctx, associate)
	if !errors.Is(err, domain.ErrTutorPromotionNeeded) {
		return tutor, err
	}
	promotion, _ := ctx.Value(viewPromotionKey{}).(*viewPromotion)
	if promotion == nil {
		// The handlers always admit the save; a caller without it cannot do what Django does.
		return nil, ErrDocumentViewServer
	}
	admitted, audit, admitErr := promotion.request.Admit()
	if admitErr != nil {
		return nil, admitErr
	}
	return s.store.PromoteMainTutor(admitted, associate, domain.ViewTutorPromotion{
		Context: viewPromotionAuditContext(promotion.identity, audit), ActorID: viewPromotionActorID(promotion.identity),
		At: s.now().UTC().Truncate(time.Microsecond)})
}

// DocumentViewReply: an HTML page (with the language it was rendered in) or a DRF JSON body.
type DocumentViewReply struct {
	Status   int
	HTML     *string
	JSON     any
	Language string
}

// AuthorizeTemplate is HasBypassAuthorizationHeader after IsAuthenticated failed.
func (s *DocumentViews) AuthorizeBypass(authorization *string) (bool, error) {
	if authorization == nil {
		return false, nil
	}
	for _, r := range *authorization {
		if r > 0x7f {
			return false, ErrDocumentViewServer
		}
	}
	return s.bypassToken != "" && subtle.ConstantTimeCompare([]byte(*authorization), []byte(s.bypassToken)) == 1, nil
}

func (s *DocumentViews) Render(ctx context.Context, identity domain.ResolvedIdentity, request DocumentViewRequest) (*DocumentViewReply, error) {
	reply, err := s.render(withViewPromotion(ctx, identity, request.Promotion), identity, request)
	if err != nil {
		if errors.Is(err, djtemplate.ErrPython) {
			// An exception Django raises for a value the database can hold:
			// the 500 page, not a request for Django.
			return nil, fmt.Errorf("%w: %v", ErrDocumentViewServer, err)
		}
		var input *PaymentInfoUUIDError
		if errors.As(err, &input) {
			return &DocumentViewReply{Status: 400, JSON: []string{input.Detail}}, nil
		}
		return nil, err
	}
	return reply, nil
}

func (s *DocumentViews) render(ctx context.Context, identity domain.ResolvedIdentity, request DocumentViewRequest) (*DocumentViewReply, error) {
	switch request.View {
	case ViewTemplate:
		return s.template(ctx, identity, request)
	case ViewMedical:
		return s.medical(ctx, identity, request)
	case ViewCompensation:
		return s.compensation(ctx, identity, request)
	case ViewSubscription:
		return s.subscription(ctx, identity, request)
	case ViewPreview:
		return s.preview(ctx, identity, request)
	}
	return nil, ErrDocumentViewServer
}

// --- values ------------------------------------------------------------------------------------

func optional(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

func optionalDate(value *time.Time) any {
	if value == nil {
		return nil
	}
	return djtemplate.DateOf(*value)
}

func jsonValue(raw *string) (any, error) {
	if raw == nil {
		return nil, nil
	}
	return djtemplate.FromJSON([]byte(*raw))
}

// lookupError maps a missing row (or a value the field rejects) to Django's
// unhandled DoesNotExist / ValidationError.
func lookupError(err error) error {
	if errors.Is(err, domain.ErrViewNotFound) {
		return ErrDocumentViewServer
	}
	return err
}

// parseModelUUID is UUIDField.to_python: a value it rejects raises ValidationError.
func parseModelUUID(raw string) (string, bool) {
	parsed, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", false
	}
	return parsed.String(), true
}

// pythonFullNameOf is get_full_name(): ('%s %s' % (first, last)).strip().
func pythonFullNameOf(first, last *string) string {
	return dto.PythonStrip(renderOptional(first) + " " + renderOptional(last))
}

// calculateAge is Associate.calculate_age() for a date (0 without one):
// int((today - born).days / 365.25).
func calculateAge(born *time.Time, today time.Time) int {
	if born == nil {
		return 0
	}
	days := civilDays(today.Year(), int(today.Month()), today.Day()) - civilDays(born.Year(), int(born.Month()), born.Day())
	return int(float64(days) / 365.25)
}

// civilDays counts days from 0001-01-01 (proleptic Gregorian).
func civilDays(year, month, day int) int {
	if month <= 2 {
		year--
		month += 12
	}
	return 365*year + year/4 - year/100 + year/400 + (153*(month-3)+2)/5 + day
}

func (s *DocumentViews) today() time.Time { return s.now().UTC() }

// associateObject is an Associate as the templates read it.
func (s *DocumentViews) associateObject(ctx context.Context, associate *domain.ViewAssociate) *djtemplate.Object {
	full := pythonFullNameOf(associate.FirstName, associate.LastName)
	id := associate.ID
	mainTutor := djtemplate.Lazy(func() (any, error) {
		tutor, err := s.mainTutor(ctx, id)
		if err != nil || tutor == nil {
			return nil, err
		}
		return s.associateObject(ctx, tutor), nil
	})
	return djtemplate.NewObject("Associate", map[string]any{
		"first_name": optional(associate.FirstName), "last_name": optional(associate.LastName), "sex": associate.Sex,
		"tax_code": optional(associate.TaxCode), "born_date": optionalDate(associate.BornDate), "born_city": optional(associate.BornCity),
		"address": optional(associate.Address), "address_city": optional(associate.AddressCity),
		"address_cap": optional(associate.AddressCap), "phone": optional(associate.Phone), "email": optional(associate.Email),
		"get_full_name": djtemplate.Lazy(func() (any, error) { return full, nil }), "full_name": full,
		"is_minor_now": calculateAge(associate.BornDate, s.today()) < 18, "main_tutor": mainTutor,
		"health_insurance_card": djtemplate.Missing, "tutor": djtemplate.Missing,
	})
}

// --- mentions ----------------------------------------------------------------------------------

// fieldResolver is get_nested_attr of a model instance. The listed paths
// are the port's fields; every other path renders as the empty text.
//
// A path outside the modelled fields renders as the empty text (accepted
// divergence X20): get_nested_attr's answer when getattr(obj, name, None)
// cannot resolve a name. Django renders str() of the attribute for a model
// attribute the port does not model (a relation, a method); the editor's
// mention list only offers modelled keys.
func fieldResolver(attributes map[string]struct{}, fields map[string]*string) mentions.Resolver {
	return func(path string) (string, error) {
		value, ok := fields[path]
		if !ok {
			// Both an unknown first name and a modelled attribute the port
			// does not render are X20: the empty text.
			return "", nil
		}
		if value == nil {
			return "", nil
		}
		return *value, nil
	}
}

func dateText(value *time.Time) *string {
	if value == nil {
		return nil
	}
	text := value.Format("2006-01-02")
	return &text
}

func associationResolver(a *domain.ViewAssociation) mentions.Resolver {
	denomination, taxCode := a.Denomination, a.TaxCode
	return fieldResolver(mentionSportAssociationAttributes, map[string]*string{"denomination": &denomination, "tax_code": &taxCode, "iban": a.Iban, "website": a.Website,
		"abbreviated": a.Abbreviated, "vat_number": a.VatNumber, "whatsapp": a.Whatsapp, "address": a.Address,
		"address_city": a.AddressCity, "address_cap": a.AddressCap, "logo": a.Logo, "user.email": a.UserEmail,
		"federation": a.Federation, "sport": a.Sport, "enroll_number": a.EnrollNumber})
}

// associateResolver: get_nested_attr of an Associate (nil: None, every path "").
func associateResolver(a *domain.ViewAssociate) mentions.Resolver {
	if a == nil {
		return func(string) (string, error) { return "", nil }
	}
	sex := a.Sex
	return fieldResolver(mentionAssociateAttributes, map[string]*string{"first_name": a.FirstName, "last_name": a.LastName, "tax_code": a.TaxCode,
		"born_date": dateText(a.BornDate), "born_city": a.BornCity, "address": a.Address, "address_city": a.AddressCity,
		"address_cap": a.AddressCap, "nationality": a.Nationality, "nationality_residence": a.NationalityRes, "email": a.Email,
		"phone": a.Phone, "phone_2": a.Phone2, "phone_3": a.Phone3, "phone_4": a.Phone4, "phone_label": a.PhoneLabel,
		"phone_2_label": a.Phone2Label, "phone_3_label": a.Phone3Label, "phone_4_label": a.Phone4Label, "notes": a.Notes, "sex": &sex})
}

// --- other.<key>.<attribute...> ----------------------------------------------------------------

// get_nested_attr (BE/application/models/utils.py:118-136) reads the 'other'
// dict with .get, then getattr(obj, name, None) for each following step, and
// answers str() of the last object ("" for None). Every view fills 'other'
// with str: 'today' and 'today_datetime' are strftime texts, 'courses_list'
// is Subscription.get_courses_list() and the medical view's 'address' is a
// model field (BE/docmanager/views/printing_views.py:165-177, 253-265,
// 495-500, 755-760, 845-851). A path below a key therefore walks CPython's
// str object graph, which is closed: a str's attributes are its methods,
// method-wrappers, __class__ and __doc__; a method's are its name, qualname,
// __self__, __doc__, __module__, __class__ and __text_signature__; and the
// str type's are its descriptors and the metadata every type has. The text
// below is what CPython 3.13.16 prints for each of them, with
// pystr.ObjectAddress in place of the address of a live object (approved
// divergence X18).
//
// A path outside the model renders as the empty text (X20) rather than
// going to Django: the str type's __dict__, __flags__, __basicsize__,
// __itemsize__ and offsets (a mappingproxy of addresses and values that
// depend on the build), and the dunder methods of a method object
// (`other.today.upper.__call__` and the like: determined, but a second object
// layer this file does not reproduce).

// mentionPyKind is the type of one object of that walk.
type mentionPyKind uint8

const (
	mentionNone mentionPyKind = iota
	mentionStr
	mentionStrType
	mentionType
	mentionBuiltin
	mentionWrapper
	mentionMethod
	mentionSlot
	mentionGetSet
)

// mentionBind is which object a built-in method's m_self is: its repr and
// __qualname__ print it. __self__ is None for a static method.
type mentionBind uint8

const (
	mentionBindValue      mentionBind = iota // the object getattr read it from
	mentionBindType                          // that object's type (a str's is str)
	mentionBindStaticType                    // that object's type; __self__ is None
	mentionBindStatic                        // the object itself; __self__ is None
	mentionBindMeta                          // type
)

// mentionSpec is one attribute getattr answers on the objects below: the
// object, and the docstring and text signature CPython gives it. owner is the
// type that defines a method-wrapper or a method/slot descriptor (its repr
// and __qualname__); doc and sig are "" where Python has None.
type mentionSpec struct {
	kind  mentionPyKind
	bind  mentionBind
	owner string
	doc   string
	sig   string
}

// mentionPy is one object of the walk.
type mentionPy struct {
	kind mentionPyKind
	name string     // a str's text, a method's name, a type's name
	recv *mentionPy // a built-in method's m_self, a method-wrapper's object
	spec mentionSpec
}

// mentionStrDoc is str.__doc__ (CPython 3.13.16).
const mentionStrDoc = "str(object='') -> str\nstr(bytes_or_buffer[, encoding[, errors]]) -> str\n\nCreate a new string object from the given object. If encoding or\nerrors is specified, then the object must expose a data buffer\nthat will be decoded using the given encoding and error handler.\nOtherwise, returns the result of object.__str__() (if defined)\nor repr(object).\nencoding defaults to 'utf-8'.\nerrors defaults to 'strict'."

var (
	mentionStrTypeObject  = &mentionPy{kind: mentionStrType, name: "str"}
	mentionTypeTypeObject = &mentionPy{kind: mentionType, name: "type"}
	mentionStrDocObject   = &mentionPy{kind: mentionStr, name: mentionStrDoc}
)

func mentionStrObject(text string) *mentionPy { return &mentionPy{kind: mentionStr, name: text} }

func mentionTypeObject(name string) *mentionPy { return &mentionPy{kind: mentionType, name: name} }

// mentionTypeName is type(obj).__name__, the word between "of" and "object"
// in a method's repr.
func mentionTypeName(v *mentionPy) string {
	switch v.kind {
	case mentionStr:
		return "str"
	case mentionStrType, mentionType:
		return "type"
	case mentionBuiltin:
		return "builtin_function_or_method"
	case mentionWrapper:
		return "method-wrapper"
	case mentionMethod:
		return "method_descriptor"
	case mentionSlot:
		return "wrapper_descriptor"
	case mentionGetSet:
		return "getset_descriptor"
	}
	return ""
}

// mentionTypeOf is type(obj).
func mentionTypeOf(v *mentionPy) *mentionPy {
	switch v.kind {
	case mentionStr:
		return mentionStrTypeObject
	case mentionStrType, mentionType:
		return mentionTypeTypeObject
	}
	return mentionTypeObject(mentionTypeName(v))
}

// mentionText is str(obj); nil is None, whose text get_nested_attr answers as "".
func mentionText(v *mentionPy) string {
	if v == nil {
		return ""
	}
	switch v.kind {
	case mentionStr:
		return v.name
	case mentionStrType, mentionType:
		return "<class '" + v.name + "'>"
	case mentionBuiltin:
		return "<built-in method " + v.name + " of " + mentionTypeName(v.recv) + " object at " + pystr.ObjectAddress + ">"
	case mentionWrapper:
		return "<method-wrapper '" + v.name + "' of " + mentionTypeName(v.recv) + " object at " + pystr.ObjectAddress + ">"
	case mentionMethod:
		return "<method '" + v.name + "' of '" + v.spec.owner + "' objects>"
	case mentionSlot:
		return "<slot wrapper '" + v.name + "' of '" + v.spec.owner + "' objects>"
	case mentionGetSet:
		return "<attribute '" + v.name + "' of '" + v.spec.owner + "' objects>"
	}
	return ""
}

// mentionQualname is obj.__qualname__: a built-in method's is its m_self's
// qualname (a type's own name) and a wrapper's or descriptor's is fixed by
// the type that defines it.
func mentionQualname(v *mentionPy) string {
	if v.kind == mentionBuiltin {
		if owner := v.recv; owner.kind == mentionStrType || owner.kind == mentionType {
			return owner.name + "." + v.name
		}
		return mentionTypeName(v.recv) + "." + v.name
	}
	return v.spec.owner + "." + v.name
}

// mentionBound is getattr(obj, name) for a spec of obj's attribute table.
func mentionBound(obj *mentionPy, name string, spec mentionSpec) *mentionPy {
	switch spec.kind {
	case mentionBuiltin:
		method := &mentionPy{kind: mentionBuiltin, name: name, spec: spec}
		switch spec.bind {
		case mentionBindValue, mentionBindStatic:
			method.recv = obj
		case mentionBindType, mentionBindStaticType:
			method.recv = mentionTypeOf(obj)
		case mentionBindMeta:
			method.recv = mentionTypeTypeObject
		}
		return method
	case mentionWrapper:
		return &mentionPy{kind: mentionWrapper, name: name, recv: obj, spec: spec}
	}
	return &mentionPy{kind: spec.kind, name: name, spec: spec}
}

// mentionTypeMetadata is the part of a type object's namespace that `type`
// provides for every type.
func mentionTypeMetadata(obj *mentionPy, name string) (*mentionPy, bool) {
	switch name {
	case "__name__", "__qualname__":
		return mentionStrObject(obj.name), true
	case "__module__":
		return mentionStrObject("builtins"), true
	case "__class__":
		return mentionTypeTypeObject, true
	case "__doc__":
		if doc, ok := mentionTypeDocs[obj.name]; ok {
			return mentionStrObject(doc), true
		}
		// A C type's __doc__ is a getset descriptor, not a str.
		return &mentionPy{kind: mentionGetSet, name: "__doc__", spec: mentionSpec{owner: obj.name}}, true
	case "__text_signature__":
		return nil, true
	case "__bases__":
		if obj.name == "object" {
			return mentionStrObject("()"), true
		}
		return mentionStrObject("(<class 'object'>,)"), true
	case "__base__":
		if obj.name == "object" {
			return nil, true
		}
		return mentionTypeObject("object"), true
	case "__mro__":
		if obj.name == "object" {
			return mentionStrObject("(<class 'object'>,)"), true
		}
		return mentionStrObject("(<class '" + obj.name + "'>, <class 'object'>)"), true
	case "__type_params__":
		return mentionStrObject("()"), true
	case "__abstractmethods__", "__annotations__":
		// type's getset raises AttributeError here: getattr's None.
		return nil, true
	}
	return nil, false
}

// mentionObjectMetadata is the part of a method or descriptor's namespace
// that CPython provides for every one of them.
func mentionObjectMetadata(obj *mentionPy, name string) (*mentionPy, bool) {
	switch name {
	case "__name__":
		return mentionStrObject(obj.name), true
	case "__qualname__":
		return mentionStrObject(mentionQualname(obj)), true
	case "__doc__":
		if obj.spec.doc == "" {
			return nil, true
		}
		return mentionStrObject(obj.spec.doc), true
	case "__text_signature__":
		if obj.spec.sig == "" {
			return nil, true
		}
		return mentionStrObject(obj.spec.sig), true
	case "__module__":
		// A built-in method's m_module is NULL and a descriptor has no
		// __module__: getattr answers None.
		return nil, true
	case "__self__":
		if obj.kind == mentionBuiltin {
			if obj.spec.bind == mentionBindStatic || obj.spec.bind == mentionBindStaticType {
				return nil, true
			}
			return obj.recv, true
		}
		if obj.kind == mentionWrapper {
			return obj.recv, true
		}
		return nil, true
	case "__objclass__":
		switch obj.kind {
		case mentionWrapper, mentionMethod, mentionSlot:
			return mentionTypeObject(obj.spec.owner), true
		}
		return nil, true
	case "__class__":
		return mentionTypeOf(obj), true
	}
	return nil, false
}

// mentionAttr is getattr(obj, name, None). A nil answer is None; the error is
// a path outside the model.
func mentionAttr(obj *mentionPy, name string) (*mentionPy, error) {
	switch obj.kind {
	case mentionStr:
		switch name {
		case "__class__":
			return mentionStrTypeObject, nil
		case "__doc__":
			return mentionStrDocObject, nil
		}
		if spec, ok := mentionStrInstanceAttrs[name]; ok {
			return mentionBound(obj, name, spec), nil
		}
	case mentionStrType:
		if value, ok := mentionTypeMetadata(obj, name); ok {
			return value, nil
		}
		if spec, ok := mentionStrTypeAttrs[name]; ok {
			return mentionBound(obj, name, spec), nil
		}
		if _, ok := mentionTypeMemory[name]; ok {
			return nil, errors.New("attribute of a type object that depends on its memory layout")
		}
	case mentionType:
		if value, ok := mentionTypeMetadata(obj, name); ok {
			return value, nil
		}
		if _, ok := mentionTypeAttributes[name]; ok {
			return nil, errors.New("method of a type object")
		}
	case mentionBuiltin, mentionWrapper, mentionMethod, mentionSlot:
		if value, ok := mentionObjectMetadata(obj, name); ok {
			return value, nil
		}
		_, dunder := mentionObjectMethods[name]
		if name == "__get__" && (obj.kind == mentionMethod || obj.kind == mentionSlot) {
			dunder = true
		}
		if dunder {
			return nil, errors.New("dunder method of a method object")
		}
	case mentionGetSet:
		switch name {
		case "__name__":
			return mentionStrObject(obj.name), nil
		case "__qualname__":
			return mentionStrObject(obj.spec.owner + "." + obj.name), nil
		case "__objclass__":
			return mentionTypeObject(obj.spec.owner), nil
		case "__class__":
			return mentionTypeObject("getset_descriptor"), nil
		case "__doc__", "__text_signature__":
			return nil, nil
		}
		_, dunder := mentionObjectMethods[name]
		if name == "__get__" || name == "__set__" || name == "__delete__" {
			dunder = true
		}
		if dunder {
			return nil, errors.New("dunder method of a getset descriptor")
		}
	}
	return nil, nil
}

// otherResolver is the 'other' dict: dict.get(key) of its entries for the
// first step, getattr(obj, name, None) for every following one, and str() of
// the last object ("" for None and for a step after None). A missing key is
// None, as dict.get answers.
func otherResolver(values map[string]string) mentions.Resolver {
	return func(path string) (string, error) {
		name, rest, nested := strings.Cut(path, ".")
		var value *mentionPy
		if text, present := values[name]; present {
			value = mentionStrObject(text)
		}
		if !nested {
			return mentionText(value), nil
		}
		if value == nil {
			// get_nested_attr: a step after None answers "".
			return "", nil
		}
		for _, attr := range strings.Split(rest, ".") {
			next, err := mentionAttr(value, attr)
			if err != nil {
				// the str type's layout fields and a method object's own
				// dunders are outside the modelled str object graph: X20, the
				// empty text.
				return "", nil
			}
			if next == nil {
				return "", nil
			}
			value = next
		}
		return mentionText(value), nil
	}
}

// mentionStrInstanceAttrs is dir(str): the attributes getattr answers for
// any str value (its __class__ and __doc__ are handled in mentionAttr).
var mentionStrInstanceAttrs = map[string]mentionSpec{
	"__add__":           {kind: mentionWrapper, owner: "str", doc: "Return self+value.", sig: "($self, value, /)"},
	"__contains__":      {kind: mentionWrapper, owner: "str", doc: "Return bool(key in self).", sig: "($self, key, /)"},
	"__delattr__":       {kind: mentionWrapper, owner: "object", doc: "Implement delattr(self, name).", sig: "($self, name, /)"},
	"__dir__":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Default dir() implementation.", sig: "($self, /)"},
	"__eq__":            {kind: mentionWrapper, owner: "str", doc: "Return self==value.", sig: "($self, value, /)"},
	"__format__":        {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a formatted version of the string as described by format_spec.", sig: "($self, format_spec, /)"},
	"__ge__":            {kind: mentionWrapper, owner: "str", doc: "Return self>=value.", sig: "($self, value, /)"},
	"__getattribute__":  {kind: mentionWrapper, owner: "object", doc: "Return getattr(self, name).", sig: "($self, name, /)"},
	"__getitem__":       {kind: mentionWrapper, owner: "str", doc: "Return self[key].", sig: "($self, key, /)"},
	"__getnewargs__":    {kind: mentionBuiltin, bind: mentionBindValue, sig: "($self, /)"},
	"__getstate__":      {kind: mentionBuiltin, bind: mentionBindValue, doc: "Helper for pickle.", sig: "($self, /)"},
	"__gt__":            {kind: mentionWrapper, owner: "str", doc: "Return self>value.", sig: "($self, value, /)"},
	"__hash__":          {kind: mentionWrapper, owner: "str", doc: "Return hash(self).", sig: "($self, /)"},
	"__init__":          {kind: mentionWrapper, owner: "object", doc: "Initialize self.  See help(type(self)) for accurate signature.", sig: "($self, /, *args, **kwargs)"},
	"__init_subclass__": {kind: mentionBuiltin, bind: mentionBindType, doc: "This method is called when a class is subclassed.\n\nThe default implementation does nothing. It may be\noverridden to extend subclasses.\n", sig: "($type, /)"},
	"__iter__":          {kind: mentionWrapper, owner: "str", doc: "Implement iter(self).", sig: "($self, /)"},
	"__le__":            {kind: mentionWrapper, owner: "str", doc: "Return self<=value.", sig: "($self, value, /)"},
	"__len__":           {kind: mentionWrapper, owner: "str", doc: "Return len(self).", sig: "($self, /)"},
	"__lt__":            {kind: mentionWrapper, owner: "str", doc: "Return self<value.", sig: "($self, value, /)"},
	"__mod__":           {kind: mentionWrapper, owner: "str", doc: "Return self%value.", sig: "($self, value, /)"},
	"__mul__":           {kind: mentionWrapper, owner: "str", doc: "Return self*value.", sig: "($self, value, /)"},
	"__ne__":            {kind: mentionWrapper, owner: "str", doc: "Return self!=value.", sig: "($self, value, /)"},
	"__new__":           {kind: mentionBuiltin, bind: mentionBindType, doc: "Create and return a new object.  See help(type) for accurate signature.", sig: "($type, *args, **kwargs)"},
	"__reduce__":        {kind: mentionBuiltin, bind: mentionBindValue, doc: "Helper for pickle.", sig: "($self, /)"},
	"__reduce_ex__":     {kind: mentionBuiltin, bind: mentionBindValue, doc: "Helper for pickle.", sig: "($self, protocol, /)"},
	"__repr__":          {kind: mentionWrapper, owner: "str", doc: "Return repr(self).", sig: "($self, /)"},
	"__rmod__":          {kind: mentionWrapper, owner: "str", doc: "Return value%self.", sig: "($self, value, /)"},
	"__rmul__":          {kind: mentionWrapper, owner: "str", doc: "Return value*self.", sig: "($self, value, /)"},
	"__setattr__":       {kind: mentionWrapper, owner: "object", doc: "Implement setattr(self, name, value).", sig: "($self, name, value, /)"},
	"__sizeof__":        {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return the size of the string in memory, in bytes.", sig: "($self, /)"},
	"__str__":           {kind: mentionWrapper, owner: "str", doc: "Return str(self).", sig: "($self, /)"},
	"__subclasshook__":  {kind: mentionBuiltin, bind: mentionBindType, doc: "Abstract classes can override this to customize issubclass().\n\nThis is invoked early on by abc.ABCMeta.__subclasscheck__().\nIt should return True, False or NotImplemented.  If it returns\nNotImplemented, the normal algorithm is used.  Otherwise, it\noverrides the normal algorithm (and the outcome is cached).\n", sig: "($type, object, /)"},
	"capitalize":        {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a capitalized version of the string.\n\nMore specifically, make the first character have upper case and the\nrest lower case.", sig: "($self, /)"},
	"casefold":          {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a version of the string suitable for caseless comparisons.", sig: "($self, /)"},
	"center":            {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a centered string of length width.\n\nPadding is done using the specified fill character (default is\na space).", sig: "($self, width, fillchar=' ', /)"},
	"count":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return the number of non-overlapping occurrences of substring sub in string S[start:end].\n\nOptional arguments start and end are interpreted as in slice\nnotation.", sig: "($self, sub[, start[, end]], /)"},
	"encode":            {kind: mentionBuiltin, bind: mentionBindValue, doc: "Encode the string using the codec registered for encoding.\n\n  encoding\n    The encoding in which to encode the string.\n  errors\n    The error handling scheme to use for encoding errors.\n    The default is 'strict' meaning that encoding errors raise a\n    UnicodeEncodeError.  Other possible values are 'ignore', 'replace'\n    and 'xmlcharrefreplace' as well as any other name registered with\n    codecs.register_error that can handle UnicodeEncodeErrors.", sig: "($self, /, encoding='utf-8', errors='strict')"},
	"endswith":          {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string ends with the specified suffix, False otherwise.\n\n  suffix\n    A string or a tuple of strings to try.\n  start\n    Optional start position. Default: start of the string.\n  end\n    Optional stop position. Default: end of the string.", sig: "($self, suffix[, start[, end]], /)"},
	"expandtabs":        {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a copy where all tab characters are expanded using spaces.\n\nIf tabsize is not given, a tab size of 8 characters is assumed.", sig: "($self, /, tabsize=8)"},
	"find":              {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return the lowest index in S where substring sub is found, such that sub is contained within S[start:end].\n\nOptional arguments start and end are interpreted as in slice\nnotation.  Return -1 on failure.", sig: "($self, sub[, start[, end]], /)"},
	"format":            {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a formatted version of the string, using substitutions from args and kwargs.\nThe substitutions are identified by braces ('{' and '}').", sig: "($self, /, *args, **kwargs)"},
	"format_map":        {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a formatted version of the string, using substitutions from mapping.\nThe substitutions are identified by braces ('{' and '}').", sig: "($self, mapping, /)"},
	"index":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return the lowest index in S where substring sub is found, such that sub is contained within S[start:end].\n\nOptional arguments start and end are interpreted as in slice\nnotation.  Raises ValueError when the substring is not found.", sig: "($self, sub[, start[, end]], /)"},
	"isalnum":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string is an alpha-numeric string, False otherwise.\n\nA string is alpha-numeric if all characters in the string are\nalpha-numeric and there is at least one character in the string.", sig: "($self, /)"},
	"isalpha":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string is an alphabetic string, False otherwise.\n\nA string is alphabetic if all characters in the string are\nalphabetic and there is at least one character in the string.", sig: "($self, /)"},
	"isascii":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if all characters in the string are ASCII, False otherwise.\n\nASCII characters have code points in the range U+0000-U+007F.\nEmpty string is ASCII too.", sig: "($self, /)"},
	"isdecimal":         {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string is a decimal string, False otherwise.\n\nA string is a decimal string if all characters in the string are\ndecimal and there is at least one character in the string.", sig: "($self, /)"},
	"isdigit":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string is a digit string, False otherwise.\n\nA string is a digit string if all characters in the string are\ndigits and there is at least one character in the string.", sig: "($self, /)"},
	"isidentifier":      {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string is a valid Python identifier, False otherwise.\n\nCall keyword.iskeyword(s) to test whether string s is a reserved\nidentifier, such as \"def\" or \"class\".", sig: "($self, /)"},
	"islower":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string is a lowercase string, False otherwise.\n\nA string is lowercase if all cased characters in the string are\nlowercase and there is at least one cased character in the string.", sig: "($self, /)"},
	"isnumeric":         {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string is a numeric string, False otherwise.\n\nA string is numeric if all characters in the string are numeric and\nthere is at least one character in the string.", sig: "($self, /)"},
	"isprintable":       {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if all characters in the string are printable, False otherwise.\n\nA character is printable if repr() may use it in its output.", sig: "($self, /)"},
	"isspace":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string is a whitespace string, False otherwise.\n\nA string is whitespace if all characters in the string are\nwhitespace and there is at least one character in the string.", sig: "($self, /)"},
	"istitle":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string is a title-cased string, False otherwise.\n\nIn a title-cased string, upper- and title-case characters may only\nfollow uncased characters and lowercase characters only cased ones.", sig: "($self, /)"},
	"isupper":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string is an uppercase string, False otherwise.\n\nA string is uppercase if all cased characters in the string are\nuppercase and there is at least one cased character in the string.", sig: "($self, /)"},
	"join":              {kind: mentionBuiltin, bind: mentionBindValue, doc: "Concatenate any number of strings.\n\nThe string whose method is called is inserted in between each given\nstring.  The result is returned as a new string.\n\nExample: '.'.join(['ab', 'pq', 'rs']) -> 'ab.pq.rs'", sig: "($self, iterable, /)"},
	"ljust":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a left-justified string of length width.\n\nPadding is done using the specified fill character (default is\na space).", sig: "($self, width, fillchar=' ', /)"},
	"lower":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a copy of the string converted to lowercase.", sig: "($self, /)"},
	"lstrip":            {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a copy of the string with leading whitespace removed.\n\nIf chars is given and not None, remove characters in chars instead.", sig: "($self, chars=None, /)"},
	"maketrans":         {kind: mentionBuiltin, bind: mentionBindStaticType, doc: "Return a translation table usable for str.translate().\n\nIf there is only one argument, it must be a dictionary mapping\nUnicode ordinals (integers) or characters to Unicode ordinals,\nstrings or None.  Character keys will be then converted to ordinals.\nIf there are two arguments, they must be strings of equal length,\nand in the resulting dictionary, each character in x will be mapped\nto the character at the same position in y.  If there is a third\nargument, it must be a string, whose characters will be mapped to\nNone in the result.", sig: "(x, y=<unrepresentable>, z=<unrepresentable>, /)"},
	"partition":         {kind: mentionBuiltin, bind: mentionBindValue, doc: "Partition the string into three parts using the given separator.\n\nThis will search for the separator in the string.  If the separator\nis found, returns a 3-tuple containing the part before the\nseparator, the separator itself, and the part after it.\n\nIf the separator is not found, returns a 3-tuple containing\nthe original string and two empty strings.", sig: "($self, sep, /)"},
	"removeprefix":      {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a str with the given prefix string removed if present.\n\nIf the string starts with the prefix string, return\nstring[len(prefix):].  Otherwise, return a copy of the original\nstring.", sig: "($self, prefix, /)"},
	"removesuffix":      {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a str with the given suffix string removed if present.\n\nIf the string ends with the suffix string and that suffix is not\nempty, return string[:-len(suffix)].  Otherwise, return a copy of\nthe original string.", sig: "($self, suffix, /)"},
	"replace":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a copy with all occurrences of substring old replaced by new.\n\n  count\n    Maximum number of occurrences to replace.\n    -1 (the default value) means replace all occurrences.\n\nIf the optional argument count is given, only the first count occurrences are\nreplaced.", sig: "($self, old, new, /, count=-1)"},
	"rfind":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return the highest index in S where substring sub is found, such that sub is contained within S[start:end].\n\nOptional arguments start and end are interpreted as in slice\nnotation.  Return -1 on failure.", sig: "($self, sub[, start[, end]], /)"},
	"rindex":            {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return the highest index in S where substring sub is found, such that sub is contained within S[start:end].\n\nOptional arguments start and end are interpreted as in slice\nnotation.  Raises ValueError when the substring is not found.", sig: "($self, sub[, start[, end]], /)"},
	"rjust":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a right-justified string of length width.\n\nPadding is done using the specified fill character (default is\na space).", sig: "($self, width, fillchar=' ', /)"},
	"rpartition":        {kind: mentionBuiltin, bind: mentionBindValue, doc: "Partition the string into three parts using the given separator.\n\nThis will search for the separator in the string, starting at the\nend.  If the separator is found, returns a 3-tuple containing the\npart before the separator, the separator itself, and the part after\nit.\n\nIf the separator is not found, returns a 3-tuple containing two\nempty strings and the original string.", sig: "($self, sep, /)"},
	"rsplit":            {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a list of the substrings in the string, using sep as the separator string.\n\n  sep\n    The separator used to split the string.\n\n    When set to None (the default value), will split on any\n    whitespace character (including \\n \\r \\t \\f and spaces) and\n    will discard empty strings from the result.\n  maxsplit\n    Maximum number of splits.\n    -1 (the default value) means no limit.\n\nSplitting starts at the end of the string and works to the front.", sig: "($self, /, sep=None, maxsplit=-1)"},
	"rstrip":            {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a copy of the string with trailing whitespace removed.\n\nIf chars is given and not None, remove characters in chars instead.", sig: "($self, chars=None, /)"},
	"split":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a list of the substrings in the string, using sep as the separator string.\n\n  sep\n    The separator used to split the string.\n\n    When set to None (the default value), will split on any\n    whitespace character (including \\n \\r \\t \\f and spaces) and\n    will discard empty strings from the result.\n  maxsplit\n    Maximum number of splits.\n    -1 (the default value) means no limit.\n\nSplitting starts at the front of the string and works to the end.\n\nNote, str.split() is mainly useful for data that has been\nintentionally delimited.  With natural text that includes\npunctuation, consider using the regular expression module.", sig: "($self, /, sep=None, maxsplit=-1)"},
	"splitlines":        {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a list of the lines in the string, breaking at line boundaries.\n\nLine breaks are not included in the resulting list unless keepends\nis given and true.", sig: "($self, /, keepends=False)"},
	"startswith":        {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return True if the string starts with the specified prefix, False otherwise.\n\n  prefix\n    A string or a tuple of strings to try.\n  start\n    Optional start position. Default: start of the string.\n  end\n    Optional stop position. Default: end of the string.", sig: "($self, prefix[, start[, end]], /)"},
	"strip":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a copy of the string with leading and trailing whitespace removed.\n\nIf chars is given and not None, remove characters in chars instead.", sig: "($self, chars=None, /)"},
	"swapcase":          {kind: mentionBuiltin, bind: mentionBindValue, doc: "Convert uppercase characters to lowercase and lowercase characters to uppercase.", sig: "($self, /)"},
	"title":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a version of the string where each word is titlecased.\n\nMore specifically, words start with uppercased characters and all\nremaining cased characters have lower case.", sig: "($self, /)"},
	"translate":         {kind: mentionBuiltin, bind: mentionBindValue, doc: "Replace each character in the string using the given translation table.\n\n  table\n    Translation table, which must be a mapping of Unicode ordinals\n    to Unicode ordinals, strings, or None.\n\nThe table must implement lookup/indexing via __getitem__, for\ninstance a dictionary or list.  If this operation raises\nLookupError, the character is left untouched.  Characters mapped to\nNone are deleted.", sig: "($self, table, /)"},
	"upper":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a copy of the string converted to uppercase.", sig: "($self, /)"},
	"zfill":             {kind: mentionBuiltin, bind: mentionBindValue, doc: "Pad a numeric string with zeros on the left, to fill a field of the given width.\n\nThe string is never truncated.", sig: "($self, width, /)"},
}

// mentionStrTypeAttrs is the str type's namespace: dir(str) plus the attributes
// a type object also answers from `type` (__call__, mro and the checks).
var mentionStrTypeAttrs = map[string]mentionSpec{
	"__add__":           {kind: mentionSlot, owner: "str", doc: "Return self+value.", sig: "($self, value, /)"},
	"__call__":          {kind: mentionWrapper, owner: "type", doc: "Call self as a function.", sig: "($self, /, *args, **kwargs)"},
	"__contains__":      {kind: mentionSlot, owner: "str", doc: "Return bool(key in self).", sig: "($self, key, /)"},
	"__delattr__":       {kind: mentionSlot, owner: "object", doc: "Implement delattr(self, name).", sig: "($self, name, /)"},
	"__dir__":           {kind: mentionMethod, owner: "object", doc: "Default dir() implementation.", sig: "($self, /)"},
	"__eq__":            {kind: mentionSlot, owner: "str", doc: "Return self==value.", sig: "($self, value, /)"},
	"__format__":        {kind: mentionMethod, owner: "str", doc: "Return a formatted version of the string as described by format_spec.", sig: "($self, format_spec, /)"},
	"__ge__":            {kind: mentionSlot, owner: "str", doc: "Return self>=value.", sig: "($self, value, /)"},
	"__getattribute__":  {kind: mentionSlot, owner: "object", doc: "Return getattr(self, name).", sig: "($self, name, /)"},
	"__getitem__":       {kind: mentionSlot, owner: "str", doc: "Return self[key].", sig: "($self, key, /)"},
	"__getnewargs__":    {kind: mentionMethod, owner: "str", sig: "($self, /)"},
	"__getstate__":      {kind: mentionMethod, owner: "object", doc: "Helper for pickle.", sig: "($self, /)"},
	"__gt__":            {kind: mentionSlot, owner: "str", doc: "Return self>value.", sig: "($self, value, /)"},
	"__hash__":          {kind: mentionSlot, owner: "str", doc: "Return hash(self).", sig: "($self, /)"},
	"__init__":          {kind: mentionSlot, owner: "object", doc: "Initialize self.  See help(type(self)) for accurate signature.", sig: "($self, /, *args, **kwargs)"},
	"__init_subclass__": {kind: mentionBuiltin, bind: mentionBindValue, doc: "This method is called when a class is subclassed.\n\nThe default implementation does nothing. It may be\noverridden to extend subclasses.\n", sig: "($type, /)"},
	"__instancecheck__": {kind: mentionBuiltin, bind: mentionBindValue, doc: "Check if an object is an instance.", sig: "($self, instance, /)"},
	"__iter__":          {kind: mentionSlot, owner: "str", doc: "Implement iter(self).", sig: "($self, /)"},
	"__le__":            {kind: mentionSlot, owner: "str", doc: "Return self<=value.", sig: "($self, value, /)"},
	"__len__":           {kind: mentionSlot, owner: "str", doc: "Return len(self).", sig: "($self, /)"},
	"__lt__":            {kind: mentionSlot, owner: "str", doc: "Return self<value.", sig: "($self, value, /)"},
	"__mod__":           {kind: mentionSlot, owner: "str", doc: "Return self%value.", sig: "($self, value, /)"},
	"__mul__":           {kind: mentionSlot, owner: "str", doc: "Return self*value.", sig: "($self, value, /)"},
	"__ne__":            {kind: mentionSlot, owner: "str", doc: "Return self!=value.", sig: "($self, value, /)"},
	"__new__":           {kind: mentionBuiltin, bind: mentionBindValue, doc: "Create and return a new object.  See help(type) for accurate signature.", sig: "($type, *args, **kwargs)"},
	"__or__":            {kind: mentionWrapper, owner: "type", doc: "Return self|value.", sig: "($self, value, /)"},
	"__prepare__":       {kind: mentionBuiltin, bind: mentionBindMeta, doc: "Create the namespace for the class statement", sig: "($cls, name, bases, /, **kwds)"},
	"__reduce__":        {kind: mentionMethod, owner: "object", doc: "Helper for pickle.", sig: "($self, /)"},
	"__reduce_ex__":     {kind: mentionMethod, owner: "object", doc: "Helper for pickle.", sig: "($self, protocol, /)"},
	"__repr__":          {kind: mentionSlot, owner: "str", doc: "Return repr(self).", sig: "($self, /)"},
	"__rmod__":          {kind: mentionSlot, owner: "str", doc: "Return value%self.", sig: "($self, value, /)"},
	"__rmul__":          {kind: mentionSlot, owner: "str", doc: "Return value*self.", sig: "($self, value, /)"},
	"__ror__":           {kind: mentionWrapper, owner: "type", doc: "Return value|self.", sig: "($self, value, /)"},
	"__setattr__":       {kind: mentionSlot, owner: "object", doc: "Implement setattr(self, name, value).", sig: "($self, name, value, /)"},
	"__sizeof__":        {kind: mentionMethod, owner: "str", doc: "Return the size of the string in memory, in bytes.", sig: "($self, /)"},
	"__str__":           {kind: mentionSlot, owner: "str", doc: "Return str(self).", sig: "($self, /)"},
	"__subclasscheck__": {kind: mentionBuiltin, bind: mentionBindValue, doc: "Check if a class is a subclass.", sig: "($self, subclass, /)"},
	"__subclasses__":    {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a list of immediate subclasses.", sig: "($self, /)"},
	"__subclasshook__":  {kind: mentionBuiltin, bind: mentionBindValue, doc: "Abstract classes can override this to customize issubclass().\n\nThis is invoked early on by abc.ABCMeta.__subclasscheck__().\nIt should return True, False or NotImplemented.  If it returns\nNotImplemented, the normal algorithm is used.  Otherwise, it\noverrides the normal algorithm (and the outcome is cached).\n", sig: "($type, object, /)"},
	"capitalize":        {kind: mentionMethod, owner: "str", doc: "Return a capitalized version of the string.\n\nMore specifically, make the first character have upper case and the\nrest lower case.", sig: "($self, /)"},
	"casefold":          {kind: mentionMethod, owner: "str", doc: "Return a version of the string suitable for caseless comparisons.", sig: "($self, /)"},
	"center":            {kind: mentionMethod, owner: "str", doc: "Return a centered string of length width.\n\nPadding is done using the specified fill character (default is\na space).", sig: "($self, width, fillchar=' ', /)"},
	"count":             {kind: mentionMethod, owner: "str", doc: "Return the number of non-overlapping occurrences of substring sub in string S[start:end].\n\nOptional arguments start and end are interpreted as in slice\nnotation.", sig: "($self, sub[, start[, end]], /)"},
	"encode":            {kind: mentionMethod, owner: "str", doc: "Encode the string using the codec registered for encoding.\n\n  encoding\n    The encoding in which to encode the string.\n  errors\n    The error handling scheme to use for encoding errors.\n    The default is 'strict' meaning that encoding errors raise a\n    UnicodeEncodeError.  Other possible values are 'ignore', 'replace'\n    and 'xmlcharrefreplace' as well as any other name registered with\n    codecs.register_error that can handle UnicodeEncodeErrors.", sig: "($self, /, encoding='utf-8', errors='strict')"},
	"endswith":          {kind: mentionMethod, owner: "str", doc: "Return True if the string ends with the specified suffix, False otherwise.\n\n  suffix\n    A string or a tuple of strings to try.\n  start\n    Optional start position. Default: start of the string.\n  end\n    Optional stop position. Default: end of the string.", sig: "($self, suffix[, start[, end]], /)"},
	"expandtabs":        {kind: mentionMethod, owner: "str", doc: "Return a copy where all tab characters are expanded using spaces.\n\nIf tabsize is not given, a tab size of 8 characters is assumed.", sig: "($self, /, tabsize=8)"},
	"find":              {kind: mentionMethod, owner: "str", doc: "Return the lowest index in S where substring sub is found, such that sub is contained within S[start:end].\n\nOptional arguments start and end are interpreted as in slice\nnotation.  Return -1 on failure.", sig: "($self, sub[, start[, end]], /)"},
	"format":            {kind: mentionMethod, owner: "str", doc: "Return a formatted version of the string, using substitutions from args and kwargs.\nThe substitutions are identified by braces ('{' and '}').", sig: "($self, /, *args, **kwargs)"},
	"format_map":        {kind: mentionMethod, owner: "str", doc: "Return a formatted version of the string, using substitutions from mapping.\nThe substitutions are identified by braces ('{' and '}').", sig: "($self, mapping, /)"},
	"index":             {kind: mentionMethod, owner: "str", doc: "Return the lowest index in S where substring sub is found, such that sub is contained within S[start:end].\n\nOptional arguments start and end are interpreted as in slice\nnotation.  Raises ValueError when the substring is not found.", sig: "($self, sub[, start[, end]], /)"},
	"isalnum":           {kind: mentionMethod, owner: "str", doc: "Return True if the string is an alpha-numeric string, False otherwise.\n\nA string is alpha-numeric if all characters in the string are\nalpha-numeric and there is at least one character in the string.", sig: "($self, /)"},
	"isalpha":           {kind: mentionMethod, owner: "str", doc: "Return True if the string is an alphabetic string, False otherwise.\n\nA string is alphabetic if all characters in the string are\nalphabetic and there is at least one character in the string.", sig: "($self, /)"},
	"isascii":           {kind: mentionMethod, owner: "str", doc: "Return True if all characters in the string are ASCII, False otherwise.\n\nASCII characters have code points in the range U+0000-U+007F.\nEmpty string is ASCII too.", sig: "($self, /)"},
	"isdecimal":         {kind: mentionMethod, owner: "str", doc: "Return True if the string is a decimal string, False otherwise.\n\nA string is a decimal string if all characters in the string are\ndecimal and there is at least one character in the string.", sig: "($self, /)"},
	"isdigit":           {kind: mentionMethod, owner: "str", doc: "Return True if the string is a digit string, False otherwise.\n\nA string is a digit string if all characters in the string are\ndigits and there is at least one character in the string.", sig: "($self, /)"},
	"isidentifier":      {kind: mentionMethod, owner: "str", doc: "Return True if the string is a valid Python identifier, False otherwise.\n\nCall keyword.iskeyword(s) to test whether string s is a reserved\nidentifier, such as \"def\" or \"class\".", sig: "($self, /)"},
	"islower":           {kind: mentionMethod, owner: "str", doc: "Return True if the string is a lowercase string, False otherwise.\n\nA string is lowercase if all cased characters in the string are\nlowercase and there is at least one cased character in the string.", sig: "($self, /)"},
	"isnumeric":         {kind: mentionMethod, owner: "str", doc: "Return True if the string is a numeric string, False otherwise.\n\nA string is numeric if all characters in the string are numeric and\nthere is at least one character in the string.", sig: "($self, /)"},
	"isprintable":       {kind: mentionMethod, owner: "str", doc: "Return True if all characters in the string are printable, False otherwise.\n\nA character is printable if repr() may use it in its output.", sig: "($self, /)"},
	"isspace":           {kind: mentionMethod, owner: "str", doc: "Return True if the string is a whitespace string, False otherwise.\n\nA string is whitespace if all characters in the string are\nwhitespace and there is at least one character in the string.", sig: "($self, /)"},
	"istitle":           {kind: mentionMethod, owner: "str", doc: "Return True if the string is a title-cased string, False otherwise.\n\nIn a title-cased string, upper- and title-case characters may only\nfollow uncased characters and lowercase characters only cased ones.", sig: "($self, /)"},
	"isupper":           {kind: mentionMethod, owner: "str", doc: "Return True if the string is an uppercase string, False otherwise.\n\nA string is uppercase if all cased characters in the string are\nuppercase and there is at least one cased character in the string.", sig: "($self, /)"},
	"join":              {kind: mentionMethod, owner: "str", doc: "Concatenate any number of strings.\n\nThe string whose method is called is inserted in between each given\nstring.  The result is returned as a new string.\n\nExample: '.'.join(['ab', 'pq', 'rs']) -> 'ab.pq.rs'", sig: "($self, iterable, /)"},
	"ljust":             {kind: mentionMethod, owner: "str", doc: "Return a left-justified string of length width.\n\nPadding is done using the specified fill character (default is\na space).", sig: "($self, width, fillchar=' ', /)"},
	"lower":             {kind: mentionMethod, owner: "str", doc: "Return a copy of the string converted to lowercase.", sig: "($self, /)"},
	"lstrip":            {kind: mentionMethod, owner: "str", doc: "Return a copy of the string with leading whitespace removed.\n\nIf chars is given and not None, remove characters in chars instead.", sig: "($self, chars=None, /)"},
	"maketrans":         {kind: mentionBuiltin, bind: mentionBindStatic, doc: "Return a translation table usable for str.translate().\n\nIf there is only one argument, it must be a dictionary mapping\nUnicode ordinals (integers) or characters to Unicode ordinals,\nstrings or None.  Character keys will be then converted to ordinals.\nIf there are two arguments, they must be strings of equal length,\nand in the resulting dictionary, each character in x will be mapped\nto the character at the same position in y.  If there is a third\nargument, it must be a string, whose characters will be mapped to\nNone in the result.", sig: "(x, y=<unrepresentable>, z=<unrepresentable>, /)"},
	"mro":               {kind: mentionBuiltin, bind: mentionBindValue, doc: "Return a type's method resolution order.", sig: "($self, /)"},
	"partition":         {kind: mentionMethod, owner: "str", doc: "Partition the string into three parts using the given separator.\n\nThis will search for the separator in the string.  If the separator\nis found, returns a 3-tuple containing the part before the\nseparator, the separator itself, and the part after it.\n\nIf the separator is not found, returns a 3-tuple containing\nthe original string and two empty strings.", sig: "($self, sep, /)"},
	"removeprefix":      {kind: mentionMethod, owner: "str", doc: "Return a str with the given prefix string removed if present.\n\nIf the string starts with the prefix string, return\nstring[len(prefix):].  Otherwise, return a copy of the original\nstring.", sig: "($self, prefix, /)"},
	"removesuffix":      {kind: mentionMethod, owner: "str", doc: "Return a str with the given suffix string removed if present.\n\nIf the string ends with the suffix string and that suffix is not\nempty, return string[:-len(suffix)].  Otherwise, return a copy of\nthe original string.", sig: "($self, suffix, /)"},
	"replace":           {kind: mentionMethod, owner: "str", doc: "Return a copy with all occurrences of substring old replaced by new.\n\n  count\n    Maximum number of occurrences to replace.\n    -1 (the default value) means replace all occurrences.\n\nIf the optional argument count is given, only the first count occurrences are\nreplaced.", sig: "($self, old, new, /, count=-1)"},
	"rfind":             {kind: mentionMethod, owner: "str", doc: "Return the highest index in S where substring sub is found, such that sub is contained within S[start:end].\n\nOptional arguments start and end are interpreted as in slice\nnotation.  Return -1 on failure.", sig: "($self, sub[, start[, end]], /)"},
	"rindex":            {kind: mentionMethod, owner: "str", doc: "Return the highest index in S where substring sub is found, such that sub is contained within S[start:end].\n\nOptional arguments start and end are interpreted as in slice\nnotation.  Raises ValueError when the substring is not found.", sig: "($self, sub[, start[, end]], /)"},
	"rjust":             {kind: mentionMethod, owner: "str", doc: "Return a right-justified string of length width.\n\nPadding is done using the specified fill character (default is\na space).", sig: "($self, width, fillchar=' ', /)"},
	"rpartition":        {kind: mentionMethod, owner: "str", doc: "Partition the string into three parts using the given separator.\n\nThis will search for the separator in the string, starting at the\nend.  If the separator is found, returns a 3-tuple containing the\npart before the separator, the separator itself, and the part after\nit.\n\nIf the separator is not found, returns a 3-tuple containing two\nempty strings and the original string.", sig: "($self, sep, /)"},
	"rsplit":            {kind: mentionMethod, owner: "str", doc: "Return a list of the substrings in the string, using sep as the separator string.\n\n  sep\n    The separator used to split the string.\n\n    When set to None (the default value), will split on any\n    whitespace character (including \\n \\r \\t \\f and spaces) and\n    will discard empty strings from the result.\n  maxsplit\n    Maximum number of splits.\n    -1 (the default value) means no limit.\n\nSplitting starts at the end of the string and works to the front.", sig: "($self, /, sep=None, maxsplit=-1)"},
	"rstrip":            {kind: mentionMethod, owner: "str", doc: "Return a copy of the string with trailing whitespace removed.\n\nIf chars is given and not None, remove characters in chars instead.", sig: "($self, chars=None, /)"},
	"split":             {kind: mentionMethod, owner: "str", doc: "Return a list of the substrings in the string, using sep as the separator string.\n\n  sep\n    The separator used to split the string.\n\n    When set to None (the default value), will split on any\n    whitespace character (including \\n \\r \\t \\f and spaces) and\n    will discard empty strings from the result.\n  maxsplit\n    Maximum number of splits.\n    -1 (the default value) means no limit.\n\nSplitting starts at the front of the string and works to the end.\n\nNote, str.split() is mainly useful for data that has been\nintentionally delimited.  With natural text that includes\npunctuation, consider using the regular expression module.", sig: "($self, /, sep=None, maxsplit=-1)"},
	"splitlines":        {kind: mentionMethod, owner: "str", doc: "Return a list of the lines in the string, breaking at line boundaries.\n\nLine breaks are not included in the resulting list unless keepends\nis given and true.", sig: "($self, /, keepends=False)"},
	"startswith":        {kind: mentionMethod, owner: "str", doc: "Return True if the string starts with the specified prefix, False otherwise.\n\n  prefix\n    A string or a tuple of strings to try.\n  start\n    Optional start position. Default: start of the string.\n  end\n    Optional stop position. Default: end of the string.", sig: "($self, prefix[, start[, end]], /)"},
	"strip":             {kind: mentionMethod, owner: "str", doc: "Return a copy of the string with leading and trailing whitespace removed.\n\nIf chars is given and not None, remove characters in chars instead.", sig: "($self, chars=None, /)"},
	"swapcase":          {kind: mentionMethod, owner: "str", doc: "Convert uppercase characters to lowercase and lowercase characters to uppercase.", sig: "($self, /)"},
	"title":             {kind: mentionMethod, owner: "str", doc: "Return a version of the string where each word is titlecased.\n\nMore specifically, words start with uppercased characters and all\nremaining cased characters have lower case.", sig: "($self, /)"},
	"translate":         {kind: mentionMethod, owner: "str", doc: "Replace each character in the string using the given translation table.\n\n  table\n    Translation table, which must be a mapping of Unicode ordinals\n    to Unicode ordinals, strings, or None.\n\nThe table must implement lookup/indexing via __getitem__, for\ninstance a dictionary or list.  If this operation raises\nLookupError, the character is left untouched.  Characters mapped to\nNone are deleted.", sig: "($self, table, /)"},
	"upper":             {kind: mentionMethod, owner: "str", doc: "Return a copy of the string converted to uppercase.", sig: "($self, /)"},
	"zfill":             {kind: mentionMethod, owner: "str", doc: "Pad a numeric string with zeros on the left, to fill a field of the given width.\n\nThe string is never truncated.", sig: "($self, width, /)"},
}

// mentionTypeMemory is what a type object answers with an address or a
// build-dependent number: not reproduced, the request goes to Django.
var mentionTypeMemory = mentionAttributeSet("__dict__", "__flags__", "__basicsize__",
	"__itemsize__", "__weakrefoffset__", "__dictoffset__")

// mentionTypeAttributes is dir(type): the attributes of a type object. The
// port answers the metadata (mentionTypeMetadata) and refuses the rest.
var mentionTypeAttributes = mentionAttributeSet(
	"__abstractmethods__", "__annotations__", "__base__", "__bases__", "__basicsize__", "__call__", "__class__",
	"__delattr__", "__dict__", "__dictoffset__", "__dir__", "__doc__", "__eq__", "__flags__", "__format__", "__ge__",
	"__getattribute__", "__getstate__", "__gt__", "__hash__", "__init__", "__init_subclass__", "__instancecheck__",
	"__itemsize__", "__le__", "__lt__", "__module__", "__mro__", "__name__", "__ne__", "__new__", "__or__",
	"__prepare__", "__qualname__", "__reduce__", "__reduce_ex__", "__repr__", "__ror__", "__setattr__", "__sizeof__",
	"__str__", "__subclasscheck__", "__subclasses__", "__subclasshook__", "__text_signature__", "__type_params__",
	"__weakrefoffset__", "mro",
)

// mentionObjectMethods is the dunder methods of a method object or a
// descriptor: `other.today.upper.__call__` and the like.
var mentionObjectMethods = mentionAttributeSet(
	"__call__", "__delattr__", "__dir__", "__eq__", "__format__", "__ge__", "__getattribute__", "__getstate__",
	"__gt__", "__hash__", "__init__", "__init_subclass__", "__le__", "__lt__", "__ne__", "__new__", "__reduce__",
	"__reduce_ex__", "__repr__", "__setattr__", "__sizeof__", "__str__", "__subclasshook__",
)

// mentionTypeDocs is __doc__ of the type objects whose __doc__ is a str. A C
// type's __doc__ is a getset descriptor (mentionTypeMetadata).
var mentionTypeDocs = map[string]string{
	"str":    "str(object='') -> str\nstr(bytes_or_buffer[, encoding[, errors]]) -> str\n\nCreate a new string object from the given object. If encoding or\nerrors is specified, then the object must expose a data buffer\nthat will be decoded using the given encoding and error handler.\nOtherwise, returns the result of object.__str__() (if defined)\nor repr(object).\nencoding defaults to 'utf-8'.\nerrors defaults to 'strict'.",
	"type":   "type(object) -> the object's type\ntype(name, bases, dict, **kwds) -> a new type",
	"object": "The base class of the class hierarchy.\n\nWhen called, it accepts no arguments and returns a new featureless\ninstance that has no instance attributes and cannot be given any.\n",
}

// modelResolver: a model instance none of whose attributes the port reads.
func modelResolver(attributes map[string]struct{}) mentions.Resolver {
	return fieldResolver(attributes, nil)
}

func (s *DocumentViews) todayText() string { return s.today().Format("02/01/2006") }

// --- views -------------------------------------------------------------------------------------

func htmlReply(html, language string) *DocumentViewReply {
	return &DocumentViewReply{Status: 200, HTML: &html, Language: language}
}

// template is document_template_view.
func (s *DocumentViews) template(ctx context.Context, identity domain.ResolvedIdentity, request DocumentViewRequest) (*DocumentViewReply, error) {
	id, ok := parseModelUUID(request.UID)
	if !ok {
		return nil, ErrDocumentViewServer
	}
	template, err := s.store.Template(ctx, id)
	if err != nil {
		return nil, lookupError(err)
	}
	// Served to the association's staff.
	if err := memberDocumentStaffAccess(ctx, s.store, identity); err != nil {
		return nil, err
	}
	association, err := s.store.Association(ctx, template.Association)
	if err != nil {
		return nil, lookupError(err)
	}
	today := s.todayText()
	context := mentions.Context{"sport_association": associationResolver(association)}
	other := map[string]string{"today": today}
	coursesList := "[]" // the local [] until get_courses_list() succeeds
	if raw, present := request.Query.Lookup("subscription_id"); present {
		text, _ := raw.(string)
		if subscriptionID, valid := parseModelUUID(text); valid {
			subscription, err := s.store.Subscription(ctx, subscriptionID, renderScope(identity, request.Group))
			switch {
			case errors.Is(err, domain.ErrViewNotFound):
			case err != nil:
				return nil, err
			default:
				courses, err := s.store.CoursesList(ctx, subscription.ID)
				switch {
				case errors.Is(err, domain.ErrViewCourseTitle):
				case err != nil:
					return nil, err
				default:
					coursesList = courses
					if subscription.AssociateID == nil {
						// subscription.associate.get_main_tutor() raises after 'associate' is set.
						context["associate"] = associateResolver(nil)
						break
					}
					associate, err := s.store.Associate(ctx, *subscription.AssociateID)
					if err != nil {
						return nil, lookupError(err)
					}
					context["associate"] = associateResolver(associate)
					tutor, err := s.mainTutor(ctx, associate.ID)
					if err != nil {
						return nil, err
					}
					context["main_tutor"] = associateResolver(tutor)
					other["courses_list"] = courses
				}
			}
		}
	}
	context["other"] = otherResolver(other)
	footer, header := association.InvoiceFooter, association.DocumentHeader
	if template.CustomFooterHeader {
		footer, header = template.CustomFooter, template.CustomHeader
	}
	headerText, footerText := renderOptionalEmpty(header), renderOptionalEmpty(footer)
	edge := mentions.Context{"sport_association": associationResolver(association),
		"other": otherResolver(map[string]string{"today": today, "courses_list": coursesList})}
	if template.Header {
		if headerText, err = mentions.Filter(headerText, true, edge); err != nil {
			return nil, err
		}
	} else {
		headerText = ""
	}
	if template.Footer {
		if footerText, err = mentions.Filter(footerText, true, edge); err != nil {
			return nil, err
		}
	} else {
		footerText = ""
	}
	content, err := mentions.Filter(renderOptionalEmpty(template.Template), template.Template != nil, context)
	if err != nil {
		return nil, err
	}
	html, err := djtemplate.Render(djtemplate.Model, map[string]any{"template": content, "header": headerText, "footer": footerText},
		request.Language)
	if err != nil {
		return nil, err
	}
	return htmlReply(html, request.Language), nil
}

func renderOptionalEmpty(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

// medical is document_medical_appointment_view (IsAuthenticated | HasBypassAuthorizationHeader).
func (s *DocumentViews) medical(ctx context.Context, identity domain.ResolvedIdentity, request DocumentViewRequest) (*DocumentViewReply, error) {
	id, err := paymentInfoParseUUID(request.UID)
	if err != nil {
		return nil, err
	}
	appointment, err := s.store.Appointment(ctx, id.String())
	if err != nil {
		// .first() is None: None.subscription raises AttributeError.
		return nil, lookupError(err)
	}
	// Served to the association's staff and to the member of the appointment's subscription.
	if err := memberDocumentAccess(ctx, s.store, identity, identity.ScopeUser, func(ctx context.Context, member string) (bool, error) {
		return s.store.MemberSubscription(ctx, member, appointment.Subscription)
	}); err != nil {
		return nil, err
	}
	if appointment.Region != "agonistica" && appointment.Region != "non-agonistica" {
		return &DocumentViewReply{Status: 303, JSON: map[string]string{
			"error": "This kind of medical appointment is not supported in this endpoint"}}, nil
	}
	association, err := s.store.Association(ctx, appointment.Association)
	if err != nil {
		return nil, lookupError(err)
	}
	var associate any
	if appointment.AssociateID != nil {
		row, err := s.store.Associate(ctx, *appointment.AssociateID)
		if err != nil {
			return nil, lookupError(err)
		}
		associate = s.associateObject(ctx, row)
	}
	meta, err := jsonValue(appointment.Meta)
	if err != nil {
		return nil, err
	}
	name := djtemplate.Agonistica
	if appointment.Region == "non-agonistica" {
		name = djtemplate.NonAgonistica
	}
	html, err := djtemplate.Render(name, map[string]any{
		"medical_appointment": djtemplate.NewObject("MedicalAppointments", map[string]any{
			"date": optionalDate(appointment.Date), "sport": optional(appointment.Sport)}),
		"sport_association": s.associationObject(association, nil), "associate": associate, "meta": meta,
	}, "it")
	if err != nil {
		return nil, err
	}
	return htmlReply(html, "it"), nil
}

// associationObject is a SportAssociation as the templates read it.
func (s *DocumentViews) associationObject(a *domain.ViewAssociation, sections any) *djtemplate.Object {
	return djtemplate.NewObject("SportAssociation", map[string]any{
		"logo": optional(a.Logo), "denomination": a.Denomination, "tax_code": a.TaxCode, "address": optional(a.Address),
		"address_city": optional(a.AddressCity), "address_cap": optional(a.AddressCap), "federation": optional(a.Federation),
		"enroll_number": optional(a.EnrollNumber), "regulation": optional(a.Regulation), "demand": optional(a.Demand),
		"additional_sections": sections,
	})
}

// datetime.strptime(text, '%d/%m/%Y'): the directive patterns' \d matches
// the decimal digits of every script (a day's second digit, the year).
var strptimeDMY = regexp.MustCompile(`^(3[01]|[12]\p{Nd}|0[1-9]|[1-9]| [1-9])/(1[0-2]|0[1-9]|[1-9])/(\p{Nd}{4})$`)

func parseDMY(text string) (time.Time, bool) {
	match := strptimeDMY.FindStringSubmatch(text)
	if match == nil {
		return time.Time{}, false
	}
	day, _ := strconv.Atoi(dto.PythonASCIIDigits(strings.TrimSpace(match[1])))
	month, _ := strconv.Atoi(match[2])
	year, _ := strconv.Atoi(dto.PythonASCIIDigits(match[3]))
	if year < 1 {
		return time.Time{}, false
	}
	value := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if value.Day() != day || int(value.Month()) != month {
		return time.Time{}, false
	}
	return value, true
}

// compensation is document_compensation_view.
func (s *DocumentViews) compensation(ctx context.Context, identity domain.ResolvedIdentity, request DocumentViewRequest) (*DocumentViewReply, error) {
	id, ok := parseModelUUID(request.UID)
	if !ok {
		return nil, ErrDocumentViewServer
	}
	payment, err := s.store.Compensation(ctx, id, renderScope(identity, request.Group))
	if err != nil {
		return nil, lookupError(err)
	}
	// Served to the association's staff and to the account tied to the instructor.
	if err := memberDocumentAccess(ctx, s.store, identity, identity.ScopeUser, func(ctx context.Context, member string) (bool, error) {
		return s.store.MemberCompensation(ctx, member, payment.ID)
	}); err != nil {
		return nil, err
	}
	hours, err := s.store.Hours(ctx, payment.ID)
	if err != nil {
		return nil, err
	}
	var dates []time.Time
	for _, hour := range hours {
		if hour.CompensationType == "hourly" {
			dates = append(dates, hour.Date)
			continue
		}
		if hour.Period == nil {
			return nil, ErrDocumentViewServer
		}
		parts := strings.Split(*hour.Period, " al ")
		if len(parts) < 2 {
			return nil, ErrDocumentViewServer
		}
		from, okFrom := parseDMY(parts[0])
		if !okFrom {
			return nil, ErrDocumentViewServer
		}
		to, okTo := parseDMY(parts[1])
		if !okTo {
			return nil, ErrDocumentViewServer
		}
		dates = append(dates, from, to)
	}
	var fromDate, toDate any
	if len(dates) > 0 {
		low, high := dates[0], dates[0]
		for _, value := range dates[1:] {
			if value.Before(low) {
				low = value
			}
			if value.After(high) {
				high = value
			}
		}
		fromDate, toDate = djtemplate.DateOf(low), djtemplate.DateOf(high)
	}
	var association, instructor, signature any
	if payment.Association != nil {
		row, err := s.store.Association(ctx, *payment.Association)
		if err != nil {
			return nil, lookupError(err)
		}
		association = s.associationObject(row, nil)
	}
	if payment.InstructorID != nil {
		row, err := s.store.Instructor(ctx, *payment.InstructorID)
		if err != nil {
			return nil, lookupError(err)
		}
		instructor = djtemplate.NewObject("Instructor", map[string]any{
			"is_volunteer": row.IsVolunteer, "first_name": row.FirstName, "last_name": row.LastName, "tax_code": optional(row.TaxCode),
			"address": optional(row.Address), "civic_number": optional(row.CivicNumber), "address_city": optional(row.AddressCity),
			"born_date": optionalDate(row.BornDate), "born_city": optional(row.BornCity), "born_province": optional(row.BornProvince),
			"phone": optional(row.Phone), "email": optional(row.Email), "study_title": optional(row.StudyTitle), "role": optional(row.Role),
			"stipulated_contract_in": optionalDate(row.StipulatedContractIn)})
	}
	if payment.SignatureID != nil {
		text, err := s.store.Signature(ctx, *payment.SignatureID)
		if err != nil {
			return nil, lookupError(err)
		}
		signature = djtemplate.NewObject("Signature", map[string]any{"signature": text})
	}
	var paymentDate any
	if payment.PaymentDate != nil {
		paymentDate = djtemplate.DateTime{Time: *payment.PaymentDate}
	}
	html, err := djtemplate.Render(djtemplate.Compensation, map[string]any{
		"payment": djtemplate.NewObject("Payment", map[string]any{"sport_association": association, "instructor": instructor,
			"amount": djtemplate.Decimal(payment.Amount), "payment_date": paymentDate, "signature": signature,
			"notes": optional(payment.Notes)}),
		"from_date": fromDate, "to_date": toDate,
	}, "it")
	if err != nil {
		return nil, err
	}
	return htmlReply(html, "it"), nil
}

// subscriptionTemplate is SportAssociation.get_subscription_template().
func subscriptionTemplate(a *domain.ViewAssociation) string {
	if a.SubscriptionTemplate != nil && *a.SubscriptionTemplate == "subscription_classic.html" {
		return djtemplate.SubscriptionClassic
	}
	return djtemplate.Subscription
}

func showTo(flags [3]bool) djtemplate.List {
	list := djtemplate.List{}
	for i, flag := range flags {
		if flag {
			list = append(list, int64(i+1))
		}
	}
	return list
}

// subscription is document_subscription_view.
func (s *DocumentViews) subscription(ctx context.Context, identity domain.ResolvedIdentity, request DocumentViewRequest) (*DocumentViewReply, error) {
	id, ok := parseModelUUID(request.UID)
	if !ok {
		return nil, ErrDocumentViewServer
	}
	subscription, err := s.store.Subscription(ctx, id, renderScope(identity, request.Group))
	if err != nil {
		return nil, lookupError(err)
	}
	// Rendered for the association's staff and for the member the subscription belongs to.
	if err := memberDocumentAccess(ctx, s.store, identity, identity.ScopeUser, func(ctx context.Context, member string) (bool, error) {
		return s.store.MemberSubscription(ctx, member, id)
	}); err != nil {
		return nil, err
	}
	var paymentData any
	if subscription.HasPayment && subscription.PaymentMeta != nil {
		meta, err := jsonValue(subscription.PaymentMeta)
		if err != nil {
			return nil, err
		}
		if truth, err := djtemplate.Truthy(meta); err != nil {
			return nil, err
		} else if truth {
			paymentData = meta
		}
	}
	association, err := s.store.Association(ctx, subscription.Association)
	if err != nil {
		return nil, lookupError(err)
	}
	sections, err := jsonValue(association.AdditionalSections)
	if err != nil {
		return nil, err
	}
	list, isList := sections.(djtemplate.List)
	if !isList {
		// for section in <not a list>: TypeError, or item assignment on a str.
		return nil, ErrDocumentViewServer
	}
	for _, item := range list {
		section, isDict := item.(*djtemplate.Dict)
		if !isDict {
			return nil, ErrDocumentViewServer
		}
		show := djtemplate.List{}
		for index, key := range []string{"show_to_members", "show_to_both", "show_to_athletes"} {
			value, present := section.Get(key)
			if !present {
				continue
			}
			truth, err := djtemplate.Truthy(value)
			if err != nil {
				return nil, err
			}
			if truth {
				show = append(show, int64(index+1))
			}
		}
		section.Set("show_to", show)
	}
	today := s.todayText()
	mentionContext := mentions.Context{"sport_association": associationResolver(association), "subscription": modelResolver(mentionSubscriptionAttributes),
		"other": otherResolver(map[string]string{"today": today})}
	header, err := mentions.Filter(renderOptionalEmpty(association.DocumentHeader), association.DocumentHeader != nil, mentionContext)
	if err != nil {
		return nil, err
	}
	footer, err := mentions.Filter(renderOptionalEmpty(association.InvoiceFooter), association.InvoiceFooter != nil, mentionContext)
	if err != nil {
		return nil, err
	}
	var associate any
	if subscription.AssociateID != nil {
		row, err := s.store.Associate(ctx, *subscription.AssociateID)
		if err != nil {
			return nil, lookupError(err)
		}
		associate = s.associateObject(ctx, row)
	}
	additional, err := jsonValue(subscription.AdditionalFields)
	if err != nil {
		return nil, err
	}
	hasSignature := renderOptionalEmpty(subscription.SignatureStorageKey) != "" || renderOptionalEmpty(subscription.SignatureURL) != ""
	object := djtemplate.NewObject("Subscription", map[string]any{
		"sport_association": s.associationObject(association, list), "creation_date": djtemplate.DateTime{Time: subscription.CreationDate},
		"type": subscription.Type, "role": subscription.Role, "associate": associate, "additional_fields": additional,
		"has_signature": hasSignature,
		"get_signature_base64": djtemplate.Lazy(func() (any, error) {
			// Subscription.get_signature_base64: the stored object as a PNG
			// data URI, then the legacy URL, then None.
			if key := renderOptionalEmpty(subscription.SignatureStorageKey); key != "" {
				if s.signatures == nil {
					return nil, errDocumentViewSignature
				}
				if content, err := s.signatures.Read(ctx, key); err == nil {
					return "data:image/png;base64," + base64.StdEncoding.EncodeToString(content), nil
				}
				// Every read error is logged and falls through to the legacy URL.
			}
			if url := renderOptionalEmpty(subscription.SignatureURL); url != "" {
				if s.signatureFetcher == nil {
					return nil, domain.ErrServerComposition
				}
				content, err := s.signatureFetcher.Fetch(ctx, url)
				if err != nil {
					// Every fetch error is logged and the signature is None.
					return nil, nil
				}
				return "data:image/png;base64," + base64.StdEncoding.EncodeToString(content), nil
			}
			return nil, nil
		}),
	})
	html, err := djtemplate.Render(subscriptionTemplate(association), map[string]any{
		"uid": request.UID, "subscription": object, "payment_data": paymentData, "show_regulation_to": showTo(association.ShowRegulation),
		"show_demand_to": showTo(association.ShowDemand), "is_preview": false, "header": header, "footer": footer,
	}, request.Language)
	if err != nil {
		return nil, err
	}
	return htmlReply(html, request.Language), nil
}

const previewBlank = "____________________________"

func previewPerson(minor bool) map[string]any {
	attrs := map[string]any{"first_name": previewBlank, "last_name": previewBlank, "tax_code": previewBlank, "born_city": previewBlank,
		"born_date": previewBlank, "address": previewBlank, "address_cap": "______________", "address_city": previewBlank,
		"sex": "[ ] M [ ] F [ ] Altro", "phone": previewBlank, "email": previewBlank, "is_minor": minor,
		"get_full_name": djtemplate.Lazy(func() (any, error) { return previewBlank + " " + previewBlank, nil }),
		"full_name":     previewBlank + " " + previewBlank,
		// calculate_age of a born_date str that int() rejects is 0.
		"is_minor_now": true, "health_insurance_card": djtemplate.Missing, "tutor": djtemplate.Missing,
	}
	return attrs
}

// preview is document_subscription_preview (AllowAny).
func (s *DocumentViews) preview(ctx context.Context, identity domain.ResolvedIdentity, request DocumentViewRequest) (*DocumentViewReply, error) {
	idValue, hasID := request.Query.Lookup("sport_association_id")
	idText, _ := idValue.(string)
	if _, print := request.Query.Lookup("print"); print {
		shown := "None"
		if hasID {
			shown = idText
		}
		pdf, err := s.renderer.Render(ctx, request.Path+"?sport_association_id="+shown, request.Authorization)
		if err != nil {
			return &DocumentViewReply{Status: 500, JSON: map[string]string{"error": "PDF not rendered"}}, nil
		}
		return &DocumentViewReply{Status: 200, JSON: map[string]string{"file": base64.StdEncoding.EncodeToString(pdf)}}, nil
	}
	if !hasID {
		// request.user.sport_association, then the unbound local: both raise.
		return nil, ErrDocumentViewServer
	}
	id, ok := parseModelUUID(idText)
	if !ok {
		return nil, ErrDocumentViewServer
	}
	var scope *string
	if identity.ImpersonationAssociationID != "" && identity.Effective != nil {
		value := identity.ImpersonationAssociationID
		scope = &value
	}
	association, err := s.store.ActiveAssociation(ctx, id, scope)
	if err != nil {
		return nil, lookupError(err)
	}
	// user.sport_association
	switch association.UserRole {
	case domain.RoleAthlete:
		return &DocumentViewReply{Status: 403, JSON: map[string]string{"detail": "User is not a sport association"}}, nil
	case domain.RoleCollaborator:
		return nil, errDocumentViewCollaboratorOwner
	}
	owned, err := s.store.AssociationsOfUser(ctx, association.UserID)
	if err != nil {
		return nil, err
	}
	if len(owned) != 1 {
		return nil, ErrDocumentViewServer
	}
	userAssociation := &owned[0]
	additional, err := jsonValue(association.AdditionalFields)
	if err != nil {
		return nil, err
	}
	sections, err := jsonValue(userAssociation.AdditionalSections)
	if err != nil {
		return nil, err
	}
	today := s.todayText()
	mentionContext := mentions.Context{"sport_association": associationResolver(association), "subscription": modelResolver(mentionSubscriptionAttributes),
		"other": otherResolver(map[string]string{"today": today})}
	header, err := mentions.Filter(renderOptionalEmpty(association.DocumentHeader), association.DocumentHeader != nil, mentionContext)
	if err != nil {
		return nil, err
	}
	footer, err := mentions.Filter(renderOptionalEmpty(association.InvoiceFooter), association.InvoiceFooter != nil, mentionContext)
	if err != nil {
		return nil, err
	}
	tutor := djtemplate.NewObject("Associate", previewPerson(false))
	person := previewPerson(true)
	person["main_tutor"] = djtemplate.Lazy(func() (any, error) { return tutor, nil })
	object := djtemplate.NewObject("Subscription", map[string]any{
		"sport_association": s.associationObject(userAssociation, sections), "creation_date": nil, "type": nil, "role": nil,
		"associate": djtemplate.NewObject("Associate", person), "additional_fields": additional, "has_signature": false,
		"get_signature_base64": djtemplate.Lazy(func() (any, error) { return nil, nil }),
	})
	html, err := djtemplate.Render(subscriptionTemplate(userAssociation), map[string]any{
		"uid": nil, "subscription": object, "payment_data": nil, "show_regulation_to": djtemplate.List{int64(1), int64(2), int64(3)},
		"show_demand_to": djtemplate.List{int64(1), int64(2), int64(3)}, "is_preview": true, "header": header, "footer": footer,
	}, request.Language)
	if err != nil {
		return nil, err
	}
	return htmlReply(html, request.Language), nil
}

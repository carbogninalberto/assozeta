package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/djtemplate"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/mentions"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// HTML views of BE/docmanager/views/printing_views.py for invoices.
const (
	ViewInvoice  = "invoice-view"
	ViewEinvoice = "einvoice-view"
)

// InvoiceViews renders document_invoice_view and document_einvoice_view.
// Shared reads (associations, associates, tutors, courses, instructors) and
// the bypass check come from the document views.
type InvoiceViews struct {
	store domain.InvoiceViewStore
	views *DocumentViews
}

func NewInvoiceViews(store domain.InvoiceViewStore, views *DocumentViews) *InvoiceViews {
	return &InvoiceViews{store: store, views: views}
}

// AuthorizeBypass is HasBypassAuthorizationHeader after IsAuthenticated failed.
func (s *InvoiceViews) AuthorizeBypass(authorization *string) (bool, error) {
	return s.views.AuthorizeBypass(authorization)
}

func (s *InvoiceViews) Render(ctx context.Context, identity domain.ResolvedIdentity, request DocumentViewRequest) (*DocumentViewReply, error) {
	var reply *DocumentViewReply
	var err error
	ctx = withViewPromotion(ctx, identity, request.Promotion)
	switch request.View {
	case ViewInvoice:
		reply, err = s.invoice(ctx, identity, request)
	case ViewEinvoice:
		reply, err = s.einvoice(ctx, identity, request)
	default:
		err = ErrDocumentViewServer
	}
	if err != nil {
		// mentions.ErrUnsupported is a refusal sentinel itself: it passes
		// through for the handoff.
		return nil, err
	}
	return reply, nil
}

// errInvoiceViewsUnreachable is a state only the application writes: a stored
// value outside its column type.
var errInvoiceViewsUnreachable = refusal.AppState("invoice view: stored value outside the column type")

// invoiceViewsPaymentTypes is Payment.get_type_in_lang('it').
var invoiceViewsPaymentTypes = map[string]string{"default": "Non specificato", "cash": "Contanti", "transfer": "Bonifico",
	"online": "Online", "sepa-transfer": "Bonifico SEPA", "stripe": "Stripe", "pos": "POS"}

// invoiceViewsCategory is payment.payment_category: the stored row or the
// in-memory PaymentCategory(name=get_course_carnet_name()).
type invoiceViewsCategory struct {
	name          any // a stored str, or the course name's JSON value
	taxDeductible bool
	deleted       bool
}

// invoice is document_invoice_view.
func (s *InvoiceViews) invoice(ctx context.Context, identity domain.ResolvedIdentity, request DocumentViewRequest) (*DocumentViewReply, error) {
	id, ok := parseModelUUID(request.UID)
	if !ok {
		return nil, ErrDocumentViewServer
	}
	scope := renderScope(identity, request.Group)
	group := supplierGroup(request.Group)
	invoice, err := s.store.Invoice(ctx, id, scope)
	if err != nil {
		return nil, lookupError(err)
	}
	// Rendered for the association's staff and for the member the receipt belongs to.
	if err := memberDocumentAccess(ctx, s.store, identity, identity.ScopeUser, func(ctx context.Context, member string) (bool, error) {
		return s.store.MemberInvoice(ctx, member, invoice.ID)
	}); err != nil {
		return nil, err
	}
	payments, err := s.store.Payments(ctx, invoice.ID, scope)
	if err != nil {
		return nil, err
	}
	if len(payments) != 1 {
		// get(): DoesNotExist or MultipleObjectsReturned.
		return nil, ErrDocumentViewServer
	}
	payment := payments[0]
	association, err := s.views.store.Association(ctx, invoice.Association)
	if err != nil {
		return nil, lookupError(err)
	}
	owner, err := s.store.Owner(ctx, invoice.Association)
	if err != nil {
		return nil, lookupError(err)
	}
	footer := renderOptionalEmpty(association.InvoiceFooter)
	header := association.DocumentHeader
	paymentMethod, ok := invoiceViewsPaymentTypes[payment.Type]
	if !ok {
		return nil, ErrDocumentViewServer // KeyError
	}
	var category *invoiceViewsCategory
	if payment.CategoryID != nil {
		row, err := s.store.Category(ctx, *payment.CategoryID)
		if err != nil {
			return nil, lookupError(err)
		}
		category = &invoiceViewsCategory{name: row.Name, taxDeductible: row.TaxDeductible, deleted: row.Deleted}
	}
	description := optional(invoice.Description)
	if payment.Subject == 0 {
		text, err := s.description(ctx, payment, category, association.Denomination)
		if err != nil {
			return nil, err
		}
		description = text
	}
	stampFree := payment.Subject == 2 || payment.Subject == 1 || (category != nil && category.taxDeductible)
	courseName := any(nil)
	if payment.Subject == 2 {
		if courseName, err = s.courseCarnetName(ctx, payment, group); err != nil {
			return nil, err
		}
		if category == nil {
			category = &invoiceViewsCategory{name: courseName}
		}
	}
	categories, stampFree, err := s.paymentCategories(ctx, payment, invoice, category, stampFree)
	if err != nil {
		return nil, err
	}
	bodyStyle := ""
	if invoice.Cancelled {
		bodyStyle = "opacity:0.7;text-decoration: line-through;"
		footer += "<br><br><b>RICEVUTA ANNULLATA (non è più valida)</b>"
	}
	var associate, tutor *domain.ViewAssociate
	var associateValue any
	if payment.AssociateID != nil {
		if associate, err = s.views.store.Associate(ctx, *payment.AssociateID); err != nil {
			return nil, lookupError(err)
		}
		minor, err := s.store.AssociateMinor(ctx, associate.ID)
		if err != nil {
			return nil, lookupError(err)
		}
		if invoice.SelectedTutor != nil {
			if tutor, err = s.views.store.Associate(ctx, *invoice.SelectedTutor); err != nil {
				return nil, lookupError(err)
			}
		} else if tutor, err = s.views.mainTutor(ctx, associate.ID); err != nil {
			return nil, err
		}
		object := s.views.associateObject(ctx, associate)
		var tutorValue any
		if tutor != nil {
			tutorValue = s.views.associateObject(ctx, tutor)
			minor = true
		}
		object.Set("tutor", tutorValue)
		object.Set("is_minor", minor)
		associateValue = object
	}
	var sub *domain.InvoiceViewSubscription
	subscriptionID, err := s.store.PaymentSubscription(ctx, payment.ID, group)
	if err != nil {
		return nil, err
	}
	if subscriptionID != nil {
		if sub, err = s.store.Subscription(ctx, *subscriptionID); err != nil {
			return nil, lookupError(err)
		}
	}
	start, end, membershipFound, err := s.store.MembershipPeriod(ctx, subscriptionID)
	if err != nil {
		return nil, err
	}
	subPeriod, membershipPeriod := "", ""
	if sub != nil {
		subPeriod = invoicePeriod(domain.InvoiceGeneratePeriod{Start: sub.Start, End: sub.End})
	}
	if membershipFound {
		membershipPeriod = invoicePeriod(domain.InvoiceGeneratePeriod{Start: start, End: end})
	}
	plans, err := invoiceViewsPlans(sub)
	if err != nil {
		return nil, err
	}
	template := djtemplate.Invoice
	if owner.InvoiceTemplate != nil && *owner.InvoiceTemplate == "invoice_classic.html" {
		template = djtemplate.InvoiceClassic
	}
	coursesList := ""
	if sub != nil {
		if coursesList, err = s.views.store.CoursesList(ctx, sub.ID); err != nil {
			if errors.Is(err, domain.ErrViewCourseTitle) {
				return nil, ErrDocumentViewServer
			}
			return nil, err
		}
	}
	invoiceMentions, err := invoiceViewsInvoiceResolver(invoice)
	if err != nil {
		return nil, err
	}
	mentionContext := mentions.Context{
		"sport_association": associationResolver(association), "invoice": invoiceMentions,
		"payment": modelResolver(mentionPaymentAttributes), "associate": invoiceViewsAssociateResolver(associate),
		"main_tutor": associateResolver(tutor),
		"other":      otherResolver(map[string]string{"today": s.views.todayText(), "courses_list": coursesList}),
	}
	headerText, err := mentions.Filter(renderOptionalEmpty(header), header != nil, mentionContext)
	if err != nil {
		return nil, err
	}
	if footer, err = mentions.Filter(footer, true, mentionContext); err != nil {
		return nil, err
	}
	extraText := optional(owner.ExtraText)
	if owner.ExtraText != nil && *owner.ExtraText != "" {
		text, blank, err := mentions.FilterBlank(*owner.ExtraText, mentionContext)
		if err != nil {
			return nil, err
		}
		extraText = text
		if blank {
			extraText = nil
		}
	}
	if category != nil && owner.HideCategoryName {
		category.name = ""
	}
	overrideLabel := (owner.DefaultCategory && category != nil && !category.deleted) || (category != nil && !owner.HideCategoryName)
	customData, err := invoiceViewsCustomData(sub)
	if err != nil {
		return nil, err
	}
	if sub == nil || payment.AssociateID == nil {
		customData = nil
	}
	context, err := s.invoiceContext(ctx, invoiceViewsRender{
		request: request, invoice: invoice, payment: payment, association: association, category: category,
		description: description, associate: associateValue, courseName: courseName, categories: categories,
	})
	if err != nil {
		return nil, err
	}
	var subValue any
	if sub != nil {
		subValue = djtemplate.NewObject("Subscription", map[string]any{"type": sub.Type})
	}
	for key, value := range map[string]any{
		"extra_text_invoices": extraText, "body_style": bodyStyle, "enumerate_invoices": owner.EnumerateInvoices,
		"custom_data": customData, "stamp_free": stampFree, "payment_method": paymentMethod, "sub": subValue,
		"sub_memebership": nil, "sub_period": subPeriod, "sub_membership_period": membershipPeriod, "override_label": overrideLabel,
		"got_plan_name": plans.planName, "got_membership_plan_name": plans.membershipName, "got_plan": plans.plan,
		"got_membership_plan": plans.membership, "header": headerText, "footer": footer,
	} {
		context[key] = value
	}
	html, err := djtemplate.Render(template, context, request.Language)
	if err != nil {
		return nil, err
	}
	return htmlReply(html, request.Language), nil
}

type invoiceViewsRender struct {
	request     DocumentViewRequest
	invoice     *domain.InvoiceViewInvoice
	payment     domain.InvoiceViewPayment
	association *domain.ViewAssociation
	category    *invoiceViewsCategory
	description any
	associate   any
	courseName  any
	categories  any
}

// invoiceContext builds the template objects the view passes.
func (s *InvoiceViews) invoiceContext(ctx context.Context, r invoiceViewsRender) (map[string]any, error) {
	total, err := invoiceViewsDecimalSum(r.invoice.ActivityFee, r.invoice.MembershipFee)
	if err != nil {
		return nil, err
	}
	var categoryValue any
	if r.category != nil {
		categoryValue = djtemplate.NewObject("PaymentCategory", map[string]any{"name": r.category.name})
	}
	var supplier any
	if r.payment.SupplierID != nil {
		row, err := s.store.Supplier(ctx, *r.payment.SupplierID)
		if err != nil {
			return nil, lookupError(err)
		}
		supplier = djtemplate.NewObject("SupplierAndCustomers", map[string]any{"name": row.Name, "address": optional(row.Address),
			"tax_code": optional(row.TaxCode)})
	}
	var paymentDate any
	if r.payment.PaymentDate != nil {
		paymentDate = djtemplate.DateTime{Time: *r.payment.PaymentDate}
	}
	var number any
	if r.invoice.Number != nil {
		number = *r.invoice.Number
	}
	payment := djtemplate.NewObject("Payment", map[string]any{
		"payment_category": categoryValue, "description": optional(r.payment.Description), "associate": r.associate,
		"supplier": supplier, "payment_date": paymentDate, "notes": optional(r.payment.Notes), "subject": r.payment.Subject,
	})
	invoice := djtemplate.NewObject("Invoice", map[string]any{
		"sport_association": djtemplate.NewObject("SportAssociation", map[string]any{"logo": optional(r.association.Logo),
			"denomination": r.association.Denomination}),
		"number": number, "description": r.description, "activity_fee": djtemplate.Decimal(r.invoice.ActivityFee),
		"membership_fee": djtemplate.Decimal(r.invoice.MembershipFee),
	})
	return map[string]any{"uid": r.request.UID, "total_amount": total, "payment": payment, "course_name": r.courseName,
		"payment_categories": r.categories, "invoice": invoice}, nil
}

// invoiceViewsDecimalSum is Decimal + Decimal of two 2-place columns.
func invoiceViewsDecimalSum(a, b string) (djtemplate.Decimal, error) {
	x, ok := new(big.Rat).SetString(a)
	y, ok2 := new(big.Rat).SetString(b)
	if !ok || !ok2 || !invoiceViewsTwoPlaces(a) || !invoiceViewsTwoPlaces(b) {
		// Invoice.activity_fee and Invoice.membership_fee are
		// DecimalField(max_digits=9, decimal_places=2), so PostgreSQL reads
		// them back with two decimal places.
		return "", errInvoiceViewsUnreachable
	}
	return djtemplate.Decimal(new(big.Rat).Add(x, y).FloatString(2)), nil
}

// invoiceViewsInvoiceResolver: the Invoice fields a mention reads (the legacy
// aliases' number, dates and amounts) as str() writes them.
func invoiceViewsInvoiceResolver(invoice *domain.InvoiceViewInvoice) (mentions.Resolver, error) {
	total, err := invoiceViewsDecimalSum(invoice.ActivityFee, invoice.MembershipFee)
	if err != nil {
		return nil, err
	}
	totalText := string(total)
	created, err := djtemplate.PythonStr(djtemplate.DateTime{Time: invoice.CreationDate})
	if err != nil {
		return nil, err
	}
	var number *string
	if invoice.Number != nil {
		text := strconv.FormatInt(*invoice.Number, 10)
		number = &text
	}
	return fieldResolver(mentionInvoiceAttributes, map[string]*string{"number": number, "creation_date": &created,
		"activity_fee": &invoice.ActivityFee, "membership_fee": &invoice.MembershipFee, "total_amount": &totalText}), nil
}

// invoiceViewsAssociateResolver: the payment's associate, to which the view
// adds the tutor and is_minor attributes.
func invoiceViewsAssociateResolver(associate *domain.ViewAssociate) mentions.Resolver {
	resolve := associateResolver(associate)
	if associate == nil {
		return resolve
	}
	return func(path string) (string, error) {
		if first, _, _ := strings.Cut(path, "."); first == "tutor" || first == "is_minor" {
			return "", fmt.Errorf("%w: mention associate.%s", mentions.ErrUnsupported, path)
		}
		return resolve(path)
	}
}

func invoiceViewsTwoPlaces(text string) bool {
	_, fraction, found := strings.Cut(text, ".")
	return found && len(fraction) == 2
}

// description is generate_invoice_description for an OTHER payment.
func (s *InvoiceViews) description(ctx context.Context, payment domain.InvoiceViewPayment, category *invoiceViewsCategory,
	denomination string) (any, error) {
	reason := any("Pagamento")
	if payment.Description != nil && *payment.Description != "" {
		reason = *payment.Description
	} else if category != nil && invoiceViewsTruthy(category.name) {
		reason = category.name
	}
	if payment.AssociateID == nil {
		recipient := ""
		if payment.SupplierID != nil {
			supplier, err := s.store.Supplier(ctx, *payment.SupplierID)
			if err != nil {
				return nil, lookupError(err)
			}
			recipient = supplier.Name
		} else if payment.InstructorID != nil {
			instructor, err := s.views.store.Instructor(ctx, *payment.InstructorID)
			if err != nil {
				return nil, lookupError(err)
			}
			recipient = dto.PythonStrip(instructor.FirstName + " " + instructor.LastName)
		}
		text, err := djtemplate.PythonStr(reason)
		if err != nil {
			return nil, err
		}
		if recipient != "" {
			return formatHTML("Ricevuta di pagamento di <b>%s</b> con causale <b>%s</b> per l'associazione <b>%s</b>.", recipient, text,
				denomination), nil
		}
		return formatHTML(invoiceDescriptionTemplates[0], text, denomination), nil
	}
	name := reason
	if category != nil {
		name = category.name
	}
	text, err := djtemplate.PythonStr(name)
	if err != nil {
		return nil, err
	}
	return formatHTML(invoiceDescriptionTemplates[0], text, denomination), nil
}

// courseCarnetName is Payment.get_course_carnet_name() of a COURSE payment:
// the meta's 'description' as it is, or a str title.
func (s *InvoiceViews) courseCarnetName(ctx context.Context, payment domain.InvoiceViewPayment, group *string) (any, error) {
	found, err := s.store.CourseName(ctx, payment.ID, group)
	if err != nil {
		return nil, err
	}
	if found.Found {
		// course_subscription.payment is the payment itself: its meta's 'description'.
		meta, err := invoiceViewsJSON(payment.Meta)
		if err != nil {
			return nil, err
		}
		if invoiceViewsTruthy(meta) {
			present, iterable := invoiceViewsMembership(meta, "description")
			if !iterable {
				// 'description' in a number or bool: TypeError.
				return nil, ErrDocumentViewServer
			}
			if present {
				object, ok := meta.(domain.RequestObject)
				if !ok {
					// list['description'] / str['description']: TypeError.
					return nil, ErrDocumentViewServer
				}
				value, _ := object.Lookup("description")
				return invoiceViewsText(value)
			}
		}
		if found.MissingCarnet {
			return nil, ErrDocumentViewServer // None.title
		}
		return optional(found.Title), nil
	}
	course, err := invoiceViewsJSON(payment.Course)
	if err != nil {
		return nil, err
	}
	if invoiceViewsTruthy(course) {
		present, iterable := invoiceViewsMembership(course, "label")
		if !iterable {
			// 'label' in a number or bool: TypeError.
			return nil, ErrDocumentViewServer
		}
		if present {
			object, ok := course.(domain.RequestObject)
			if !ok {
				// list['label'] / str['label']: TypeError.
				return nil, ErrDocumentViewServer
			}
			label, _ := object.Lookup("label")
			text, ok := label.(string)
			if !ok {
				return nil, ErrDocumentViewServer // ", ".join of a non-str
			}
			return text + ", " + renderOptionalEmpty(payment.Description), nil
		}
	}
	return optional(payment.Description), nil
}

// invoiceViewsMembership is `key in value` for a parsed JSON value: dict keys,
// list items by Python equality and str substrings. iterable is false for a
// number or a bool, whose `in` raises TypeError.
func invoiceViewsMembership(value any, key string) (present, iterable bool) {
	switch v := value.(type) {
	case domain.RequestObject:
		_, present = v.Lookup(key)
		return present, true
	case []any:
		for _, item := range v {
			if dto.PythonEqual(item, key) {
				return true, true
			}
		}
		return false, true
	case string:
		return strings.Contains(v, key), true
	}
	return false, false
}

// invoiceViewsTruthy is bool() of a parsed JSON value or of a converted template
// value: a float underflow is 0 and -0.0 is false.
func invoiceViewsTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			number, ok := new(big.Int).SetString(text, 10)
			return !ok || number.Sign() != 0
		}
		number, ok := invoiceViewsFloatText(text)
		return !ok || number != 0
	case int64:
		return v != 0
	case *big.Int:
		return v.Sign() != 0
	case float64:
		return v != 0
	case djtemplate.List:
		return len(v) > 0
	case djtemplate.Tuple:
		return len(v) > 0
	case *djtemplate.Dict:
		return v.Len() > 0
	case []any:
		return len(v) > 0
	case domain.RequestObject:
		return len(v) > 0
	}
	return true
}

// invoiceViewsText is a stored meta 'description' as the template sees it: a
// PaymentCategory(name=value) and the course_name, exactly as Django returns
// the JSON value (a str, or any other jsonb value).
func invoiceViewsText(value any) (any, error) {
	return invoiceViewsTemplateValue(value)
}

func invoiceViewsJSON(raw *string) (any, error) {
	if raw == nil {
		return nil, nil
	}
	return dto.ParseReferenceJSON([]byte(*raw))
}

// paymentCategories is the meta_payment_categories block: the {name: amount}
// dict (None without meta categories) and stamp_free.
func (s *InvoiceViews) paymentCategories(ctx context.Context, payment domain.InvoiceViewPayment, invoice *domain.InvoiceViewInvoice,
	category *invoiceViewsCategory, stampFree bool) (any, bool, error) {
	meta, err := invoiceViewsJSON(payment.MetaPaymentCategories)
	if err != nil {
		return nil, false, err
	}
	if !invoiceViewsTruthy(meta) {
		return nil, stampFree, nil
	}
	items, ok := meta.([]any)
	if !ok {
		// len()/iteration of a truthy non-list ends in a TypeError.
		return nil, false, ErrDocumentViewServer
	}
	categories := djtemplate.NewDict()
	if category != nil && payment.Subject != 1 {
		text, ok := category.name.(string)
		if !ok {
			return nil, false, ErrDocumentViewServer // non-str .lower(): AttributeError
		}
		name := pystr.Lower(text)
		subtract, err := invoiceViewsSum(items)
		if err != nil {
			return nil, false, err
		}
		fee, err := strconv.ParseFloat(invoice.ActivityFee, 64)
		if err != nil {
			// Invoice.activity_fee is DecimalField(max_digits=9, decimal_places=2):
			// float() of it cannot fail.
			return nil, false, errInvoiceViewsUnreachable
		}
		categories.Set(name, fee-subtract)
	}
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return nil, false, ErrDocumentViewServer
		}
		raw, present := object.Lookup("payment_category_id")
		if !present {
			return nil, false, ErrDocumentViewServer
		}
		// PaymentCategory.objects.get(payment_category_id=value): UUIDField.to_python.
		text, valid := dto.DjangoUUIDLookup(raw)
		if !valid {
			return nil, false, ErrDocumentViewServer
		}
		row, err := s.store.Category(ctx, text)
		if err != nil {
			return nil, false, lookupError(err)
		}
		name := pystr.Lower(row.Name)
		amount, present := object.Lookup("amount")
		if !present {
			return nil, false, ErrDocumentViewServer
		}
		value, err := invoiceViewsTemplateValue(amount)
		if err != nil {
			return nil, false, err
		}
		key := name
		if _, taken := categories.Get(name); taken {
			for i := 1; ; i++ {
				key = name + " (" + strconv.Itoa(i) + ")"
				if _, taken := categories.Get(key); !taken {
					break
				}
			}
		}
		categories.Set(key, value)
		stampFree = stampFree && row.TaxDeductible
	}
	return categories, stampFree, nil
}

// invoiceViewsSumOperand is one 'amount' as sum() adds it: an exact int (a bool
// is 0 or 1) or a float.
type invoiceViewsSumOperand struct {
	integer *big.Int // nil for a float
	real    float64
}

// invoiceViewsSumOperandOf is the amount as sum() sees it. A str, None, list or
// dict is not a sum() operand and is a TypeError.
func invoiceViewsSumOperandOf(amount any) (invoiceViewsSumOperand, error) {
	switch v := amount.(type) {
	case bool:
		if v {
			return invoiceViewsSumOperand{integer: big.NewInt(1)}, nil
		}
		return invoiceViewsSumOperand{integer: big.NewInt(0)}, nil
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			number, ok := new(big.Int).SetString(text, 10)
			if !ok {
				// The parser only emits valid integer text.
				return invoiceViewsSumOperand{}, errInvoiceViewsUnreachable
			}
			return invoiceViewsSumOperand{integer: number}, nil
		}
		number, ok := invoiceViewsFloatText(text)
		if !ok {
			return invoiceViewsSumOperand{}, errInvoiceViewsUnreachable
		}
		return invoiceViewsSumOperand{real: number}, nil
	}
	return invoiceViewsSumOperand{}, ErrDocumentViewServer
}

// invoiceViewsSum is float(sum(item['amount'] for item in items)). CPython's
// sum() (builtin_sum_impl) is not a plain float total:
//
//   - Its first loop adds exact ints (a bool is an int) in a C Py_ssize_t
//     (int64 here) and leaves at the first item that is not an int or that
//     would overflow it. A float item is added there with PyNumber_Add, so
//     float(int total) + x (OverflowError past a double); the compensated
//     float loop then continues with the rest. An int that does not fit is
//     also added with PyNumber_Add, but the total stays an int, so the generic
//     loop below runs instead.
//   - A float total uses Neumaier compensation for float items:
//     t = f + x; c += fabs(f) >= fabs(x) ? (f - t) + x : (x - t) + f; f = t.
//     An int that fits a C long is added without compensation, as
//     f += float64(value). An int that does not fit first adds c to f, then is
//     added with PyNumber_Add, and the generic loop takes over.
//   - The float loop ends with f += c, unless c is zero, infinite or nan.
//   - The generic loop is a plain PyNumber_Add per item: an exact int total
//     stays exact, and its float() at the end is an OverflowError past a
//     double. Once that total is a float, every later item is a naive add.
func invoiceViewsSum(items []any) (float64, error) {
	// The generator's item['amount'] reads happen as sum() consumes the items;
	// a non-dict item or a missing key is the generator's TypeError.
	amounts := make([]any, len(items))
	for i, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return 0, ErrDocumentViewServer // subscript of a non-dict
		}
		amount, present := object.Lookup("amount")
		if !present {
			return 0, ErrDocumentViewServer // KeyError
		}
		amounts[i] = amount
	}
	// CPython's int fast loop, in a Py_ssize_t.
	var total int64
	for i, amount := range amounts {
		operand, err := invoiceViewsSumOperandOf(amount)
		if err != nil {
			return 0, err
		}
		if operand.integer == nil {
			return invoiceViewsSumFloat(amounts[i+1:], float64(total)+operand.real)
		}
		if operand.integer.IsInt64() {
			value := operand.integer.Int64()
			if invoiceViewsSumFitsInt64(total, value) {
				total += value
				continue
			}
		}
		// Either the item or the total does not fit: PyNumber_Add on the exact
		// total, which is an int, so the generic loop runs.
		exact := new(big.Int).SetInt64(total)
		return invoiceViewsSumInt(amounts[i+1:], exact.Add(exact, operand.integer))
	}
	return float64(total), nil
}

// invoiceViewsSumFitsInt64 is CPython's Py_ssize_t overflow test before adding b
// to a.
func invoiceViewsSumFitsInt64(a, b int64) bool {
	if a >= 0 {
		return b <= math.MaxInt64-a
	}
	return b >= math.MinInt64-a
}

// invoiceViewsSumCompensated adds Neumaier's compensation to a float total,
// unless it is zero, infinite or nan (an infinite or nan total keeps c out).
func invoiceViewsSumCompensated(total, compensation float64) float64 {
	if compensation != 0 && !math.IsInf(compensation, 0) && !math.IsNaN(compensation) {
		return total + compensation
	}
	return total
}

// invoiceViewsSumFloat is CPython's compensated float loop. An int beyond a C
// long adds c, then the int with PyNumber_Add, and leaves the rest of the
// items to the generic loop.
func invoiceViewsSumFloat(items []any, total float64) (float64, error) {
	var compensation float64
	for i, amount := range items {
		operand, err := invoiceViewsSumOperandOf(amount)
		if err != nil {
			return 0, err
		}
		if operand.integer == nil {
			sum := total + operand.real
			if math.Abs(total) >= math.Abs(operand.real) {
				compensation += (total - sum) + operand.real
			} else {
				compensation += (operand.real - sum) + total
			}
			total = sum
			continue
		}
		if operand.integer.IsInt64() {
			total += float64(operand.integer.Int64())
			continue
		}
		total = invoiceViewsSumCompensated(total, compensation)
		value, ok := invoiceViewsIntFloat(operand.integer)
		if !ok {
			return 0, ErrDocumentViewServer // float(int): OverflowError
		}
		return invoiceViewsSumNaive(items[i+1:], total+value)
	}
	return invoiceViewsSumCompensated(total, compensation), nil
}

// invoiceViewsSumInt is the generic loop entered with an exact int total: every
// item is PyNumber_Add, so ints stay exact and the first float makes the total
// float(int) + x (OverflowError when the int does not fit a double).
func invoiceViewsSumInt(items []any, total *big.Int) (float64, error) {
	for i, amount := range items {
		operand, err := invoiceViewsSumOperandOf(amount)
		if err != nil {
			return 0, err
		}
		if operand.integer != nil {
			total.Add(total, operand.integer)
			continue
		}
		value, ok := invoiceViewsIntFloat(total)
		if !ok {
			return 0, ErrDocumentViewServer // float(int): OverflowError
		}
		return invoiceViewsSumNaive(items[i+1:], value+operand.real)
	}
	value, ok := invoiceViewsIntFloat(total)
	if !ok {
		return 0, ErrDocumentViewServer // float(int): OverflowError
	}
	return value, nil
}

// invoiceViewsSumNaive is the generic loop once the total is a float: every
// later item is a PyNumber_Add, which is a plain float addition.
func invoiceViewsSumNaive(items []any, total float64) (float64, error) {
	for _, amount := range items {
		operand, err := invoiceViewsSumOperandOf(amount)
		if err != nil {
			return 0, err
		}
		if operand.integer == nil {
			total += operand.real
			continue
		}
		value, ok := invoiceViewsIntFloat(operand.integer)
		if !ok {
			return 0, ErrDocumentViewServer // float(int): OverflowError
		}
		total += value
	}
	return total, nil
}

// invoiceViewsFloatText is float(text) of a JSON number: an overflowing
// exponent is ±Inf (Python's float() reads it so) and an underflow is 0 or a
// subnormal. ok false is text the parser cannot emit.
func invoiceViewsFloatText(text string) (float64, bool) {
	value, err := strconv.ParseFloat(text, 64)
	if err != nil {
		var number *strconv.NumError
		if !errors.As(err, &number) || number.Err != strconv.ErrRange {
			return 0, false
		}
	}
	return value, true
}

// invoiceViewsIntFloat is float(int), correctly rounded. ok false is Python's
// OverflowError when the integer does not fit a float.
func invoiceViewsIntFloat(number *big.Int) (float64, bool) {
	value, _ := new(big.Float).SetInt(number).Float64()
	if math.IsInf(value, 0) {
		return 0, false
	}
	return value, true
}

// invoiceViewsTemplateValue converts a parsed JSON value to a template value.
func invoiceViewsTemplateValue(value any) (any, error) {
	switch v := value.(type) {
	case nil, bool, string:
		return v, nil
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			number, err := strconv.ParseInt(text, 10, 64)
			if err == nil {
				return number, nil
			}
			bigNumber, ok := new(big.Int).SetString(text, 10)
			if !ok {
				// The parser only emits valid integer text.
				return nil, errInvoiceViewsUnreachable
			}
			return bigNumber, nil
		}
		number, ok := invoiceViewsFloatText(text)
		if !ok {
			// The parser only emits JSON numbers.
			return nil, errInvoiceViewsUnreachable
		}
		return number, nil
	case []any:
		list := djtemplate.List{}
		for _, item := range v {
			converted, err := invoiceViewsTemplateValue(item)
			if err != nil {
				return nil, err
			}
			list = append(list, converted)
		}
		return list, nil
	case domain.RequestObject:
		dict := djtemplate.NewDict()
		for _, field := range v {
			converted, err := invoiceViewsTemplateValue(field.Value)
			if err != nil {
				return nil, err
			}
			dict.Set(field.Name, converted)
		}
		return dict, nil
	}
	// The parser emits only the JSON value types above.
	return nil, errInvoiceViewsUnreachable
}

type invoiceViewsPlanResult struct {
	plan, membership         bool
	planName, membershipName any
}

// invoiceViewsPlansMembership is the plan block for a list or str meta:
// `'key' in meta` is a list membership or a str substring, then meta.get raises
// AttributeError, which the view's try/except turns into the end of the block.
// A key that is absent everywhere leaves plan_id unbound (NameError).
func invoiceViewsPlansMembership(meta any, result invoiceViewsPlanResult) invoiceViewsPlanResult {
	for _, step := range []struct {
		key  string
		flag *bool
	}{
		{"plan_id", &result.plan},
		{"membership_plan_id", &result.membership},
		{"plan_name", nil},
		{"membership_plan_name", nil},
	} {
		present, iterable := invoiceViewsMembership(meta, step.key)
		if !iterable {
			return result // TypeError from `in`
		}
		if present {
			if step.flag != nil {
				*step.flag = true
			}
			return result // .get raises AttributeError
		}
	}
	return result // plan_id stays unbound: NameError
}

// invoiceViewsPlans is the view's plan block (its try/except stops at the
// first exception, keeping what it assigned).
func invoiceViewsPlans(sub *domain.InvoiceViewSubscription) (invoiceViewsPlanResult, error) {
	result := invoiceViewsPlanResult{planName: "-", membershipName: "-"}
	if sub == nil {
		return result, nil
	}
	parsed, err := invoiceViewsJSON(sub.Meta)
	if err != nil {
		return result, err
	}
	var meta domain.RequestObject
	switch v := parsed.(type) {
	case nil:
		return result, nil
	case bool, json.Number:
		return result, nil // 'plan_id' in <number>: TypeError
	case []any, string:
		return invoiceViewsPlansMembership(v, result), nil
	case domain.RequestObject:
		meta = v
	default:
		// The parser emits only the JSON value types above.
		return result, errInvoiceViewsUnreachable
	}
	planID, hasPlan := meta.Lookup("plan_id")
	membershipID, hasMembership := meta.Lookup("membership_plan_id")
	result.plan, result.membership = hasPlan, hasMembership
	var planName, membershipName any = "-", "-"
	if value, ok := meta.Lookup("plan_name"); ok {
		planName = value
	}
	if value, ok := meta.Lookup("membership_plan_name"); ok {
		membershipName = value
	}
	finish := func() (invoiceViewsPlanResult, error) {
		var err error
		if result.planName, err = invoiceViewsTemplateValue(planName); err != nil {
			return result, err
		}
		result.membershipName, err = invoiceViewsTemplateValue(membershipName)
		return result, err
	}
	if !hasPlan {
		return finish() // plan_id unbound: NameError
	}
	notEmpty := func(value any) bool { return value != nil && !dto.PythonEqual(value, "") }
	isDash := func(value any) bool { return dto.PythonEqual(value, "-") }
	// search walks a plan list; ok false means an exception ended the block.
	search := func(raw *string, id any) (any, bool, bool, error) {
		plans, err := invoiceViewsJSON(raw)
		if err != nil {
			return nil, false, false, err
		}
		list, isList := plans.([]any)
		if !isList {
			switch v := plans.(type) {
			case domain.RequestObject:
				return nil, false, len(v) == 0, nil
			case string:
				return nil, false, v == "", nil
			}
			return nil, false, false, nil
		}
		for _, item := range list {
			plan, ok := item.(domain.RequestObject)
			if !ok {
				return nil, false, false, nil
			}
			planKey, present := plan.Lookup("id")
			if !present {
				return nil, false, false, nil
			}
			if dto.PythonEqual(planKey, id) {
				name, present := plan.Lookup("name")
				if !present {
					return nil, false, false, nil
				}
				return name, true, true, nil
			}
		}
		return nil, false, true, nil
	}
	if notEmpty(planID) && isDash(planName) {
		plans, err := invoiceViewsJSON(sub.SubscriptionPlans)
		if err != nil {
			return result, err
		}
		if invoiceViewsTruthy(plans) {
			name, found, ok, err := search(sub.SubscriptionPlans, planID)
			if err != nil {
				return result, err
			}
			if found {
				planName = name
			}
			if !ok {
				return finish()
			}
		}
	}
	if !hasMembership {
		return finish()
	}
	if notEmpty(membershipID) && isDash(membershipName) {
		name, found, _, err := search(sub.MembershipPlans, membershipID)
		if err != nil {
			return result, err
		}
		if found {
			membershipName = name
		}
	}
	return finish()
}

// invoiceViewsCustomData is sub.custom_data after the is_multiple fix.
func invoiceViewsCustomData(sub *domain.InvoiceViewSubscription) (any, error) {
	if sub == nil || sub.CustomData == nil {
		return nil, nil
	}
	parsed, err := invoiceViewsJSON(sub.CustomData)
	if err != nil {
		return nil, err
	}
	if !invoiceViewsTruthy(parsed) {
		return invoiceViewsTemplateValue(parsed)
	}
	object, ok := parsed.(domain.RequestObject)
	if !ok {
		// Item assignment / subscript of a truthy non-dict: TypeError.
		return nil, ErrDocumentViewServer
	}
	value, err := invoiceViewsTemplateValue(object)
	if err != nil {
		return nil, err
	}
	dict := value.(*djtemplate.Dict)
	multiple, present := object.Lookup("is_multiple")
	dict.Set("is_multiple", present && invoiceViewsTruthy(multiple))
	return dict, nil
}

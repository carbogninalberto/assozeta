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
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/djtemplate"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/mentions"
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
	switch request.View {
	case ViewInvoice:
		reply, err = s.invoice(ctx, identity, request)
	case ViewEinvoice:
		reply, err = s.einvoice(ctx, request)
	default:
		err = ErrDocumentViewServer
	}
	if err != nil {
		if errors.Is(err, djtemplate.ErrUnsupported) || errors.Is(err, mentions.ErrUnsupported) || errors.Is(err, domain.ErrViewTutorWrite) ||
			errors.Is(err, errInvoiceViewsNotPorted) {
			return nil, fmt.Errorf("%w: %v", ErrDocumentViewNotPorted, err)
		}
		return nil, err
	}
	return reply, nil
}

// errInvoiceViewsNotPorted refuses a stored value outside the port.
var errInvoiceViewsNotPorted = errors.New("invoice view: value not ported")

func invoiceViewsNotPorted(format string, args ...any) error {
	return fmt.Errorf("%w: "+format, append([]any{errInvoiceViewsNotPorted}, args...)...)
}

// invoiceViewsPaymentTypes is Payment.get_type_in_lang('it').
var invoiceViewsPaymentTypes = map[string]string{"default": "Non specificato", "cash": "Contanti", "transfer": "Bonifico",
	"online": "Online", "sepa-transfer": "Bonifico SEPA", "stripe": "Stripe", "pos": "POS"}

// invoiceViewsCategory is payment.payment_category: the stored row or the
// in-memory PaymentCategory(name=get_course_carnet_name()).
type invoiceViewsCategory struct {
	name          any // str, or None for the in-memory category
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
		} else if tutor, err = s.views.store.MainTutor(ctx, associate.ID); err != nil {
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
	mentionContext := mentions.Context{
		"sport_association": associationResolver(association), "invoice": refusingResolver("invoice"),
		"payment": refusingResolver("payment"), "associate": associateResolver(associate), "main_tutor": associateResolver(tutor),
		"other": otherResolver(map[string]string{"today": s.views.todayText(), "courses_list": coursesList}),
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
		return "", invoiceViewsNotPorted("decimal %q + %q", a, b)
	}
	return djtemplate.Decimal(new(big.Rat).Add(x, y).FloatString(2)), nil
}

func invoiceViewsTwoPlaces(text string) bool {
	_, fraction, found := strings.Cut(text, ".")
	return found && len(fraction) == 2
}

// description is generate_invoice_description for an OTHER payment.
func (s *InvoiceViews) description(ctx context.Context, payment domain.InvoiceViewPayment, category *invoiceViewsCategory,
	denomination string) (any, error) {
	reason := "Pagamento"
	if payment.Description != nil && *payment.Description != "" {
		reason = *payment.Description
	} else if category != nil && category.name.(string) != "" {
		reason = category.name.(string)
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
		if recipient != "" {
			return formatHTML("Ricevuta di pagamento di <b>%s</b> con causale <b>%s</b> per l'associazione <b>%s</b>.", recipient, reason,
				denomination), nil
		}
		return formatHTML(invoiceDescriptionTemplates[0], reason, denomination), nil
	}
	name := reason
	if category != nil {
		name = category.name.(string)
	}
	return formatHTML(invoiceDescriptionTemplates[0], name, denomination), nil
}

// courseCarnetName is Payment.get_course_carnet_name() of a COURSE payment:
// a str or None.
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
		if dto.PythonTruthy(meta) {
			object, ok := meta.(domain.RequestObject)
			if !ok {
				return nil, invoiceViewsNotPorted("payment meta %T", meta)
			}
			if value, present := object.Lookup("description"); present {
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
	if dto.PythonTruthy(course) {
		object, ok := course.(domain.RequestObject)
		if !ok {
			return nil, invoiceViewsNotPorted("payment course %T", course)
		}
		if label, present := object.Lookup("label"); present {
			text, ok := label.(string)
			if !ok {
				return nil, ErrDocumentViewServer // ", ".join of a non-str
			}
			return text + ", " + renderOptionalEmpty(payment.Description), nil
		}
	}
	return optional(payment.Description), nil
}

// invoiceViewsText accepts a str or None (other JSON values are not ported).
func invoiceViewsText(value any) (any, error) {
	switch v := value.(type) {
	case nil:
		return nil, nil
	case string:
		return v, nil
	}
	return nil, invoiceViewsNotPorted("description %T", value)
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
	if !dto.PythonTruthy(meta) {
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
			return nil, false, ErrDocumentViewServer // None.lower()
		}
		name, err := invoiceViewsLower(text)
		if err != nil {
			return nil, false, err
		}
		subtract, err := invoiceViewsSum(items)
		if err != nil {
			return nil, false, err
		}
		fee, err := strconv.ParseFloat(invoice.ActivityFee, 64)
		if err != nil {
			return nil, false, invoiceViewsNotPorted("activity fee %q", invoice.ActivityFee)
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
		text, ok := raw.(string)
		if !ok {
			return nil, false, invoiceViewsNotPorted("payment_category_id %T", raw)
		}
		id, valid := parseModelUUID(text)
		if !valid {
			return nil, false, ErrDocumentViewServer
		}
		row, err := s.store.Category(ctx, id)
		if err != nil {
			return nil, false, lookupError(err)
		}
		name, err := invoiceViewsLower(row.Name)
		if err != nil {
			return nil, false, err
		}
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

// invoiceViewsSum is float(sum(item['amount'] for item in items)) for the
// numbers and bools sum() accepts; anything else raises TypeError.
func invoiceViewsSum(items []any) (float64, error) {
	exact := new(big.Int)
	var total float64
	floating := false
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return 0, ErrDocumentViewServer
		}
		amount, present := object.Lookup("amount")
		if !present {
			return 0, ErrDocumentViewServer
		}
		switch v := amount.(type) {
		case bool:
			if v {
				if floating {
					total++
				} else {
					exact.Add(exact, big.NewInt(1))
				}
			}
		case json.Number:
			text := string(v)
			if !strings.ContainsAny(text, ".eE") {
				number, ok := new(big.Int).SetString(text, 10)
				if !ok {
					return 0, invoiceViewsNotPorted("amount %s", text)
				}
				if floating {
					value, err := invoiceViewsIntFloat(number)
					if err != nil {
						return 0, err
					}
					total += value
				} else {
					exact.Add(exact, number)
				}
				continue
			}
			number, err := strconv.ParseFloat(text, 64)
			if err != nil || math.IsInf(number, 0) {
				return 0, invoiceViewsNotPorted("amount %s", text)
			}
			if !floating {
				start, err := invoiceViewsIntFloat(exact)
				if err != nil {
					return 0, err
				}
				total, floating = start, true
			}
			total += number
		default:
			return 0, ErrDocumentViewServer
		}
	}
	if !floating {
		return invoiceViewsIntFloat(exact)
	}
	if math.IsInf(total, 0) || math.IsNaN(total) {
		return 0, invoiceViewsNotPorted("non-finite sum")
	}
	return total, nil
}

// invoiceViewsIntFloat is float(int), correctly rounded.
func invoiceViewsIntFloat(number *big.Int) (float64, error) {
	value, _ := new(big.Float).SetInt(number).Float64()
	if math.IsInf(value, 0) {
		return 0, invoiceViewsNotPorted("int too large for float")
	}
	return value, nil
}

// invoiceViewsLower is str.lower() (runes with special Python mappings refused).
func invoiceViewsLower(text string) (string, error) {
	for _, r := range text {
		if r == 0x130 || r == 0x3A3 || (r >= 0x1F88 && r <= 0x1FFC) {
			return "", invoiceViewsNotPorted("lower() of %U", r)
		}
	}
	return strings.Map(unicode.ToLower, text), nil
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
			if err != nil {
				return nil, invoiceViewsNotPorted("integer %s", text)
			}
			return number, nil
		}
		number, err := strconv.ParseFloat(text, 64)
		if err != nil || math.IsInf(number, 0) {
			return nil, invoiceViewsNotPorted("float %s", text)
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
	return nil, invoiceViewsNotPorted("JSON %T", value)
}

type invoiceViewsPlanResult struct {
	plan, membership         bool
	planName, membershipName any
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
	switch parsed.(type) {
	case nil:
		return result, nil
	case bool, json.Number:
		return result, nil // 'plan_id' in <number>: TypeError
	case domain.RequestObject:
	default:
		return result, invoiceViewsNotPorted("subscription meta %T", parsed)
	}
	meta := parsed.(domain.RequestObject)
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
		if dto.PythonTruthy(plans) {
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
	if !dto.PythonTruthy(parsed) {
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
	dict.Set("is_multiple", present && dto.PythonTruthy(multiple))
	return dict, nil
}

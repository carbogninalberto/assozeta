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
	// ErrDocumentViewNotPorted refuses an input outside the port (the 500 page).
	ErrDocumentViewNotPorted = refusal.New("document view: input not ported")
)

// PreviewRenderer is pdfrender.Client's call without request headers.
type PreviewRenderer interface {
	RenderWithoutHeaders(ctx context.Context, url string) ([]byte, error)
}

type DocumentViews struct {
	store       domain.DocumentViewStore
	renderer    PreviewRenderer
	bypassToken string
	now         func() time.Time
}

func NewDocumentViews(store domain.DocumentViewStore, renderer PreviewRenderer, bypassToken string, now func() time.Time) *DocumentViews {
	return &DocumentViews{store: store, renderer: renderer, bypassToken: bypassToken, now: now}
}

// DocumentViewRequest is one call of an HTML view.
type DocumentViewRequest struct {
	View, UID, Path string
	Query           domain.RequestObject
	Group           string
	Language        string
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
	reply, err := s.render(ctx, identity, request)
	if err != nil {
		if errors.Is(err, djtemplate.ErrUnsupported) || errors.Is(err, mentions.ErrUnsupported) || errors.Is(err, domain.ErrViewTutorWrite) {
			return nil, fmt.Errorf("%w: %v", ErrDocumentViewNotPorted, err)
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
		tutor, err := s.store.MainTutor(ctx, id)
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

func fieldResolver(fields map[string]*string) mentions.Resolver {
	return func(path string) (string, error) {
		value, ok := fields[path]
		if !ok {
			return "", fmt.Errorf("%w: mention %q", mentions.ErrUnsupported, path)
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
	return fieldResolver(map[string]*string{"denomination": &denomination, "tax_code": &taxCode, "iban": a.Iban, "website": a.Website,
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
	return fieldResolver(map[string]*string{"first_name": a.FirstName, "last_name": a.LastName, "tax_code": a.TaxCode,
		"born_date": dateText(a.BornDate), "born_city": a.BornCity, "address": a.Address, "address_city": a.AddressCity,
		"address_cap": a.AddressCap, "nationality": a.Nationality, "nationality_residence": a.NationalityRes, "email": a.Email,
		"phone": a.Phone, "phone_2": a.Phone2, "phone_3": a.Phone3, "phone_4": a.Phone4, "phone_label": a.PhoneLabel,
		"phone_2_label": a.Phone2Label, "phone_3_label": a.Phone3Label, "phone_4_label": a.Phone4Label, "notes": a.Notes, "sex": &sex})
}

// otherResolver is the 'other' dict: dict.get(key) of its entries; a
// missing key is None ("").
func otherResolver(values map[string]string) mentions.Resolver {
	return func(path string) (string, error) {
		if strings.Contains(path, ".") {
			return "", fmt.Errorf("%w: mention other.%s", mentions.ErrUnsupported, path)
		}
		return values[path], nil
	}
}

// refusingResolver: an object whose attributes the port does not read.
func refusingResolver(name string) mentions.Resolver {
	return func(path string) (string, error) {
		return "", fmt.Errorf("%w: mention %s.%s", mentions.ErrUnsupported, name, path)
	}
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
					tutor, err := s.store.MainTutor(ctx, associate.ID)
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

// datetime.strptime(text, '%d/%m/%Y') for ASCII input.
var strptimeDMY = regexp.MustCompile(`^(3[01]|[12][0-9]|0[1-9]|[1-9]| [1-9])/(1[0-2]|0[1-9]|[1-9])/([0-9]{4})$`)

func parseDMY(text string) (time.Time, bool) {
	match := strptimeDMY.FindStringSubmatch(text)
	if match == nil {
		return time.Time{}, false
	}
	day, _ := strconv.Atoi(strings.TrimSpace(match[1]))
	month, _ := strconv.Atoi(match[2])
	year, _ := strconv.Atoi(match[3])
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
		for _, r := range *hour.Period {
			if r > 0x7f {
				return nil, fmt.Errorf("%w: non-ASCII period", ErrDocumentViewNotPorted)
			}
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
	mentionContext := mentions.Context{"sport_association": associationResolver(association), "subscription": refusingResolver("subscription"),
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
			// Reads the private storage object or the legacy URL.
			return nil, fmt.Errorf("%w: signature image", ErrDocumentViewNotPorted)
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
		pdf, err := s.renderer.RenderWithoutHeaders(ctx, request.Path+"?sport_association_id="+shown)
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
		return nil, fmt.Errorf("%w: collaborator account as an association's user", ErrDocumentViewNotPorted)
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
	mentionContext := mentions.Context{"sport_association": associationResolver(association), "subscription": refusingResolver("subscription"),
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

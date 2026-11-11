package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/googleapi"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pydatetime"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyheader"
)

// GoogleCalendarSettings is the Django configuration the views read:
// GOOGLE_CALENDAR_CLIENT_SECRET_FILE, GOOGLE_CALENDAR_REDIRECT_URL and the
// oauthlib environment switches.
type GoogleCalendarSettings struct {
	ClientSecretFile string
	RedirectURL      string
	Environment      googleapi.Environment
}

// GoogleCalendar ports BE/application/views/google_views.py.
type GoogleCalendar struct {
	store     domain.GoogleCalendarStore
	transport *googleapi.Transport
	settings  GoogleCalendarSettings
	now       func() time.Time
}

func NewGoogleCalendar(store domain.GoogleCalendarStore, transport *googleapi.Transport, settings GoogleCalendarSettings,
	now func() time.Time) *GoogleCalendar {
	return &GoogleCalendar{store: store, transport: transport, settings: settings, now: now}
}

// GoogleConfigAnswer is the config GET answer and the session key SessionMiddleware sets.
type GoogleConfigAnswer struct {
	Body       map[string]string
	SessionKey string
}

// googleFailure keeps a Python exception's 500 distinct from Go errors.
func googleFailure(err error) error {
	if err == nil {
		return nil
	}
	var python *googleapi.PythonError
	var connection *googleapi.ConnectionError
	var api *googleapi.HTTPError
	if errors.As(err, &python) || errors.As(err, &connection) || errors.As(err, &api) || errors.Is(err, googleapi.ErrNotPorted) {
		return domain.ErrGoogleServer
	}
	return err
}

// actingUser is `request.user`, or acting_user(request) for collaborators.
func (s *GoogleCalendar) actingUser(ctx context.Context, identity domain.ResolvedIdentity) (*domain.GoogleUser, error) {
	id := identity.ScopeUser.ID
	if identity.Collaborator {
		id = identity.Effective.ID
	}
	return s.store.User(ctx, id)
}

func googleFields(user *domain.GoogleUser) domain.GoogleUserFields {
	return domain.GoogleUserFields{State: user.State, Credentials: user.Credentials, AutoSync: user.AutoSync}
}

// storedCredentials is the Python value of integration_google_credentials (nil for None).
func storedCredentials(user *domain.GoogleUser) (any, error) {
	if user.Credentials == nil {
		return nil, nil
	}
	return dto.ParsePythonJSON(*user.Credentials)
}

func pythonStringJSON(text string) *string {
	encoded := string(dto.PythonJSONDumps(text))
	return &encoded
}

// credentials is User.get_google_credentials(): expired credentials are
// refreshed through requests and saved before they are returned.
func (s *GoogleCalendar) credentials(ctx context.Context, user *domain.GoogleUser, audit domain.LoginAuditContext) (*googleapi.Credentials, error) {
	stored, err := storedCredentials(user)
	if err != nil {
		return nil, err
	}
	if stored == nil {
		return nil, nil
	}
	credentials, err := googleapi.LoadCredentials(stored, s.now())
	if err != nil {
		return nil, googleFailure(err)
	}
	if credentials.Expired(s.now()) {
		if err := googleapi.RefreshForLoad(ctx, s.transport, credentials, s.now); err != nil {
			return nil, googleFailure(err)
		}
		user.Credentials = pythonStringJSON(credentials.ToJSON())
		if err := s.store.SaveUser(ctx, user.ID, googleFields(user), audit, s.now()); err != nil {
			return nil, err
		}
	}
	return credentials, nil
}

// Check is google_check.
func (s *GoogleCalendar) Check(ctx context.Context, identity domain.ResolvedIdentity) (map[string]bool, error) {
	user, err := s.actingUser(ctx, identity)
	if err != nil {
		return nil, err
	}
	stored, err := storedCredentials(user)
	if err != nil {
		return nil, err
	}
	return map[string]bool{"google_sync_enabled": stored != nil}, nil
}

// ConfigGet is google_calendar_config GET: the authorization URL; the state
// is saved on the acting user and in a new session.
// sessionKey is the request's sessionid cookie ("" without one): a key the
// cache holds is saved again with the new state (only a session holding just
// a state is supported); any other key gets a new session.
func (s *GoogleCalendar) ConfigGet(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext, sessionKey string) (*GoogleConfigAnswer, error) {
	audit = paymentCategoryAuditContext(identity, audit)
	reuse := false
	// _validate_session_key: a key shorter than 8 characters is no session.
	if len(sessionKey) >= 8 {
		found, other, err := s.store.SessionState(ctx, sessionKey)
		if err != nil {
			return nil, err
		}
		if other {
			// Another session's contents (not written by this view): not ported.
			return nil, domain.ErrGoogleServer
		}
		reuse = found
	}
	config, err := googleapi.LoadClientSecrets(s.settings.ClientSecretFile)
	if err != nil {
		return nil, googleFailure(err)
	}
	location, state, err := config.AuthorizationURL(s.settings.RedirectURL, s.settings.Environment)
	if err != nil {
		return nil, googleFailure(err)
	}
	user, err := s.actingUser(ctx, identity)
	if err != nil {
		return nil, err
	}
	fields := googleFields(user)
	fields.State = &state
	if err := s.store.SaveUser(ctx, user.ID, fields, audit, s.now()); err != nil {
		return nil, err
	}
	if reuse {
		saved, err := s.store.SaveSession(ctx, sessionKey, state)
		if err != nil {
			return nil, err
		}
		if !saved {
			// UpdateError: the session expired during the request.
			return nil, domain.ErrGoogleServer
		}
		return &GoogleConfigAnswer{Body: map[string]string{"authorization_url": location}, SessionKey: sessionKey}, nil
	}
	key, err := s.store.CreateSession(ctx, state)
	if err != nil {
		return nil, err
	}
	return &GoogleConfigAnswer{Body: map[string]string{"authorization_url": location}, SessionKey: key}, nil
}

// ConfigDelete is google_calendar_config DELETE.
func (s *GoogleCalendar) ConfigDelete(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) (map[string]string, error) {
	audit = paymentCategoryAuditContext(identity, audit)
	user, err := s.actingUser(ctx, identity)
	if err != nil {
		return nil, err
	}
	credentials, err := s.credentials(ctx, user, audit)
	if err != nil {
		return nil, err
	}
	if credentials == nil {
		// credentials.token on None: AttributeError.
		return nil, domain.ErrGoogleServer
	}
	if err := googleapi.Revoke(ctx, s.transport, credentials.Token); err != nil {
		return nil, googleFailure(err)
	}
	if err := s.store.SaveUser(ctx, user.ID, domain.GoogleUserFields{}, audit, s.now()); err != nil {
		return nil, err
	}
	return map[string]string{"msg": "deleted"}, nil
}

// Callback is google_oauth2callback after the ?error check: the user holding
// the state, the code exchange and the stored credentials.
func (s *GoogleCalendar) Callback(ctx context.Context, identity domain.ResolvedIdentity, query domain.RequestObject, audit domain.LoginAuditContext) error {
	if identity.Actor != nil {
		audit = paymentCategoryAuditContext(identity, audit)
	}
	stateValue, present := query.Lookup("state")
	if !present {
		return domain.ErrGoogleServer
	}
	state, _ := stateValue.(string)
	user, err := s.store.UserByState(ctx, state)
	if err != nil {
		return err
	}
	if user == nil {
		return domain.ErrGoogleNoUser
	}
	config, err := googleapi.LoadClientSecrets(s.settings.ClientSecretFile)
	if err != nil {
		return googleFailure(err)
	}
	codeValue, present := query.Lookup("code")
	if !present {
		return domain.ErrGoogleServer
	}
	code, _ := codeValue.(string)
	stored, err := config.FetchToken(ctx, s.transport, code, s.settings.RedirectURL, s.settings.Environment, s.now)
	if err != nil {
		return googleFailure(err)
	}
	fields := googleFields(user)
	fields.Credentials = pythonStringJSON(stored)
	if user.Role != domain.RoleCollaborator {
		fields.AutoSync = true
	}
	return s.store.SaveUser(ctx, user.ID, fields, audit, s.now())
}

// List is google_calendar_list: every calendarList page.
func (s *GoogleCalendar) List(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) (any, error) {
	audit = paymentCategoryAuditContext(identity, audit)
	user, err := s.actingUser(ctx, identity)
	if err != nil {
		return nil, err
	}
	credentials, err := s.credentials(ctx, user, audit)
	if err != nil {
		return nil, err
	}
	if credentials == nil {
		// build() without credentials: google.auth.default() raises DefaultCredentialsError.
		return nil, domain.ErrGoogleServer
	}
	session := googleapi.NewSession(s.transport, credentials, s.now)
	calendars := []any{}
	var pageToken *string
	for {
		call := googleapi.Call{Method: "GET", Path: "/calendar/v3/users/me/calendarList"}
		if pageToken != nil {
			call.Query = []googleapi.Param{{Name: "pageToken", Value: *pageToken}}
		}
		answer, err := session.Execute(ctx, call)
		if err != nil {
			return nil, googleFailure(err)
		}
		page, ok := answer.(domain.RequestObject)
		if !ok {
			return nil, domain.ErrGoogleServer
		}
		items, present := googleLookup(page, "items")
		if !present {
			return nil, domain.ErrGoogleServer
		}
		list, ok := items.([]any)
		if !ok {
			// Iterating a string or dictionary: not ported.
			return nil, domain.ErrGoogleServer
		}
		calendars = append(calendars, list...)
		next, _ := googleLookup(page, "nextPageToken")
		if !googleTruthy(next) {
			break
		}
		text, ok := next.(string)
		if !ok {
			return nil, domain.ErrGoogleServer
		}
		pageToken = &text
	}
	return domain.RequestObject{{Name: "data", Value: domain.RequestObject{{Name: "calendars", Value: calendars}}}}, nil
}

func googleLookup(object domain.RequestObject, name string) (any, bool) {
	var found any
	present := false
	for _, field := range object {
		if field.Name == name {
			found, present = field.Value, true
		}
	}
	return found, present
}

func googleTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		f, err := v.Float64()
		return err != nil || f != 0
	case domain.RequestObject:
		return len(v) > 0
	case []any:
		return len(v) > 0
	}
	return true
}

// googleExportResult is the export answer: status and body.
type GoogleExportResult struct {
	Status int
	Body   map[string]any
}

const googleCalendarDescription = "Il calendario viene sincronizzato in automatico, non modificarlo."

// Export is google_calendar_export_course.
func (s *GoogleCalendar) Export(ctx context.Context, identity domain.ResolvedIdentity, courseID, group string,
	audit domain.LoginAuditContext) (*GoogleExportResult, error) {
	audit = paymentCategoryAuditContext(identity, audit)
	id, ok := dto.DjangoUUIDLookup(courseID)
	if !ok {
		return nil, domain.ErrGoogleServer
	}
	course, err := s.store.Course(ctx, id, supplierGroup(group))
	if err != nil {
		return nil, err
	}
	if course == nil {
		return &GoogleExportResult{Status: 404, Body: map[string]any{"msg": "Course not found."}}, nil
	}
	registry, err := s.store.FirstRegistry(ctx, course.ID)
	if err != nil {
		return nil, err
	}
	events, err := googleRegistryEvents(registry, course.ID)
	if err != nil {
		return nil, err
	}
	user, err := s.actingUser(ctx, identity)
	if err != nil {
		return nil, err
	}
	credentials, err := s.credentials(ctx, user, audit)
	if err != nil {
		return nil, err
	}
	if credentials == nil {
		return nil, domain.ErrGoogleServer
	}
	session := googleapi.NewSession(s.transport, credentials, s.now)
	swallow := func(err error) error {
		if errors.Is(err, googleapi.ErrNotPorted) {
			return domain.ErrGoogleServer
		}
		return nil
	}
	if course.CalendarID != nil {
		_, err := session.Execute(ctx, googleapi.Call{Method: "DELETE", Path: "/calendar/v3/calendars/" + googleapi.PathSegment(*course.CalendarID), Raw: true})
		if err := swallow(err); err != nil {
			return nil, err
		}
	}
	var title any
	if course.Title != nil {
		title = *course.Title
	}
	answer, err := session.Execute(ctx, googleapi.Call{Method: "POST", Path: "/calendar/v3/calendars", HasBody: true,
		Body: domain.RequestObject{{Name: "description", Value: googleCalendarDescription}, {Name: "summary", Value: title},
			{Name: "timeZone", Value: "Europe/Rome"}}})
	var calendarID string
	if err == nil {
		// calendar['id'] inside the try: a missing key or a non-mapping is caught too.
		object, ok := answer.(domain.RequestObject)
		value, present := googleLookup(object, "id")
		if !ok || !present {
			return &GoogleExportResult{Status: 400, Body: map[string]any{"msg": "Error creating calendar."}}, nil
		}
		text, ok := value.(string)
		if !ok {
			return nil, domain.ErrGoogleServer
		}
		calendarID = text
	} else {
		if errors.Is(err, googleapi.ErrNotPorted) {
			return nil, domain.ErrGoogleServer
		}
		if mentioning, ok := err.(interface{ Mentions(string) bool }); ok && mentioning.Mentions("quotaExceeded") {
			return &GoogleExportResult{Status: 403, Body: map[string]any{"msg": "quotaExceeded"}}, nil
		}
		return &GoogleExportResult{Status: 400, Body: map[string]any{"msg": "Error creating calendar."}}, nil
	}
	if user.Role != domain.RoleCollaborator {
		course.CalendarID = &calendarID
	}
	if err := s.store.SaveCourse(ctx, *course, audit, s.now()); err != nil {
		return nil, err
	}
	if course.Color == nil {
		// foreground_color(None): TypeError after the save.
		return nil, domain.ErrGoogleServer
	}
	color := *course.Color
	foreground, ok := googleForeground(color)
	if !ok {
		return nil, domain.ErrGoogleServer // int(background_color[1:3], 16) raises ValueError
	}
	_, err = session.Execute(ctx, googleapi.Call{Method: "DELETE", Path: "/calendar/v3/users/me/calendarList/" + googleapi.PathSegment(calendarID),
		Raw: true})
	if err := swallow(err); err != nil {
		return nil, err
	}
	if _, err := session.Execute(ctx, googleapi.Call{Method: "POST", Path: "/calendar/v3/users/me/calendarList", HasBody: true,
		Body: domain.RequestObject{{Name: "id", Value: calendarID}, {Name: "backgroundColor", Value: color},
			{Name: "foregroundColor", Value: foreground}}}); err != nil {
		return nil, googleFailure(err)
	}
	if registry == nil {
		return &GoogleExportResult{Status: 404, Body: map[string]any{"msg": "Attendance Registry not found."}}, nil
	}
	if events.failure {
		return nil, domain.ErrGoogleServer
	}
	for _, raw := range events.items {
		body, err := googleEventBody(course.ID, calendarID, raw)
		if err != nil {
			return nil, err
		}
		_, err = session.Execute(ctx, googleapi.Call{Method: "POST", Path: "/calendar/v3/calendars/" + googleapi.PathSegment(calendarID) + "/events",
			HasBody: true, Body: body})
		if err := swallow(err); err != nil {
			return nil, err
		}
	}
	var stored any
	if course.CalendarID != nil {
		stored = *course.CalendarID
	}
	return &GoogleExportResult{Status: 200, Body: map[string]any{"msg": "exported.", "google_calendar_id": stored}}, nil
}

// googleForeground is foreground_color(): int(background_color[1:3], 16) and so on
// over code points; ok is false when int() raises ValueError.
func googleForeground(color string) (string, bool) {
	runes := []rune(color)
	slice := func(from, to int) string {
		from, to = min(from, len(runes)), min(to, len(runes))
		return string(runes[from:to])
	}
	r, ok1 := pythonHexInt(slice(1, 3))
	g, ok2 := pythonHexInt(slice(3, 5))
	b, ok3 := pythonHexInt(slice(5, 7))
	if !ok1 || !ok2 || !ok3 {
		return "", false
	}
	if r*299+g*587+b*114 >= 128000 {
		return "#000000", true
	}
	return "#ffffff", true
}

// pythonHexInt is int(text, 16): surrounding whitespace, a sign, an optional
// 0x prefix and digits with single underscores between them; Unicode decimal
// digits count as their value.
func pythonHexInt(text string) (int64, bool) {
	text = strings.TrimFunc(text, pyheader.IsSpace)
	negative := false
	if text != "" && (text[0] == '+' || text[0] == '-') {
		negative = text[0] == '-'
		text = text[1:]
	}
	if len(text) >= 2 && text[0] == '0' && (text[1] == 'x' || text[1] == 'X') {
		text = text[2:]
		text = strings.TrimPrefix(text, "_")
	}
	if text == "" || strings.HasSuffix(text, "_") || strings.Contains(text, "__") || strings.HasPrefix(text, "_") {
		return 0, false
	}
	var value int64
	for _, r := range text {
		if r == '_' {
			continue
		}
		var digit int64
		switch {
		case r >= '0' && r <= '9':
			digit = int64(r - '0')
		case r >= 'a' && r <= 'f':
			digit = int64(r-'a') + 10
		case r >= 'A' && r <= 'F':
			digit = int64(r-'A') + 10
		default:
			number, isDigit := pydatetime.DecimalDigit(r)
			if !isDigit {
				return 0, false
			}
			digit = int64(number)
		}
		value = value*16 + digit
	}
	if negative {
		value = -value
	}
	return value, true
}

type googleEvents struct {
	items []any
	// failure: iterating registry.events raises (None, a number, a non-empty string or mapping).
	failure bool
}

// googleRegistryEvents reads registry.events and refuses, before any write,
// events whose dates Python formats in a platform-dependent way.
func googleRegistryEvents(registry *domain.GoogleRegistry, courseID string) (googleEvents, error) {
	if registry == nil {
		return googleEvents{}, nil
	}
	if registry.Events == nil {
		return googleEvents{failure: true}, nil
	}
	parsed, err := dto.ParsePythonJSON(*registry.Events)
	if err != nil {
		return googleEvents{}, err
	}
	switch v := parsed.(type) {
	case []any:
		for _, raw := range v {
			// A dry run of the conversion: only years below 1000 are refused (strftime('%Y')
			// pads on BSD libc and not on glibc), every other failure is the view's 500 later.
			if _, err := googleEventBody(courseID, "", raw); errors.Is(err, errGoogleYearUnpadded) {
				return googleEvents{}, googleapi.ErrNotPorted
			}
		}
		return googleEvents{items: v}, nil
	case domain.RequestObject:
		return googleEvents{failure: len(v) > 0}, nil
	case string:
		return googleEvents{failure: v != ""}, nil
	}
	return googleEvents{failure: true}, nil
}

// errGoogleYearUnpadded marks an event date before the year 1000.
var errGoogleYearUnpadded = errors.New("google: event year below 1000")

func googleLookupObject(object domain.RequestObject, name string) (domain.RequestObject, bool) {
	value, present := googleLookup(object, name)
	inner, ok := value.(domain.RequestObject)
	return inner, present && ok
}

// googleEventBody converts one registry event (the loop body before the
// insert): any Python exception is the view's 500.
func googleEventBody(courseID, calendarID string, raw any) (domain.RequestObject, error) {
	event, ok := raw.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrGoogleServer
	}
	value, present := googleLookup(event, "event_id")
	identifier, ok := value.(string)
	if !present || !ok {
		return nil, domain.ErrGoogleServer
	}
	identifier = strings.ReplaceAll(identifier, "-", "")
	parse := func(name string) (*pydatetime.Moment, error) {
		value, present := googleLookup(event, name)
		text, ok := value.(string)
		if !present || !ok {
			return nil, domain.ErrGoogleServer
		}
		moment, err := pydatetime.ParseDatetime(text)
		if err != nil {
			return nil, domain.ErrGoogleServer
		}
		return moment, nil
	}
	start, err := parse("start")
	if err != nil {
		return nil, err
	}
	description := ""
	if props, present := googleLookup(event, "extendedProps"); present {
		contains := false
		switch v := props.(type) {
		case domain.RequestObject:
			_, contains = googleLookup(v, "instructor")
		case string:
			contains = strings.Contains(v, "instructor")
		case []any:
			for _, item := range v {
				if item == "instructor" {
					contains = true
				}
			}
		default:
			return nil, domain.ErrGoogleServer
		}
		if contains {
			object, ok := props.(domain.RequestObject)
			if !ok {
				return nil, domain.ErrGoogleServer
			}
			if instructor, ok := googleLookupObject(object, "instructor"); ok {
				if label, present := googleLookup(instructor, "label"); present {
					description = "Istruttore: " + dto.PythonStr(label)
				}
			}
		}
	}
	endValue, present := googleLookup(event, "end")
	if !present {
		return nil, domain.ErrGoogleServer
	}
	var end *pydatetime.Moment
	allDay := false
	if text, isText := endValue.(string); endValue != nil && !(isText && text == "None") {
		if end, err = parse("end"); err != nil {
			return nil, err
		}
	} else {
		allDay = true
		if start == nil {
			return nil, domain.ErrGoogleServer
		}
		copied := *start
		copied.Hour, copied.Minute = 23, 59
		end = &copied
	}
	title, present := googleLookup(event, "title")
	if !present || start == nil || end == nil {
		return nil, domain.ErrGoogleServer
	}
	startRome, ok1 := start.InRome()
	endRome, ok2 := end.InRome()
	if !ok1 || !ok2 {
		return nil, domain.ErrGoogleServer // OverflowError
	}
	if startRome.Year() < 1000 || endRome.Year() < 1000 {
		return nil, errGoogleYearUnpadded
	}
	body := domain.RequestObject{
		{Name: "extendedProperties", Value: domain.RequestObject{{Name: "private", Value: domain.RequestObject{
			{Name: "course_id", Value: courseID}, {Name: "event_id", Value: identifier}}}}},
		{Name: "calendar_id", Value: calendarID},
		{Name: "summary", Value: title},
		{Name: "description", Value: description},
		{Name: "reminders", Value: domain.RequestObject{{Name: "useDefault", Value: false}, {Name: "overrides", Value: []any{
			domain.RequestObject{{Name: "method", Value: "email"}, {Name: "minutes", Value: json.Number("1440")}},
			domain.RequestObject{{Name: "method", Value: "popup"}, {Name: "minutes", Value: json.Number("10")}}}}}},
	}
	moment := func(at time.Time) domain.RequestObject {
		if allDay {
			return domain.RequestObject{{Name: "date", Value: at.Format("2006-01-02")}, {Name: "timeZone", Value: "Europe/Rome"}}
		}
		return domain.RequestObject{{Name: "dateTime", Value: at.Format("2006-01-02T15:04:05")}, {Name: "timeZone", Value: "Europe/Rome"}}
	}
	return append(body, domain.RequestField{Name: "start", Value: moment(startRome)}, domain.RequestField{Name: "end", Value: moment(endRome)}), nil
}

// djangoEnvironmentBool is django-environ's env.bool.
func djangoEnvironmentBool(value string) bool {
	switch strings.ToLower(value) {
	case "true", "on", "ok", "y", "yes", "1":
		return true
	}
	return false
}

// GoogleCalendarSettingsFrom reads core/settings.py's Google calendar
// settings and SESSION_COOKIE_SECURE (env.bool, default not DEBUG) from the
// process environment.
func GoogleCalendarSettingsFrom(getenv func(string) string) (GoogleCalendarSettings, bool) {
	secrets := getenv("GOOGLE_CLIENT_SECRET_FILE")
	if secrets == "" {
		secrets = "google_client_secret.json"
	}
	redirect, present := os.LookupEnv("GOOGLE_CALENDAR_REDIRECT_URL")
	if !present {
		redirect = getenv("CURRENT_HOST") + "/google/oauth2callback"
	} else {
		redirect = getenv("GOOGLE_CALENDAR_REDIRECT_URL")
	}
	secure := !djangoEnvironmentBool(getenv("DEBUG"))
	if value, present := os.LookupEnv("SESSION_COOKIE_SECURE"); present {
		secure = djangoEnvironmentBool(value)
	}
	return GoogleCalendarSettings{ClientSecretFile: secrets, RedirectURL: redirect, Environment: googleapi.Environment{
		InsecureTransport: getenv("OAUTHLIB_INSECURE_TRANSPORT") != "", RelaxTokenScope: getenv("OAUTHLIB_RELAX_TOKEN_SCOPE") != "",
		StrictTokenType: getenv("OAUTHLIB_STRICT_TOKEN_TYPE") != ""}}, secure
}

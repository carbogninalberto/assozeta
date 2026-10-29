package handler

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type paymentCategoryAdmissionContextKey struct{}

type paymentCategoryAccountsTest struct{}

func (paymentCategoryAccountsTest) ByID(ctx context.Context, _ string) (domain.Account, error) {
	if err := ctx.Err(); err != nil {
		return domain.Account{}, err
	}
	return domain.Account{
		ID: "00000000-0000-4000-8000-000000000101", Email: "owner@example.test",
		Role: domain.RoleAssociation, Active: true,
	}, nil
}

type paymentCategoryTokensTest struct{}

func (paymentCategoryTokensTest) Verify(string, string) (map[string]any, error) {
	return map[string]any{"user_id": "00000000-0000-4000-8000-000000000101"}, nil
}

type paymentCategoryServiceTest struct {
	mu sync.Mutex

	admissions       []string
	admissionRelease <-chan struct{}
	admissionErr     error

	mutationStarted chan struct{}
	mutationRelease chan struct{}
	mutationContext context.Context
	mutationAudit   domain.LoginAuditContext
	mutationCalls   int
	mutationApplied int

	listStarted chan struct{}
	listRelease chan struct{}
	listContext context.Context
}

func (s *paymentCategoryServiceTest) AdmitAdd(ctx context.Context, _ domain.ResolvedIdentity) error {
	return s.admit(ctx, "add")
}

func (s *paymentCategoryServiceTest) AdmitUpdate(ctx context.Context, _ domain.ResolvedIdentity, _ string) error {
	return s.admit(ctx, "update")
}

func (s *paymentCategoryServiceTest) admit(ctx context.Context, operation string) error {
	s.mu.Lock()
	s.admissions = append(s.admissions, operation)
	release, admissionErr := s.admissionRelease, s.admissionErr
	s.mu.Unlock()
	if release != nil {
		select {
		case <-release:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	if admissionErr != nil {
		return admissionErr
	}
	return nil
}

func (s *paymentCategoryServiceTest) Add(ctx context.Context, _ domain.ResolvedIdentity, _ dto.PaymentCategoryWrite, audit domain.LoginAuditContext) (dto.PaymentCategory, error) {
	return s.mutate(ctx, audit)
}

func (s *paymentCategoryServiceTest) Update(ctx context.Context, _ domain.ResolvedIdentity, _ string, _ dto.PaymentCategoryWrite, audit domain.LoginAuditContext) (dto.PaymentCategory, error) {
	return s.mutate(ctx, audit)
}

func (s *paymentCategoryServiceTest) Delete(ctx context.Context, _ domain.ResolvedIdentity, _ string, audit domain.LoginAuditContext) error {
	_, err := s.mutate(ctx, audit)
	return err
}

func (s *paymentCategoryServiceTest) mutate(ctx context.Context, audit domain.LoginAuditContext) (dto.PaymentCategory, error) {
	s.mu.Lock()
	s.mutationCalls++
	s.mutationContext = ctx
	s.mutationAudit = audit
	started, release := s.mutationStarted, s.mutationRelease
	s.mu.Unlock()
	if started != nil {
		close(started)
	}
	if release != nil {
		select {
		case <-release:
		case <-ctx.Done():
			return dto.PaymentCategory{}, ctx.Err()
		}
	}
	if err := ctx.Err(); err != nil {
		return dto.PaymentCategory{}, err
	}
	s.mu.Lock()
	s.mutationApplied++
	s.mu.Unlock()
	return dto.PaymentCategory{ID: "00000000-0000-4000-8000-000000000102", Name: "Admitted"}, nil
}

func (s *paymentCategoryServiceTest) List(ctx context.Context, _ domain.ResolvedIdentity) ([]dto.PaymentCategory, error) {
	s.mu.Lock()
	started, release := s.listStarted, s.listRelease
	s.listContext = ctx
	s.mu.Unlock()
	if started != nil {
		close(started)
	}
	if release != nil {
		<-release
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return nil, nil
}

func (s *paymentCategoryServiceTest) snapshot() (int, context.Context) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.mutationCalls, s.mutationContext
}

func newPaymentCategoryAdmissionHandler(categories *paymentCategoryServiceTest, auditRelease <-chan struct{}) (*PaymentCategoriesHandler, *paymentcategorylifecycle.Lifecycle) {
	audit := func(request *http.Request) (domain.LoginAuditContext, error) {
		if auditRelease != nil {
			<-auditRelease
		}
		email, _ := request.Context().Value(paymentCategoryAdmissionContextKey{}).(string)
		return domain.LoginAuditContext{ActorEmail: &email}, nil
	}
	accounts := paymentCategoryAccountsTest{}
	operations := paymentcategorylifecycle.New()
	return NewPaymentCategoriesHandler(
		categories,
		service.NewActorAuthentication(accounts, paymentCategoryTokensTest{}),
		service.NewIdentityResolver(accounts, nil, nil, nil, nil),
		audit,
		operations,
	), operations
}

func newPaymentCategoryAdmissionRequest(t *testing.T, method, path, body string) (echo.Context, *httptest.ResponseRecorder, context.CancelFunc) {
	t.Helper()
	request := httptest.NewRequest(method, path, strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer lifecycle-token")
	request.Header.Set("Content-Type", "application/json")
	ctx, cancel := context.WithCancel(context.WithValue(request.Context(), paymentCategoryAdmissionContextKey{}, "trace-admission"))
	request = request.WithContext(ctx)
	recorder := httptest.NewRecorder()
	c := echo.New().NewContext(request, recorder)
	return c, recorder, cancel
}

func TestPaymentCategoryAdmittedMutationsSurviveRequestCancellation(t *testing.T) {
	for _, tc := range []struct {
		name, method, path, body string
		invoke                   func(*PaymentCategoriesHandler, echo.Context) error
	}{
		{name: "add", method: http.MethodPost, path: "/payment/category/add", body: `{}`,
			invoke: func(h *PaymentCategoriesHandler, c echo.Context) error { return h.Add(c) }},
		{name: "update", method: http.MethodPatch, path: "/payment/category/00000000-0000-4000-8000-000000000102/update", body: `{}`,
			invoke: func(h *PaymentCategoriesHandler, c echo.Context) error { return h.Update(c) }},
		{name: "delete", method: http.MethodDelete, path: "/payment/category/00000000-0000-4000-8000-000000000102/delete", body: "",
			invoke: func(h *PaymentCategoriesHandler, c echo.Context) error { return h.Delete(c) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			release := make(chan struct{})
			service := &paymentCategoryServiceTest{
				mutationStarted: make(chan struct{}),
				mutationRelease: release,
			}
			h, _ := newPaymentCategoryAdmissionHandler(service, nil)
			c, recorder, cancelRequest := newPaymentCategoryAdmissionRequest(t, tc.method, tc.path, tc.body)
			defer cancelRequest()
			if tc.name != "add" {
				c.SetParamNames("uid")
				c.SetParamValues("00000000-0000-4000-8000-000000000102")
			}
			done := make(chan error, 1)
			go func() { done <- tc.invoke(h, c) }()
			select {
			case <-service.mutationStarted:
			case <-time.After(time.Second):
				t.Fatal("admitted mutation did not start")
			}
			service.mu.Lock()
			operationCtx := service.mutationContext
			mutationAudit := service.mutationAudit
			service.mu.Unlock()
			if got := operationCtx.Value(paymentCategoryAdmissionContextKey{}); got != "trace-admission" {
				t.Fatalf("operation context lost tracing value: %v", got)
			}
			if mutationAudit.ActorEmail == nil || *mutationAudit.ActorEmail != "trace-admission" {
				t.Fatalf("mutation lost request audit values: %+v", mutationAudit)
			}
			cancelRequest()
			<-c.Request().Context().Done()
			if err := operationCtx.Err(); err != nil {
				t.Fatalf("request cancellation reached admitted mutation: %v", err)
			}
			close(release)
			select {
			case err := <-done:
				if err != nil {
					t.Fatalf("admitted mutation failed: %v", err)
				}
			case <-time.After(time.Second):
				t.Fatal("admitted mutation did not drain")
			}
			if recorder.Code != http.StatusOK {
				t.Fatalf("admitted mutation response: %d %s", recorder.Code, recorder.Body.String())
			}
			if calls, _ := service.snapshot(); calls != 1 {
				t.Fatalf("mutation calls=%d, want 1", calls)
			}
			service.mu.Lock()
			applied := service.mutationApplied
			service.mu.Unlock()
			if applied != 1 {
				t.Fatalf("mutation effects=%d, want 1", applied)
			}
			service.mu.Lock()
			admissions := append([]string(nil), service.admissions...)
			service.mu.Unlock()
			switch tc.name {
			case "add":
				if len(admissions) != 1 || admissions[0] != "add" {
					t.Fatalf("Add admission order: %v", admissions)
				}
			case "update":
				if len(admissions) != 1 || admissions[0] != "update" {
					t.Fatalf("Update admission order: %v", admissions)
				}
			case "delete":
				if len(admissions) != 0 {
					t.Fatalf("Delete unexpectedly admitted via body service: %v", admissions)
				}
			}
		})
	}
}

func TestPaymentCategoryDrainExpiryCancelsEveryAdmittedMutation(t *testing.T) {
	for _, tc := range []struct {
		name, method, path, body string
		invoke                   func(*PaymentCategoriesHandler, echo.Context) error
	}{
		{name: "add", method: http.MethodPost, path: "/payment/category/add", body: `{}`,
			invoke: func(h *PaymentCategoriesHandler, c echo.Context) error { return h.Add(c) }},
		{name: "update", method: http.MethodPatch, path: "/payment/category/00000000-0000-4000-8000-000000000102/update", body: `{}`,
			invoke: func(h *PaymentCategoriesHandler, c echo.Context) error { return h.Update(c) }},
		{name: "delete", method: http.MethodDelete, path: "/payment/category/00000000-0000-4000-8000-000000000102/delete", body: "",
			invoke: func(h *PaymentCategoriesHandler, c echo.Context) error { return h.Delete(c) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			release := make(chan struct{})
			defer close(release)
			service := &paymentCategoryServiceTest{
				mutationStarted: make(chan struct{}),
				mutationRelease: release,
			}
			h, operations := newPaymentCategoryAdmissionHandler(service, nil)
			c, _, cancelRequest := newPaymentCategoryAdmissionRequest(t, tc.method, tc.path, tc.body)
			defer cancelRequest()
			if tc.name != "add" {
				c.SetParamNames("uid")
				c.SetParamValues("00000000-0000-4000-8000-000000000102")
			}
			done := make(chan error, 1)
			go func() { done <- tc.invoke(h, c) }()
			select {
			case <-service.mutationStarted:
			case <-time.After(time.Second):
				t.Fatal("admitted mutation did not start")
			}
			service.mu.Lock()
			operationContext := service.mutationContext
			service.mu.Unlock()

			operations.StopAdmission()
			if operationContext.Err() != nil {
				t.Fatalf("graceful drain canceled admitted mutation: %v", operationContext.Err())
			}
			operations.Expire()
			select {
			case <-done:
			case <-time.After(time.Second):
				t.Fatal("drain expiry did not cancel admitted mutation")
			}
			if !errors.Is(operationContext.Err(), context.Canceled) {
				t.Fatalf("mutation context error = %v", operationContext.Err())
			}
			if calls, _ := service.snapshot(); calls != 1 {
				t.Fatalf("mutation calls=%d, want 1", calls)
			}
			service.mu.Lock()
			applied := service.mutationApplied
			service.mu.Unlock()
			if applied != 0 {
				t.Fatalf("expired mutation applied %d effects", applied)
			}
			waitContext, cancelWait := context.WithTimeout(context.Background(), time.Second)
			defer cancelWait()
			if err := operations.Wait(waitContext); err != nil {
				t.Fatalf("handler did not release expired mutation: %v", err)
			}
		})
	}
}

func TestPaymentCategoryDoesNotStartMutationAfterAdmissionStops(t *testing.T) {
	service := &paymentCategoryServiceTest{}
	h, operations := newPaymentCategoryAdmissionHandler(service, nil)
	operations.StopAdmission()
	c, recorder, cancelRequest := newPaymentCategoryAdmissionRequest(t, http.MethodPost, "/payment/category/add", `{}`)
	defer cancelRequest()

	if err := h.Add(c); err != nil {
		t.Fatal(err)
	}
	if calls, _ := service.snapshot(); calls != 0 {
		t.Fatalf("mutation started after admission stopped: %d", calls)
	}
	if recorder.Code != http.StatusInternalServerError {
		t.Fatalf("post-stop admission response: %d %s", recorder.Code, recorder.Body.String())
	}
}

func TestPaymentCategoryCancellationBeforeMutationDispatchDoesNotMutate(t *testing.T) {
	admissionRelease := make(chan struct{})
	service := &paymentCategoryServiceTest{admissionRelease: admissionRelease}
	h, _ := newPaymentCategoryAdmissionHandler(service, nil)
	c, recorder, cancelRequest := newPaymentCategoryAdmissionRequest(t, http.MethodPost, "/payment/category/add", `{}`)
	defer cancelRequest()
	done := make(chan error, 1)
	go func() { done <- h.Add(c) }()

	deadline := time.Now().Add(time.Second)
	for {
		service.mu.Lock()
		admitted := len(service.admissions) == 1
		service.mu.Unlock()
		if admitted {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("Add service admission did not start")
		}
		time.Sleep(time.Millisecond)
	}
	cancelRequest()
	<-c.Request().Context().Done()
	close(admissionRelease)
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("pre-dispatch cancellation did not finish")
	}
	if calls, _ := service.snapshot(); calls != 0 {
		t.Fatalf("pre-dispatch cancellation invoked %d mutations", calls)
	}
	if recorder.Code != http.StatusInternalServerError {
		t.Fatalf("pre-dispatch cancellation response: %d %s", recorder.Code, recorder.Body.String())
	}
}

func TestPaymentCategoryDrainExpiryCancelsAndWaitsForAdmissionCleanup(t *testing.T) {
	admissionRelease := make(chan struct{})
	service := &paymentCategoryServiceTest{admissionRelease: admissionRelease}
	h, operations := newPaymentCategoryAdmissionHandler(service, nil)
	c, recorder, cancelRequest := newPaymentCategoryAdmissionRequest(t, http.MethodPost, "/payment/category/add", `{}`)
	defer cancelRequest()
	done := make(chan error, 1)
	go func() { done <- h.Add(c) }()

	deadline := time.Now().Add(time.Second)
	for {
		service.mu.Lock()
		admitted := len(service.admissions) == 1
		service.mu.Unlock()
		if admitted {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("Add service admission did not start")
		}
		time.Sleep(time.Millisecond)
	}
	operations.StopAdmission()
	operations.Expire()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("drain expiry did not cancel pre-admission cleanup")
	}
	if calls, _ := service.snapshot(); calls != 0 {
		t.Fatalf("pre-admission expiry invoked %d mutations", calls)
	}
	if recorder.Code != http.StatusInternalServerError {
		t.Fatalf("pre-admission expiry response: %d %s", recorder.Code, recorder.Body.String())
	}
	waitContext, cancelWait := context.WithTimeout(context.Background(), time.Second)
	defer cancelWait()
	if err := operations.Wait(waitContext); err != nil {
		t.Fatalf("handler did not release pre-admission cleanup: %v", err)
	}
	close(admissionRelease)
}

func TestPaymentCategoryListRemainsRequestCancelable(t *testing.T) {
	release := make(chan struct{})
	service := &paymentCategoryServiceTest{listStarted: make(chan struct{}), listRelease: release}
	h, _ := newPaymentCategoryAdmissionHandler(service, nil)
	c, recorder, cancelRequest := newPaymentCategoryAdmissionRequest(t, http.MethodGet, "/payment/category/list", "")
	defer cancelRequest()
	done := make(chan error, 1)
	go func() { done <- h.List(c) }()
	select {
	case <-service.listStarted:
	case <-time.After(time.Second):
		t.Fatal("LIST did not start")
	}
	cancelRequest()
	<-c.Request().Context().Done()
	close(release)
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("cancelled LIST did not finish")
	}
	if recorder.Code != http.StatusInternalServerError {
		t.Fatalf("cancelled LIST response: %d %s", recorder.Code, recorder.Body.String())
	}
	service.mu.Lock()
	listContext := service.listContext
	service.mu.Unlock()
	if !errors.Is(listContext.Err(), context.Canceled) {
		t.Fatalf("LIST did not retain request cancellation: %v", listContext.Err())
	}
}

func TestPaymentCategoryLifecycleRejectsCancelledContextBeforeAdmission(t *testing.T) {
	lifecycle := paymentcategorylifecycle.New()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	operation, err := lifecycle.Track(ctx)
	if !errors.Is(err, context.Canceled) || operation != nil {
		t.Fatalf("cancelled context accepted: %v %v", operation, err)
	}
}

package service

import (
	"context"
	"errors"
	"io"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/djangoauth"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

// InstanceSuperuserError is a CommandError: the command prints
// "CommandError: <message>" and exits 1 without changing any row.
type InstanceSuperuserError struct{ Message string }

func (e *InstanceSuperuserError) Error() string { return e.Message }

// ErrInstanceSuperuserCancelled is EOFError/KeyboardInterrupt while prompting.
var ErrInstanceSuperuserCancelled = errors.New("account creation cancelled")

type InstanceSuperuserStore interface {
	Exists(ctx context.Context, field, value string) (bool, error)
	Create(ctx context.Context, user domain.InstanceSuperuser, now func() time.Time) error
}

type InstanceSuperuserHasher interface {
	Encode(ctx context.Context, password string) (string, error)
}

// InstanceSuperuserPrompts is input() and getpass.getpass(); io.EOF or
// ErrInstanceSuperuserCancelled cancel the command.
type InstanceSuperuserPrompts interface {
	Input(label string) (string, error)
	Password(prompt string) (string, error)
}

// InstanceSuperuserOptions are the parsed command options. A nil value is
// an option that was not given.
type InstanceSuperuserOptions struct {
	Username, Email, FirstName, LastName, Phone *string
	NoInput, AllowWeakPassword                  bool
	// EnvironmentPassword is DJANGO_SUPERUSER_PASSWORD ("" when unset).
	EnvironmentPassword string
	DeploymentMode      string
}

type InstanceSuperusers struct {
	store  InstanceSuperuserStore
	hashes InstanceSuperuserHasher
	now    func() time.Time
}

func NewInstanceSuperusers(store InstanceSuperuserStore, hashes InstanceSuperuserHasher, now func() time.Time) *InstanceSuperusers {
	return &InstanceSuperusers{store: store, hashes: hashes, now: now}
}

func commandError(message string) error { return &InstanceSuperuserError{Message: message} }

// Create is create_instance_superuser's handle(), in Django's order. It
// returns the stored username for the success message.
func (s *InstanceSuperusers) Create(ctx context.Context, options InstanceSuperuserOptions, prompts InstanceSuperuserPrompts) (string, error) {
	if options.AllowWeakPassword && options.DeploymentMode != "development" {
		return "", commandError("Weak test passwords are allowed only in development.")
	}
	cancelled := commandError("Account creation cancelled.")
	fields := []struct {
		name, label string
		value       *string
	}{
		{"username", "Username", options.Username}, {"email", "Email", options.Email},
		{"first_name", "First name (optional)", options.FirstName}, {"last_name", "Last name (optional)", options.LastName},
		{"phone", "Phone (optional)", options.Phone},
	}
	values := make([]djangoauth.UserField, len(fields))
	for index, field := range fields {
		value := ""
		if field.value != nil {
			value = *field.value
		} else if !options.NoInput {
			answer, err := prompts.Input(field.label + ": ")
			if errors.Is(err, io.EOF) || errors.Is(err, ErrInstanceSuperuserCancelled) {
				return "", cancelled
			}
			if err != nil {
				return "", err
			}
			value = answer
		}
		values[index] = djangoauth.UserField{Name: field.name, Value: djangoauth.PythonStrip(value)}
	}
	values[0].Value = djangoauth.PythonUpper(values[0].Value)
	username, email := values[0].Value, values[1].Value
	if username == "" || email == "" {
		return "", commandError("Username and email are required.")
	}
	if message, ok := djangoauth.ValidateEmail(email); !ok {
		return "", commandError(message)
	}
	for _, field := range values[:2] {
		exists, err := s.store.Exists(ctx, field.Name, field.Value)
		if err != nil {
			return "", err
		}
		if exists {
			return "", commandError("An account with this " + field.Name + " already exists; no account was changed.")
		}
	}
	for _, field := range values {
		if messages := djangoauth.CleanUserField(field); len(messages) > 0 {
			return "", commandError(strings.Join(messages, "; "))
		}
	}
	password := options.EnvironmentPassword
	if !options.NoInput {
		first, err := prompts.Password("Password: ")
		if err == nil {
			var again string
			again, err = prompts.Password("Password (again): ")
			if err == nil && again != first {
				return "", commandError("Passwords do not match.")
			}
		}
		if errors.Is(err, io.EOF) || errors.Is(err, ErrInstanceSuperuserCancelled) {
			return "", cancelled
		}
		if err != nil {
			return "", err
		}
		password = first
	}
	if password == "" {
		return "", commandError("A nonempty password is required.")
	}
	if !options.AllowWeakPassword {
		messages, err := djangoauth.ValidatePassword(password, djangoauth.UserAttributes{
			Username: username, FirstName: values[2].Value, LastName: values[3].Value, Email: email})
		if err != nil {
			return "", err
		}
		if len(messages) > 0 {
			return "", commandError(strings.Join(messages, "; "))
		}
	}
	hash, err := s.hashes.Encode(ctx, password)
	if err != nil {
		return "", err
	}
	user := domain.InstanceSuperuser{UserID: uuid.NewString(), Username: djangoauth.NormalizeUsername(username),
		Email: djangoauth.NormalizeEmail(email), FirstName: values[2].Value, LastName: values[3].Value, Phone: values[4].Value,
		PasswordHash: hash}
	if err = s.store.Create(ctx, user, s.now); err != nil {
		if errors.Is(err, domain.ErrInstanceSuperuserIntegrity) {
			return "", commandError("An account with these details already exists; no account was changed.")
		}
		return "", err
	}
	return user.Username, nil
}

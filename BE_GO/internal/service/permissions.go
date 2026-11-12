package service

import (
	"context"
	"fmt"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type Permissions struct{}

func NewPermissions() *Permissions { return &Permissions{} }

// CheckCollaborator implements only Django's collaborator registry gate. The
// caller must first resolve the trusted effective user and its active tenant
// owner. A successful result never substitutes for resource, instructor, family,
// group or action-specific authorization. Version prefixes belong to the router.
func (p *Permissions) CheckCollaborator(ctx context.Context, subject domain.PermissionSubject, method, path string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if !subject.Collaborator {
		return nil
	}
	path = strings.TrimPrefix(strings.Trim(path, "/"), "api/")
	if method == "GET" && (path == "manuale/sections" || strings.HasPrefix(path, "manuale/assets/")) {
		return nil
	}
	if subject.CollaboratorRole == domain.CollaboratorFull {
		return nil
	}
	for _, pattern := range collaboratorExclusions {
		if matchPermissionPath(path, pattern) {
			return nil
		}
	}
	required := requiredPermission(path, method)
	if required == "" {
		return &domain.PermissionDenied{Detail: fmt.Sprintf("Access denied: No permission mapping for endpoint '%s'. Contact administrator to grant access.", path)}
	}
	for _, granted := range subject.Grants {
		if granted == required {
			return nil
		}
	}
	return &domain.PermissionDenied{Detail: "Missing permission: " + required}
}

func requiredPermission(path, method string) string {
	// Method-specific declarations take precedence over every generic declaration.
	for _, rule := range collaboratorRules {
		if rule.Method != "" && rule.Method == method && matchPermissionPath(path, rule.Path) {
			return rule.Permission
		}
	}
	for _, rule := range collaboratorRules {
		if rule.Method == "" && matchPermissionPath(path, rule.Path) {
			return rule.Permission
		}
	}
	return ""
}
func matchPermissionPath(path, pattern string) bool {
	if path == "" || pattern == "" {
		return false
	}
	parts := strings.FieldsFunc(path, func(r rune) bool { return r == '/' })
	expected := strings.FieldsFunc(pattern, func(r rune) bool { return r == '/' })
	if len(parts) != len(expected) {
		return false
	}
	for i, part := range parts {
		if expected[i] != "*" && expected[i] != part {
			return false
		}
	}
	return true
}

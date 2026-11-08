package dto

// CollaboratorRole is int(data.get('collaborator_role', User.COLLABORATOR))
// followed by the view's fallback: a role other than FULL (1), ONLY_ACCOUNTING
// (2) or CUSTOM_COLLABORATOR_ROLE (3) becomes FULL. ok is false when int()
// raises (500).
func CollaboratorRole(value any, present bool) (role int64, ok bool) {
	if !present {
		return 3, true
	}
	number, result := PythonInt(value)
	if result != PythonIntOK {
		return 0, false
	}
	if number.IsInt64() {
		if role = number.Int64(); role >= 1 && role <= 3 {
			return role, true
		}
	}
	return 1, true
}

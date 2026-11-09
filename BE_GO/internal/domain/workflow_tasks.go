package domain

import "github.com/google/uuid"

// WorkflowTaskWorkflow is an AutomationWorkflow row as the workflow Celery
// tasks load it: the key, the association foreign key and the
// automation_tree JSON text.
type WorkflowTaskWorkflow struct {
	ID          uuid.UUID
	Association uuid.UUID
	Tree        string
}

// WorkflowTaskBlockWorkflow is AutomationWorkflow with
// select_related('sport_association', 'sport_association__user').
type WorkflowTaskBlockWorkflow struct {
	WorkflowTaskWorkflow
	OwnerID    uuid.UUID
	OwnerEmail string
}

// WorkflowTaskSubscription is Subscription with select_related('associate',
// 'user'): the associate columns are nil without an associate.
type WorkflowTaskSubscription struct {
	ID             uuid.UUID
	UserID         uuid.UUID
	UserEmail      string
	AssociateID    *uuid.UUID
	AssociateEmail *string
	AssociateMinor bool
}

// WorkflowTaskTag is a Tags row (tag_id, tag_name).
type WorkflowTaskTag struct {
	ID   uuid.UUID
	Name string
}

// WorkflowTaskCronFilter is the target filter AutomationWorkflow.trigger
// adds to the cron subscriptions: a status flag with archived=False,
// archived=True, or a tag (tags__tag_id OR tags__tag_name__icontains).
type WorkflowTaskCronFilter struct {
	Status   *int
	Archived *bool
	Tag      *WorkflowTaskTag
}

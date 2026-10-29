package repository

import (
	"context"
	"database/sql"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type ModuleTemplates struct{ db *sqlx.DB }

func NewModuleTemplates(db *sqlx.DB) *ModuleTemplates { return &ModuleTemplates{db: db} }

var _ domain.ModuleTemplateStore = (*ModuleTemplates)(nil)

const moduleTemplateColumns = `sport_association_module_templates_id,sport_association_id,name,template,footer,header,
custom_footer_header,custom_footer,custom_header`

func (r *ModuleTemplates) ActiveAssociationForUser(ctx context.Context, user string) (string, error) {
	return messageReadsAssociation(ctx, r.db, user)
}

func (r *ModuleTemplates) ModuleTemplates(ctx context.Context, association string) ([]domain.ModuleTemplate, error) {
	rows := []domain.ModuleTemplate{}
	err := r.db.SelectContext(ctx, &rows, `SELECT `+moduleTemplateColumns+` FROM application_sportassociationmoduletemplates
 WHERE sport_association_id=$1`, association)
	return rows, err
}

func (r *ModuleTemplates) BeginModuleTemplateMutations(ctx context.Context) (domain.ModuleTemplateTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &moduleTemplateTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

// Reuse the shared owned transaction and active-association lookup.
type moduleTemplateTransaction struct{ *paymentCategoryTransaction }

var _ domain.ModuleTemplateTransaction = (*moduleTemplateTransaction)(nil)

// ModuleTemplateIDExists is the primary-key UniqueValidator across associations.
func (t *moduleTemplateTransaction) ModuleTemplateIDExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_sportassociationmoduletemplates
 WHERE sport_association_module_templates_id=$1)`, id)
	return exists, err
}

func (t *moduleTemplateTransaction) InsertModuleTemplate(ctx context.Context, row domain.ModuleTemplate) error {
	_, err := t.tx.NamedExecContext(ctx, `INSERT INTO application_sportassociationmoduletemplates (`+moduleTemplateColumns+`)
 VALUES (:sport_association_module_templates_id,:sport_association_id,:name,:template,:footer,:header,
 :custom_footer_header,:custom_footer,:custom_header)`, row)
	return err
}

func (t *moduleTemplateTransaction) DeleteModuleTemplates(ctx context.Context, association string, ids []string) ([]domain.ModuleTemplate, error) {
	rows := []domain.ModuleTemplate{}
	err := t.tx.SelectContext(ctx, &rows, `DELETE FROM application_sportassociationmoduletemplates
 WHERE sport_association_id=$1 AND sport_association_module_templates_id=ANY($2::uuid[]) RETURNING `+moduleTemplateColumns,
		association, ids)
	return rows, err
}

// Creates resolve through the registered direct resolver; after a delete the
// row is gone and resolution falls back to the changes JSON.
func (t *moduleTemplateTransaction) AppendModuleTemplateAudit(ctx context.Context, event domain.ModuleTemplateAudit) error {
	path := "direct"
	if event.Action == 2 {
		path = "changes_json.sport_association"
	}
	row := event.Template
	values := map[string]string{"sport_association_module_templates_id": row.ID, "sport_association": row.AssociationID,
		"name": row.Name, "footer": pythonBool(row.Footer), "header": pythonBool(row.Header),
		"custom_footer_header": pythonBool(row.CustomFooterHeader)}
	for name, value := range map[string]*string{"template": row.Template, "custom_footer": row.CustomFooter, "custom_header": row.CustomHeader} {
		if value != nil {
			values[name] = *value
		}
	}
	changes := make(map[string][2]string, len(values))
	for name, value := range values {
		if event.Action == 0 {
			changes[name] = [2]string{"None", value}
		} else {
			changes[name] = [2]string{value, "None"}
		}
	}
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "sportassociationmoduletemplates", Key: row.ID,
		Representation: "SportAssociationModuleTemplates object (" + row.ID + ")", Action: event.Action, Changes: changes,
		Context: event.Context, AssociationID: &row.AssociationID, ResolutionPath: &path, At: event.At})
}

func pythonBool(value bool) string {
	if value {
		return "True"
	}
	return "False"
}

package dto

import (
	"encoding/json"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// StaffBoardMessageRow follows StaffBoardMessageSerializer field order.
type StaffBoardMessageRow struct {
	StaffBoardMessageID string          `json:"staff_board_message_id"`
	SportAssociation    string          `json:"sport_association"`
	Author              *string         `json:"author"`
	AuthorName          string          `json:"author_name"`
	AuthorAvatar        *string         `json:"author_avatar"`
	IsOwner             bool            `json:"is_owner"`
	Document            json.RawMessage `json:"document"`
	Content             string          `json:"content"`
	Pinned              bool            `json:"pinned"`
	CreatedAt           string          `json:"created_at"`
	UpdatedAt           string          `json:"updated_at"`
}

// StaffBoardMessageRowFrom projects a row for the acting user (message_actor:
// the acting collaborator, not the effective owner).
func StaffBoardMessageRowFrom(message domain.StaffBoardMessage, actorID string) StaffBoardMessageRow {
	row := StaffBoardMessageRow{StaffBoardMessageID: message.ID, SportAssociation: message.AssociationID,
		AuthorName: "Utente eliminato", Document: message.Document, Content: message.Content, Pinned: message.Pinned,
		CreatedAt: paymentCategoryResponseTime(message.CreatedAt), UpdatedAt: paymentCategoryResponseTime(message.UpdatedAt)}
	if author := message.Author; author != nil {
		id := author.ID
		row.Author, row.AuthorAvatar, row.IsOwner = &id, author.AvatarImage, author.ID == actorID
		switch name := pythonStrip(author.FirstName + " " + author.LastName); {
		case name != "":
			row.AuthorName = name
		case message.AssociationOwnerID == author.ID:
			row.AuthorName = message.AssociationDenomination
		default:
			row.AuthorName = author.Username
		}
	}
	return row
}

package domain

import (
	"encoding/json"
	"time"
)

// StaffBoardAuthor is the joined author row; nil on StaffBoardMessage means the
// author was deleted (author_id SET_NULL).
type StaffBoardAuthor struct {
	ID, FirstName, LastName, Username string
	AvatarImage                       *string
}

// StaffBoardMessage is one communications_staffboardmessage row with the
// association owner and denomination needed for author_name.
type StaffBoardMessage struct {
	ID, AssociationID                           string
	AssociationOwnerID, AssociationDenomination string
	Author                                      *StaffBoardAuthor
	Content                                     string
	Document                                    json.RawMessage // nil is SQL/JSON null
	Pinned                                      bool
	CreatedAt, UpdatedAt                        time.Time
}

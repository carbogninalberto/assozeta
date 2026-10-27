package dto

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestStaffBoardMessageRowCorpus(t *testing.T) {
	raw, err := os.ReadFile("../../../TESTS/fixtures/staff-board-responses.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Format int `json:"format"`
		Cases  []struct {
			Name   string `json:"name"`
			Record struct {
				ID          string `json:"id"`
				Association struct {
					ID           string `json:"id"`
					OwnerID      string `json:"owner_id"`
					Denomination string `json:"denomination"`
				} `json:"association"`
				Author *struct {
					ID          string  `json:"id"`
					FirstName   string  `json:"first_name"`
					LastName    string  `json:"last_name"`
					Username    string  `json:"username"`
					AvatarImage *string `json:"avatar_image"`
				} `json:"author"`
				Content   string          `json:"content"`
				Document  json.RawMessage `json:"document"`
				Pinned    bool            `json:"pinned"`
				CreatedAt string          `json:"created_at"`
				UpdatedAt string          `json:"updated_at"`
			} `json:"record"`
			ActorID string          `json:"actor_id"`
			Expect  json.RawMessage `json:"expect"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &corpus); err != nil || corpus.Format != 1 || len(corpus.Cases) < 9 {
		t.Fatal("corpus", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			r := c.Record
			created, err1 := time.Parse(time.RFC3339Nano, r.CreatedAt)
			updated, err2 := time.Parse(time.RFC3339Nano, r.UpdatedAt)
			if err1 != nil || err2 != nil {
				t.Fatal(err1, err2)
			}
			message := domain.StaffBoardMessage{ID: r.ID, AssociationID: r.Association.ID,
				AssociationOwnerID: r.Association.OwnerID, AssociationDenomination: r.Association.Denomination,
				Content: r.Content, Pinned: r.Pinned, CreatedAt: created, UpdatedAt: updated}
			if string(r.Document) != "null" {
				message.Document = r.Document
			}
			if r.Author != nil {
				message.Author = &domain.StaffBoardAuthor{ID: r.Author.ID, FirstName: r.Author.FirstName,
					LastName: r.Author.LastName, Username: r.Author.Username, AvatarImage: r.Author.AvatarImage}
			}
			got, err := json.Marshal(StaffBoardMessageRowFrom(message, c.ActorID))
			if err != nil {
				t.Fatal(err)
			}
			var gotValue, wantValue any
			if json.Unmarshal(got, &gotValue) != nil || json.Unmarshal(c.Expect, &wantValue) != nil || !reflect.DeepEqual(gotValue, wantValue) {
				t.Fatalf("row\n got %s\nwant %s", got, c.Expect)
			}
		})
	}
}

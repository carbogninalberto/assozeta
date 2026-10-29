package dto

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

// FolderNode is FolderSerializer with its recursive children.
type FolderNode struct {
	ID            int64        `json:"id"`
	Name          string       `json:"name"`
	Parent        *int64       `json:"parent"`
	AssociationID string       `json:"sport_association"`
	CreatedAt     string       `json:"created_at"`
	Level         int          `json:"level"`
	Children      []FolderNode `json:"children"`
}

type FolderCrumb struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
}

type FolderList struct {
	Data []FolderNode  `json:"data"`
	Path []FolderCrumb `json:"path"`
}

// FolderNodes renders the nodes whose parent is parent (nil for roots), with
// children nested in tree order, from rows already in tree order.
func FolderNodes(rows []domain.Folder, parent *int64) []FolderNode {
	children := map[int64][]domain.Folder{}
	roots := []domain.Folder{}
	for _, row := range rows {
		if row.ParentID == nil {
			roots = append(roots, row)
		} else {
			children[*row.ParentID] = append(children[*row.ParentID], row)
		}
	}
	var build func([]domain.Folder) []FolderNode
	build = func(level []domain.Folder) []FolderNode {
		nodes := make([]FolderNode, 0, len(level))
		for _, row := range level {
			nodes = append(nodes, FolderNode{ID: row.ID, Name: row.Name, Parent: row.ParentID, AssociationID: row.AssociationID,
				CreatedAt: DRFUTCDateTime(row.CreatedAt), Level: row.Level, Children: build(children[row.ID])})
		}
		return nodes
	}
	if parent == nil {
		return build(roots)
	}
	return build(children[*parent])
}

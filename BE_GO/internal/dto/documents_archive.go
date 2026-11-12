package dto

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

type ArchiveDocument struct {
	ID           string  `json:"document_id"`
	CreationDate string  `json:"creation_date"`
	Filepath     *string `json:"filepath"`
	Filename     string  `json:"filename"`
	FileSize     *int64  `json:"file_size_bytes"`
	Token        string  `json:"token"`
}

type ArchiveRow struct {
	ID            string          `json:"sport_association_documents_archive_id"`
	AssociationID string          `json:"sport_association"`
	Document      ArchiveDocument `json:"document"`
	Date          string          `json:"date"`
	Folder        *int64          `json:"folder"`
}

type ArchivePagination struct {
	Page       int64 `json:"page"`
	TotalPages int64 `json:"total_pages"`
	TotalItems int64 `json:"total_items"`
	HasNext    bool  `json:"has_next"`
}

type ArchiveList struct {
	Data       []ArchiveRow      `json:"data"`
	Pagination ArchivePagination `json:"pagination"`
}

func ArchiveRowFrom(row domain.ArchivedDocument) ArchiveRow {
	return ArchiveRow{ID: row.ID, AssociationID: row.AssociationID, Date: row.Date.Format("2006-01-02"), Folder: row.FolderID,
		Document: ArchiveDocument{ID: row.DocumentID, CreationDate: DRFUTCDateTime(row.CreationDate), Filepath: row.Filepath,
			Filename: row.Filename, FileSize: row.FileSize, Token: row.Token}}
}

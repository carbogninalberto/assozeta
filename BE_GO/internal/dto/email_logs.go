package dto

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

type EmailLogRow struct {
	ID        string  `json:"email_log_id"`
	Recipient *string `json:"recipient"`
	Subject   string  `json:"subject"`
	SentAt    string  `json:"sent_at"`
	Result    string  `json:"result"`
}

type EmailLogsList struct {
	TodayCount int           `json:"today_count"`
	TotalCount int           `json:"total_count"`
	Results    []EmailLogRow `json:"results"`
}

func EmailLogsListFrom(rows []domain.EmailLogRecord, today int) EmailLogsList {
	list := EmailLogsList{TodayCount: today, TotalCount: len(rows), Results: make([]EmailLogRow, 0, len(rows))}
	for _, row := range rows {
		list.Results = append(list.Results, EmailLogRow{ID: row.ID, Recipient: row.Recipient, Subject: row.Subject,
			SentAt: paymentCategoryResponseTime(row.SentAt), Result: row.Result})
	}
	return list
}

package repository

import (
	"context"
	"errors"
)

// CheckSchema validates the Django-owned columns this scheduler actually reads
// before initializing a loop. It never migrates, seeds, inspects every source,
// or infers ownership from an empty candidate page. Operational history is
// checked separately through Jobs.CheckSchema.
func (r *ScheduleCandidates) CheckSchema(ctx context.Context) error {
	checks := []struct {
		query string
		types []string
	}{
		{`SELECT id,name,task,enabled,last_run_at,date_changed,start_time,total_run_count,
 interval_id,crontab_id,solar_id,clocked_id,args,kwargs,headers,queue,exchange,routing_key,
 priority,expires,expire_seconds,one_off FROM public.django_celery_beat_periodictask LIMIT 0`,
			[]string{"INT4", "STRING", "STRING", "BOOL", "TIMESTAMPTZ", "TIMESTAMPTZ", "TIMESTAMPTZ", "INT4",
				"INT4", "INT4", "INT4", "INT4", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING",
				"INT4", "TIMESTAMPTZ", "INT4", "BOOL"}},
		{`SELECT id,every,period FROM public.django_celery_beat_intervalschedule LIMIT 0`,
			[]string{"INT4", "INT4", "STRING"}},
		{`SELECT id,minute,hour,day_of_week,day_of_month,month_of_year,timezone FROM public.django_celery_beat_crontabschedule LIMIT 0`,
			[]string{"INT4", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING"}},
		{`SELECT state FROM public.instance_datarestore LIMIT 0`, []string{"STRING"}},
	}
	for _, check := range checks {
		rows, err := r.db.QueryxContext(ctx, check.query)
		if err != nil {
			return err
		}
		columns, typeErr := rows.ColumnTypes()
		closeErr := rows.Close()
		if typeErr != nil {
			return typeErr
		}
		if closeErr != nil {
			return closeErr
		}
		if len(columns) != len(check.types) {
			return errors.New("incompatible periodic source schema")
		}
		for index, expected := range check.types {
			actual := columns[index].DatabaseTypeName()
			if actual != expected && !(expected == "STRING" && (actual == "TEXT" || actual == "VARCHAR")) {
				return errors.New("incompatible periodic source schema")
			}
		}
	}
	return nil
}

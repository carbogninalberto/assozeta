package config

import "time"

// When Django loads settings, django/conf/__init__.py sets os.environ["TZ"]
// to self.TIME_ZONE and calls time.tzset(). BE/core/settings.py sets TIME_ZONE
// to UTC, so naive datetime.now() and date.today() use the UTC wall clock on any host.
// Every Go command that serves Django's behavior imports config, so pinning
// time.Local here gives those processes the same zone. Production images already
// run in UTC, so this only affects hosts configured for another time zone.
func init() {
	time.Local = time.UTC
}

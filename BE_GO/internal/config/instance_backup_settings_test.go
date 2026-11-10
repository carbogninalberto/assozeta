package config

import "testing"

// Expected values follow backups.upload_max_bytes and remote_backups'
// ASSOZETA_BACKUP_REMOTE_ALLOW_HTTP check for the same environment values.
func TestInstanceAdminBackupSettings(t *testing.T) {
	for value, want := range map[string]int64{"": DefaultBackupUploadMaxBytes, " 4096 ": 4096, "\t12\n": 12, "0": DefaultBackupUploadMaxBytes,
		"-1": DefaultBackupUploadMaxBytes, "1e3": DefaultBackupUploadMaxBytes, "1_000": DefaultBackupUploadMaxBytes,
		"٣": DefaultBackupUploadMaxBytes, "007": 7, "99999999999999999999999": 1<<63 - 1} {
		settings, err := parseInstanceAdmin(map[string]string{"ASSOZETA_BACKUP_UPLOAD_MAX_BYTES": value})
		if err != nil || settings.BackupUploadMaxBytes != want {
			t.Errorf("%q: got %d %v, want %d", value, settings.BackupUploadMaxBytes, err, want)
		}
	}
	for value, want := range map[string]bool{"": false, "1": true, " TRUE ": true, "yes": true, "On": true, "0": false, "no": false, "y": false} {
		settings, err := parseInstanceAdmin(map[string]string{"ASSOZETA_BACKUP_REMOTE_ALLOW_HTTP": value})
		if err != nil || settings.BackupRemoteAllowHTTP != want {
			t.Errorf("%q: got %v %v", value, settings.BackupRemoteAllowHTTP, err)
		}
	}
	settings, _ := parseInstanceAdmin(map[string]string{})
	if settings.BackupUploadMaxBytes != 50<<30 || settings.BackupRemoteAllowHTTP {
		t.Fatalf("defaults: %+v", settings)
	}
}

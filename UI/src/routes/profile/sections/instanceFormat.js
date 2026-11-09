// Shared formatting for the instance owner pages: one date, size and freshness style.
const DAY = 86400000;

export function formatSize(bytes) {
    if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return '—';
    const units = [['GB', 1024 ** 3], ['MB', 1024 ** 2], ['KB', 1024]];
    const [unit, divisor] = units.find(([, size]) => bytes >= size) || units[2];
    return `${new Intl.NumberFormat('it-IT', {maximumFractionDigits: unit === 'GB' ? 1 : 0}).format(bytes / divisor)} ${unit}`;
}

export function formatDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString('it-IT', {day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'});
}

export function formatRelative(value, now = Date.now()) {
    if (!value) return '';
    const elapsed = new Date(value).getTime() - now;
    if (Number.isNaN(elapsed)) return '';
    const format = new Intl.RelativeTimeFormat('it-IT', {numeric: 'auto'});
    const minutes = Math.round(elapsed / 60000);
    if (Math.abs(minutes) < 1) return 'adesso';
    if (Math.abs(minutes) < 60) return format.format(minutes, 'minute');
    const hours = Math.round(minutes / 60);
    if (Math.abs(hours) < 24) return format.format(hours, 'hour');
    return format.format(Math.round(hours / 24), 'day');
}

// The newest archive that can actually be restored: any origin counts.
export function latestBackup(archives = []) {
    return archives.reduce((latest, archive) => (!latest || (archive.created_at || '') > (latest.created_at || '') ? archive : latest), null);
}

// "ok" within the expected interval, "stale" when overdue, "missing" when there is none.
// Without a schedule the owner is expected to back up at least weekly.
export function backupFreshness(archive, schedule = null, now = Date.now()) {
    if (!archive?.created_at) return 'missing';
    const interval = schedule?.enabled ? (schedule.frequency === 'weekly' ? 7 * DAY : DAY) : 7 * DAY;
    // One hour of grace covers a scheduled run that is still in progress.
    return now - new Date(archive.created_at).getTime() > interval + 3600000 ? 'stale' : 'ok';
}

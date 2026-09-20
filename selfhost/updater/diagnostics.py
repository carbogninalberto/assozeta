"""Read-only host evidence for backup preparation. No backup or restore is triggered."""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import time


def backup_readiness(root):
    def result(status, message):
        return {'backup': {'status': status, 'level': 'configuration', 'message': message}}
    try:
        root = Path(root)
        directory = root / 'backups'
        target = directory if directory.exists() else root
        if target.is_symlink() or not target.is_dir() or not os.access(target, os.W_OK | os.X_OK):
            return result('failed', 'La cartella dei backup non è disponibile. Verifica i permessi sul server.')
        free = shutil.disk_usage(target).free
        # Only the normal backup directory is inspected, never arbitrary paths.
        archives = []
        if directory.exists():
            for index, path in enumerate(directory.iterdir()):
                if index >= 1000:
                    return result('warning', 'Troppi file nella cartella backup. Verifica spazio e politica di conservazione sul server.')
                if path.name.startswith('assozeta-') and path.name.endswith('.tar.gz') and not path.is_symlink() and path.is_file():
                    archives.append(path)
        latest = max(archives, key=lambda item: item.stat().st_mtime) if archives else None
        if free < max(512 * 1024 * 1024, latest.stat().st_size * 2 if latest else 0):
            return result('warning', 'Spazio libero insufficiente per la soglia prudenziale (512 MB o due volte l’ultimo archivio). Libera spazio prima di aggiornare.')
        if not latest:
            return result('warning', 'Cartella e spazio disponibili, ma nessun backup rilevato nella cartella standard. Crea un backup e prova il ripristino prima di aggiornare.')
        info = latest.stat()
        with latest.open('rb') as stream:
            if stream.read(2) != b'\x1f\x8b':
                return result('failed', 'L’ultimo archivio non ha un’intestazione gzip valida. Crea e verifica un nuovo backup.')
        stamp = datetime.fromtimestamp(info.st_mtime, timezone.utc).strftime('%d/%m/%Y %H:%M UTC')
        stale = time.time() - info.st_mtime > 7 * 86400
        return result('warning' if stale else 'passed',
                      f'Archivio rilevato: {stamp}. ' + ('Ha più di 7 giorni. ' if stale else '') +
                      'Permessi e soglia di spazio verificati in lettura. Integrità completa, spazio effettivo necessario e ripristino non verificati.')
    except (OSError, ValueError):
        return result('failed', 'Impossibile verificare la preparazione dei backup. Controlla cartella e spazio sul server.')

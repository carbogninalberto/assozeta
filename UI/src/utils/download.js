export function downloadFile(filename, base64, filetype = 'text/csv') {
    const a = document.createElement("a");
    a.href = `data:${filetype};base64,${base64}`;
    a.download = filename;
    a.click();
    a.remove();
}

export function downloadCSV(filename, base64) {
    downloadFile(filename, base64, 'text/csv');
}

export function downloadPdf(url) {
    const token = localStorage.getItem('sessionToken').replaceAll('"', '');
    window.open(url + '?download=true&token=' + token, '_blank');
}

export function downloadZip(filename, base64) {
    downloadFile(filename, base64, 'application/zip');
}

export function tryDownloadFile(res, filetype = 'text/csv') {
    if (res?.response?.data) {
        downloadFile(res.response.data.filename, res.response.data.file, filetype);
    } else {
        window.Swal?.fire({
            icon: 'error',
            title: 'Errore',
            text: 'Impossibile esportare i dati',
        });
    }
}

export function tryDownloadCSV(res) {
    tryDownloadFile(res, 'text/csv');
}

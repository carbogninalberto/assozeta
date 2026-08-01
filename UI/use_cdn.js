import * as fs from 'fs';

function setFileVersion(filename){
    const cdnBaseUrl = process.env.CDN_BASE_URL;
    if (!cdnBaseUrl) {
        console.info('CDN_BASE_URL not set, skipping CDN rewrite.');
        return;
    }

    fs.readFile(filename, 'utf-8', function(err, data){
        if (err) throw err;
        var newValue = data;
        newValue = newValue.replaceAll("/static/assets/", `${cdnBaseUrl}/static/assets/`);
        newValue = newValue.replaceAll("/static/css/", `${cdnBaseUrl}/static/css/`);
        newValue = newValue.replaceAll("/build-assets/", `${cdnBaseUrl}/build-assets/`);
        fs.writeFile(filename, newValue, 'utf-8', function (err) {
            if (err) throw err;
        });
    });

}

setFileVersion('./dist/public/index.html');
console.info("index.html updated.");

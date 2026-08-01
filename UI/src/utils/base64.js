const _keyStr = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=";

function _utf8_encode(e) {
    e = e.replace(/\r\n/g, "\n");
    let t = "";
    for (let n = 0; n < e.length; n++) {
        const r = e.charCodeAt(n);
        if (r < 128) {
            t += String.fromCharCode(r);
        } else if (r > 127 && r < 2048) {
            t += String.fromCharCode(r >> 6 | 192);
            t += String.fromCharCode(r & 63 | 128);
        } else {
            t += String.fromCharCode(r >> 12 | 224);
            t += String.fromCharCode(r >> 6 & 63 | 128);
            t += String.fromCharCode(r & 63 | 128);
        }
    }
    return t;
}

function _utf8_decode(e) {
    let t = "";
    let n = 0;
    while (n < e.length) {
        const r = e.charCodeAt(n);
        if (r < 128) {
            t += String.fromCharCode(r);
            n++;
        } else if (r > 191 && r < 224) {
            const c2 = e.charCodeAt(n + 1);
            t += String.fromCharCode((r & 31) << 6 | c2 & 63);
            n += 2;
        } else {
            const c2 = e.charCodeAt(n + 1);
            const c3 = e.charCodeAt(n + 2);
            t += String.fromCharCode((r & 15) << 12 | (c2 & 63) << 6 | c3 & 63);
            n += 3;
        }
    }
    return t;
}

export function encode(e) {
    let t = "";
    e = _utf8_encode(e);
    let f = 0;
    while (f < e.length) {
        const n = e.charCodeAt(f++);
        const r = e.charCodeAt(f++);
        const i = e.charCodeAt(f++);
        const s = n >> 2;
        const o = (n & 3) << 4 | r >> 4;
        const u = (r & 15) << 2 | i >> 6;
        const a = i & 63;
        const uVal = isNaN(r) ? 64 : u;
        const aVal = isNaN(r) || isNaN(i) ? 64 : a;
        t = t + _keyStr.charAt(s) + _keyStr.charAt(o) + _keyStr.charAt(uVal) + _keyStr.charAt(aVal);
    }
    return t;
}

export function decode(e) {
    let t = "";
    e = e.replace(/[^A-Za-z0-9\+\/\=]/g, "");
    let f = 0;
    while (f < e.length) {
        const s = _keyStr.indexOf(e.charAt(f++));
        const o = _keyStr.indexOf(e.charAt(f++));
        const u = _keyStr.indexOf(e.charAt(f++));
        const a = _keyStr.indexOf(e.charAt(f++));
        const n = s << 2 | o >> 4;
        const r = (o & 15) << 4 | u >> 2;
        const i = (u & 3) << 6 | a;
        t = t + String.fromCharCode(n);
        if (u != 64) {
            t = t + String.fromCharCode(r);
        }
        if (a != 64) {
            t = t + String.fromCharCode(i);
        }
    }
    t = _utf8_decode(t);
    return t;
}

export default { encode, decode };

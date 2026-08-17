export function waitForElementAndExecute(selector, callback, options = {}) {
    const query = options.query || (value => document.querySelector(value));
    const schedule = options.schedule || (next => requestAnimationFrame(next));

    return new Promise(resolve => {
        function checkElement() {
            const element = query(selector);
            if (element) {
                resolve(element);
            } else {
                schedule(checkElement);
            }
        }

        checkElement();
    }).then(element => {
        if (element.isConnected === false) {
            return waitForElementAndExecute(selector, callback, {query, schedule});
        }

        return callback(element);
    });
}

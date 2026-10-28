import {EventEmitter} from 'node:events';
import fs from 'node:fs/promises';

export class PoolUnavailableError extends Error {
    constructor(message) {
        super(message);
        this.name = 'PoolUnavailableError';
        this.statusCode = 503;
    }
}

async function readFirst(paths) {
    for (const path of paths) {
        try {
            const value = (await fs.readFile(path, 'utf8')).trim();
            if (value) return value;
        } catch (_) {
            // The cgroup layout differs between Docker hosts and local runs.
        }
    }
    return '';
}

export async function readContainerMemory() {
    const currentText = await readFirst([
        '/sys/fs/cgroup/memory.current',
        '/sys/fs/cgroup/memory/memory.usage_in_bytes',
    ]);
    const limitText = await readFirst([
        '/sys/fs/cgroup/memory.max',
        '/sys/fs/cgroup/memory/memory.limit_in_bytes',
    ]);
    const statText = await readFirst([
        '/sys/fs/cgroup/memory.stat',
        '/sys/fs/cgroup/memory/memory.stat',
    ]);
    const stat = new Map(
        statText.split('\n')
            .map(line => line.trim().split(/\s+/))
            .filter(parts => parts.length === 2)
            .map(([key, value]) => [key, Number.parseInt(value, 10)]),
    );
    const current = Number.parseInt(currentText, 10);
    const anonymous = stat.get('anon');
    const kernel = stat.get('kernel');
    const used = Number.isFinite(anonymous)
        ? anonymous + (Number.isFinite(kernel) ? kernel : 0)
        : current;
    const limit = Number.parseInt(limitText, 10);
    return {
        usedBytes: Number.isFinite(used) && used > 0 ? used : null,
        currentBytes: Number.isFinite(current) && current > 0 ? current : null,
        limitBytes: Number.isFinite(limit) && limit > 0 ? limit : null,
    };
}

function withTimeout(promise, timeoutMs, message) {
    let timer;
    const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new PoolUnavailableError(message)), timeoutMs);
        timer.unref();
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

export class BrowserPool extends EventEmitter {
    constructor({
        launchBrowser,
        size = 2,
        warmSize = 1,
        acquireTimeoutMs = 30000,
        launchTimeoutMs = 30000,
        browserMaxAgeMs = 10 * 60 * 1000,
        browserMaxRequests = 100,
        memoryLimitBytes = null,
    }) {
        super();
        this.launchBrowser = launchBrowser;
        this.size = Math.max(1, size);
        this.warmSize = Math.min(this.size, Math.max(1, warmSize));
        this.acquireTimeoutMs = acquireTimeoutMs;
        this.launchTimeoutMs = launchTimeoutMs;
        this.browserMaxAgeMs = browserMaxAgeMs;
        this.browserMaxRequests = browserMaxRequests;
        this.memoryLimitBytes = memoryLimitBytes;
        this.resources = new Set();
        this.idle = [];
        this.waiters = [];
        this.launching = 0;
        this.leases = 0;
        this.shuttingDown = false;
        this.lastMemory = {usedBytes: null, currentBytes: null, limitBytes: memoryLimitBytes};
        this.metrics = {
            launched: 0,
            renders: 0,
            destroyed: 0,
            recycled: 0,
            launchFailures: 0,
            acquireTimeouts: 0,
        };
    }

    async warmUp() {
        let firstError;
        while (this.resources.size + this.launching < this.warmSize) {
            try {
                const resource = await this._launch();
                this.idle.push(resource);
            } catch (error) {
                firstError ||= error;
                break;
            }
        }
        if (this.resources.size === 0) {
            throw firstError || new PoolUnavailableError('No renderer browser could be started');
        }
    }

    async acquire({timeoutMs = this.acquireTimeoutMs} = {}) {
        const deadline = Date.now() + timeoutMs;

        while (true) {
            if (this.shuttingDown) {
                throw new PoolUnavailableError('Renderer is shutting down');
            }

            const resource = await this._takeAvailable();
            if (resource) return this._lease(resource);

            if (this.resources.size + this.launching < this.size) {
                const remaining = deadline - Date.now();
                if (remaining <= 0) break;
                try {
                    const launched = await this._launch(Math.min(remaining, this.launchTimeoutMs));
                    return this._lease(launched);
                } catch (error) {
                    if (Date.now() >= deadline) {
                        this.metrics.acquireTimeouts++;
                        break;
                    }
                    throw error;
                }
            }

            const remaining = deadline - Date.now();
            if (remaining <= 0) break;
            await this._wait(remaining);
        }

        this.metrics.acquireTimeouts++;
        throw new PoolUnavailableError('Renderer pool acquisition timed out');
    }

    release(resource, {countRender = true, destroy = false} = {}) {
        if (!resource || !resource.leased) return;
        resource.leased = false;
        this.leases--;
        if (countRender) {
            resource.requests++;
            this.metrics.renders++;
        }

        if (
            destroy
            || this.shuttingDown
            || !resource.connected
            || this._expired(resource)
        ) {
            if (!destroy && resource.connected && this._expired(resource)) {
                this.metrics.recycled++;
            }
            void this._destroy(resource);
        } else {
            this.idle.push(resource);
        }
        this._signal();
    }

    async refreshMemory() {
        this.lastMemory = await readContainerMemory();
        if (this.memoryLimitBytes) {
            this.lastMemory.limitBytes = this.memoryLimitBytes;
        }
        return this.lastMemory;
    }

    async probeBrowser(callback) {
        let leased;
        let resource = [...this.resources].find(item => item.connected && !item.destroying);
        if (!resource) {
            leased = await this.acquire();
            resource = leased;
        }
        try {
            return await callback(resource.browser);
        } finally {
            if (leased) this.release(leased, {countRender: false});
        }
    }

    getStats() {
        return {
            browserCount: this.resources.size,
            idle: this.idle.length,
            leased: this.leases,
            waiting: this.waiters.length,
            launching: this.launching,
            max: this.size,
            memory: {...this.lastMemory},
            metrics: {...this.metrics},
        };
    }

    async drain({timeoutMs = 15000} = {}) {
        this.shuttingDown = true;
        this._rejectWaiters();

        try {
            await this._waitFor(() => this.leases === 0 && this.launching === 0, timeoutMs);
        } catch (_) {
            // Force-close remaining browsers if an in-flight request does not stop.
        }

        await Promise.all([...this.resources].map(resource => this._destroy(resource)));
    }

    async _launch(timeoutMs = this.launchTimeoutMs) {
        if (this.shuttingDown) {
            throw new PoolUnavailableError('Renderer is shutting down');
        }

        this.launching++;
        this._signal();
        try {
            const memory = await this.refreshMemory();
            if (
                memory.usedBytes
                && memory.limitBytes
                && memory.usedBytes >= memory.limitBytes * 0.9
            ) {
                throw new PoolUnavailableError('Renderer container memory limit is nearly exhausted');
            }
            const launch = this.launchBrowser();
            let browser;
            try {
                browser = await withTimeout(
                    launch,
                    timeoutMs,
                    'Renderer browser launch timed out',
                );
            } catch (error) {
                launch.then(lateBrowser => lateBrowser.close(), () => {});
                throw error;
            }
            const resource = {
                browser,
                connected: true,
                createdAt: Date.now(),
                requests: 0,
                leased: false,
                destroying: false,
            };
            browser.on('disconnected', () => {
                resource.connected = false;
                this._removeIdle(resource);
                this.resources.delete(resource);
                this._signal();
            });
            if (this.shuttingDown) {
                await browser.close().catch(() => {});
                throw new PoolUnavailableError('Renderer is shutting down');
            }
            this.resources.add(resource);
            this.metrics.launched++;
            return resource;
        } catch (error) {
            this.metrics.launchFailures++;
            if (error instanceof PoolUnavailableError) throw error;
            const unavailable = new PoolUnavailableError(`Renderer browser launch failed: ${error.message}`);
            unavailable.cause = error;
            throw unavailable;
        } finally {
            this.launching--;
            this._signal();
        }
    }

    async _takeAvailable() {
        while (true) {
            const resource = this.idle.pop();
            if (!resource) return null;
            if (resource.connected && !this._expired(resource)) return resource;
            if (resource.connected) this.metrics.recycled++;
            await this._destroy(resource);
        }
    }

    _lease(resource) {
        resource.leased = true;
        this.leases++;
        return resource;
    }

    _expired(resource) {
        return (
            Date.now() - resource.createdAt >= this.browserMaxAgeMs
            || resource.requests >= this.browserMaxRequests
        );
    }

    _removeIdle(resource) {
        const index = this.idle.indexOf(resource);
        if (index !== -1) this.idle.splice(index, 1);
    }

    async _destroy(resource) {
        if (!resource || resource.destroying) return;
        resource.destroying = true;
        this._removeIdle(resource);
        this.resources.delete(resource);
        this.metrics.destroyed++;
        if (resource.connected) {
            await withTimeout(
                resource.browser.close(),
                5000,
                'Renderer browser shutdown timed out',
            ).catch(() => {});
        }
        this._signal();
    }

    _wait(timeoutMs) {
        return new Promise(resolve => {
            const waiter = {resolve, timer: null};
            waiter.timer = setTimeout(() => {
                const index = this.waiters.indexOf(waiter);
                if (index !== -1) this.waiters.splice(index, 1);
                resolve();
            }, timeoutMs);
            this.waiters.push(waiter);
        });
    }

    _waitFor(predicate, timeoutMs) {
        return new Promise((resolve, reject) => {
            let timer;
            const cleanup = () => {
                clearTimeout(timer);
                this.off('state', onState);
            };
            const onState = () => {
                if (predicate()) {
                    cleanup();
                    resolve();
                }
            };
            const onTimeout = () => {
                cleanup();
                if (predicate()) resolve();
                else reject(new Error('Waiting for renderer pool timed out'));
            };
            timer = setTimeout(onTimeout, timeoutMs);
            timer.unref();
            this.on('state', onState);
            onState();
        });
    }

    _signal() {
        this.emit('state');
        const waiter = this.waiters.shift();
        if (waiter) {
            clearTimeout(waiter.timer);
            waiter.resolve();
        }
    }

    _rejectWaiters() {
        for (const waiter of this.waiters.splice(0)) {
            clearTimeout(waiter.timer);
            waiter.resolve();
        }
    }
}

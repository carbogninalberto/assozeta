export const HEARTBEAT_INTERVAL_MS = 25000;
export const HEARTBEAT_TIMEOUT_MS = 10000;
export const HEARTBEAT_TIMEOUT_CLOSE_CODE = 4000;

/** Application-level JSON heartbeat for browser WebSockets. */
export default class WebSocketHeartbeat {
    constructor(getSocket, options = {}) {
        this.getSocket = getSocket;
        this.intervalMs = options.intervalMs || HEARTBEAT_INTERVAL_MS;
        this.timeoutMs = options.timeoutMs || HEARTBEAT_TIMEOUT_MS;
        this.interval = null;
        this.timeout = null;
        this.pendingTimestamp = null;
        this.visibilityHandler = () => {
            if (document.visibilityState === 'visible') this.ping();
        };
    }

    start() {
        this.stop();
        document.addEventListener('visibilitychange', this.visibilityHandler);
        this.ping();
        this.interval = setInterval(() => this.ping(), this.intervalMs);
    }

    stop() {
        if (this.interval) clearInterval(this.interval);
        if (this.timeout) clearTimeout(this.timeout);
        this.interval = null;
        this.timeout = null;
        this.pendingTimestamp = null;
        document.removeEventListener('visibilitychange', this.visibilityHandler);
    }

    ping() {
        const socket = this.getSocket();
        if (document.visibilityState === 'hidden' || socket?.readyState !== WebSocket.OPEN) return;

        if (this.timeout) clearTimeout(this.timeout);
        const timestamp = Date.now();
        this.pendingTimestamp = timestamp;
        socket.send(JSON.stringify({type: 'ping', timestamp}));
        this.timeout = setTimeout(() => {
            this.timeout = null;
            if (document.visibilityState === 'hidden') return;
            const currentSocket = this.getSocket();
            if (currentSocket?.readyState === WebSocket.OPEN) {
                currentSocket.close(HEARTBEAT_TIMEOUT_CLOSE_CODE, 'Heartbeat timeout');
            }
        }, this.timeoutMs);
    }

    handlePong(message) {
        if (message?.type !== 'pong') return false;
        if (message.timestamp === this.pendingTimestamp) {
            if (this.timeout) clearTimeout(this.timeout);
            this.timeout = null;
            this.pendingTimestamp = null;
        }
        return true;
    }
}

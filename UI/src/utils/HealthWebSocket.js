import {getWebSocketUrl} from './websocketUrl.js';

/**
 * HealthWebSocket - Application health status via WebSocket
 *
 * Replaces deprecated REST endpoint:
 * - GET /health
 *
 * No authentication required - health endpoint is public.
 * Health status is sent automatically on connect.
 *
 * @example
 * import HealthWebSocket from './HealthWebSocket';
 *
 * const ws = new HealthWebSocket();
 * ws.setOnHealth((health) => {
 *   console.log(`Status: ${health.status}, Version: ${health.version}`);
 * });
 * ws.connect();
 */

class HealthWebSocket {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 3;
        this.reconnectDelay = 2000;

        this.onHealth = null;
        this.onError = null;
        this.onConnect = null;
        this.onDisconnect = null;
    }

    /**
     * Connect to the health WebSocket
     */
    connect() {
        const wsPath = __bakney.env.WS.HEALTH;
        const url = getWebSocketUrl(wsPath);

        this.ws = new WebSocket(url);

        this.ws.onopen = () => {
            console.log('[HealthWebSocket] Connected');
            this.reconnectAttempts = 0;
            this.onConnect?.();
            // Health status is sent automatically on connect
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (error) {
                console.error('[HealthWebSocket] Failed to parse message:', error);
            }
        };

        this.ws.onclose = (event) => {
            console.log('[HealthWebSocket] Closed:', event.code);
            this.onDisconnect?.(event.code);

            // Attempt reconnection
            this.attemptReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('[HealthWebSocket] Error:', error);
            this.onError?.('Connection error');
        };
    }

    /**
     * Handle incoming WebSocket messages
     * @private
     */
    handleMessage(data) {
        switch (data.type) {
            case 'health':
                this.onHealth?.({
                    status: data.status,
                    msg: data.msg,
                    version: data.version,
                });
                break;

            case 'error':
                console.error('[HealthWebSocket] Server error:', data.message);
                this.onError?.(data.message);
                break;

            default:
                console.warn('[HealthWebSocket] Unknown message type:', data.type);
        }
    }

    /**
     * Attempt to reconnect with exponential backoff
     * @private
     */
    attemptReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.error('[HealthWebSocket] Max reconnection attempts reached');
            this.onError?.('Max reconnection attempts reached');
            return;
        }

        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

        console.log(`[HealthWebSocket] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
        setTimeout(() => this.connect(), delay);
    }

    /**
     * Send a message to the WebSocket server
     * @private
     */
    send(data) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
        } else {
            console.warn('[HealthWebSocket] Cannot send - WebSocket not open');
        }
    }

    // Public API methods

    /**
     * Request a health check
     */
    check() {
        this.send({ type: 'check' });
    }

    // Event handlers

    /**
     * Set handler for health status updates
     * @param {function({status: string, msg: string, version: string}): void} handler
     */
    setOnHealth(handler) {
        this.onHealth = handler;
    }

    /**
     * Set handler for errors
     * @param {function(string): void} handler - (errorMessage) => {}
     */
    setOnError(handler) {
        this.onError = handler;
    }

    /**
     * Set handler for connection established
     * @param {function(): void} handler - () => {}
     */
    setOnConnect(handler) {
        this.onConnect = handler;
    }

    /**
     * Set handler for disconnection
     * @param {function(number): void} handler - (closeCode) => {}
     */
    setOnDisconnect(handler) {
        this.onDisconnect = handler;
    }

    /**
     * Disconnect from the WebSocket
     */
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }

    /**
     * Check if WebSocket is connected
     * @returns {boolean}
     */
    isConnected() {
        return this.ws?.readyState === WebSocket.OPEN;
    }

    /**
     * Reset reconnection attempts counter
     * Call this before manually triggering a reconnect
     */
    resetReconnectAttempts() {
        this.reconnectAttempts = 0;
    }
}

export default HealthWebSocket;

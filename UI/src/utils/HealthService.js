/**
 * HealthService - Singleton service for monitoring application health via WebSocket
 *
 * This service wraps HealthWebSocket and provides a simple API for health monitoring.
 *
 * @example
 * import healthService from './HealthService';
 *
 * // Initialize
 * healthService.init((health) => {
 *   console.log(`App status: ${health.status}`);
 * });
 *
 * // Request a health check
 * healthService.check();
 *
 * // Disconnect
 * healthService.disconnect();
 */

import HealthWebSocket from './HealthWebSocket.js';

class HealthService {
    constructor() {
        this.ws = null;
        this.isInitialized = false;
        this.onHealthCallback = null;
        this.onOfflineCallback = null;
        this.lastHealth = null;
        this.visibilityHandler = null;
    }

    /**
     * Initialize the health service
     * @param {function({status: string, msg: string, version: string}): void} onHealth - Health status callback
     * @param {function(): void} [onOffline] - Called when connection is lost
     */
    init(onHealth, onOffline = null) {
        if (this.isInitialized) {
            console.warn('[HealthService] Already initialized');
            return;
        }

        this.onHealthCallback = onHealth;
        this.onOfflineCallback = onOffline;

        this.ws = new HealthWebSocket();

        this.ws.setOnHealth((health) => {
            this.lastHealth = health;
            this.onHealthCallback?.(health);
        });

        this.ws.setOnDisconnect(() => {
            this.onOfflineCallback?.();
        });

        this.ws.setOnError((error) => {
            console.error('[HealthService] Error:', error);
            this.onOfflineCallback?.();
        });

        this.ws.connect();
        this.isInitialized = true;

        // Add visibility change handler to reconnect when tab becomes visible
        this.visibilityHandler = () => {
            if (document.visibilityState === 'visible' && !this.isConnected()) {
                console.log('[HealthService] Tab visible, attempting reconnect...');
                this.ws.resetReconnectAttempts();
                this.ws.connect();
            }
        };
        document.addEventListener('visibilitychange', this.visibilityHandler);
    }

    /**
     * Request a health check
     */
    check() {
        if (!this.ws) {
            console.warn('[HealthService] Not initialized');
            return;
        }
        this.ws.check();
    }

    /**
     * Get the last known health status
     * @returns {{status: string, msg: string, version: string} | null}
     */
    getLastHealth() {
        return this.lastHealth;
    }

    /**
     * Check if service is connected
     * @returns {boolean}
     */
    isConnected() {
        return this.ws?.isConnected() || false;
    }

    /**
     * Disconnect the WebSocket
     */
    disconnect() {
        if (this.visibilityHandler) {
            document.removeEventListener('visibilitychange', this.visibilityHandler);
            this.visibilityHandler = null;
        }
        if (this.ws) {
            this.ws.disconnect();
            this.ws = null;
        }
        this.isInitialized = false;
    }

    /**
     * Reconnect
     */
    reconnect() {
        if (this.ws) {
            this.ws.resetReconnectAttempts();
            this.ws.disconnect();
            this.ws.connect();
        }
    }
}

// Export singleton instance
const healthService = new HealthService();
export default healthService;

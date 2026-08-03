import {getWebSocketUrl} from './websocketUrl.js';

/**
 * NotificationWebSocket - Real-time notification management via WebSocket
 *
 * Replaces deprecated REST endpoints:
 * - GET /notifications/all/
 * - POST /notifications/<id>/read/
 * - POST /notifications/all/read/
 *
 * @example
 * import NotificationWebSocket from './NotificationWebSocket';
 *
 * const ws = new NotificationWebSocket();
 * ws.setOnNotifications((notifications, unread) => {
 *   console.log('Notifications:', notifications, 'Unread:', unread);
 * });
 * ws.setOnNewNotification((notification) => {
 *   console.log('New notification:', notification);
 * });
 * ws.connect();
 */

class NotificationWebSocket {
    constructor(token = null) {
        this.ws = null;
        this.token = token;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;

        this.onNotifications = null;
        this.onNewNotification = null;
        this.onReadConfirmed = null;
        this.onAllReadConfirmed = null;
        this.onError = null;
        this.onConnect = null;
        this.onDisconnect = null;
    }

    /**
     * Connect to the notifications WebSocket
     */
    connect() {
        const wsPath = __bakney.env.WS.NOTIFICATIONS;
        const baseUrl = getWebSocketUrl(wsPath);
        const url = this.token ? `${baseUrl}?token=${this.token}` : baseUrl;

        this.ws = new WebSocket(url);

        this.ws.onopen = () => {
            console.log('[NotificationWebSocket] Connected');
            this.reconnectAttempts = 0;
            this.onConnect?.();
            // Fetch initial notifications
            this.fetch();
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (error) {
                console.error('[NotificationWebSocket] Failed to parse message:', error);
            }
        };

        this.ws.onclose = (event) => {
            console.log('[NotificationWebSocket] Closed:', event.code);
            this.onDisconnect?.(event.code);

            // Don't reconnect if auth failed (4001)
            if (event.code === 4001) {
                console.error('[NotificationWebSocket] Authentication failed');
                this.onError?.('Authentication failed');
                return;
            }

            // Attempt reconnection for other close codes
            this.attemptReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('[NotificationWebSocket] Error:', error);
            this.onError?.('Connection error');
        };
    }

    /**
     * Handle incoming WebSocket messages
     * @private
     */
    handleMessage(data) {
        switch (data.type) {
            case 'notifications':
                this.onNotifications?.(data.data, data.unread);
                break;

            case 'notification_push':
                this.onNewNotification?.(data.notification);
                break;

            case 'read_confirmed':
                console.log('[NotificationWebSocket] Notification marked as read:', data.id);
                this.onReadConfirmed?.(data.id);
                break;

            case 'all_read_confirmed':
                console.log('[NotificationWebSocket] All notifications marked as read');
                this.onAllReadConfirmed?.();
                break;

            case 'error':
                console.error('[NotificationWebSocket] Server error:', data.message);
                this.onError?.(data.message);
                break;

            default:
                console.warn('[NotificationWebSocket] Unknown message type:', data.type);
        }
    }

    /**
     * Attempt to reconnect with exponential backoff
     * @private
     */
    attemptReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.error('[NotificationWebSocket] Max reconnection attempts reached');
            this.onError?.('Max reconnection attempts reached');
            return;
        }

        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

        console.log(`[NotificationWebSocket] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
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
            console.warn('[NotificationWebSocket] Cannot send - WebSocket not open');
        }
    }

    // Public API methods

    /**
     * Fetch all notifications
     */
    fetch() {
        this.send({ type: 'fetch' });
    }

    /**
     * Mark a single notification as read
     * @param {string} id - Notification UUID
     */
    markRead(id) {
        this.send({ type: 'mark_read', id });
    }

    /**
     * Mark all notifications as read
     */
    markAllRead() {
        this.send({ type: 'mark_all_read' });
    }

    // Event handlers

    /**
     * Set handler for receiving all notifications
     * @param {function(Array, number): void} handler - (notifications, unreadCount) => {}
     */
    setOnNotifications(handler) {
        this.onNotifications = handler;
    }

    /**
     * Set handler for new notification push
     * @param {function(Object): void} handler - (notification) => {}
     */
    setOnNewNotification(handler) {
        this.onNewNotification = handler;
    }

    /**
     * Set handler for read confirmation
     * @param {function(string): void} handler - (notificationId) => {}
     */
    setOnReadConfirmed(handler) {
        this.onReadConfirmed = handler;
    }

    /**
     * Set handler for all read confirmation
     * @param {function(): void} handler - () => {}
     */
    setOnAllReadConfirmed(handler) {
        this.onAllReadConfirmed = handler;
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

export default NotificationWebSocket;

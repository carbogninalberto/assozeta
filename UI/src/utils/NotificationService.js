/**
 * NotificationService - Singleton service for managing notifications via WebSocket
 *
 * This service wraps NotificationWebSocket and integrates with Svelte stores.
 * It should be initialized once when the user is authenticated.
 *
 * @example
 * import notificationService from './NotificationService';
 * import { notifications, unreadNotificationsCounter } from 'store/stores.js';
 *
 * // Initialize when user logs in
 * notificationService.init(notifications, unreadNotificationsCounter);
 *
 * // Mark a notification as read
 * notificationService.markRead(notificationId);
 *
 * // Mark all as read
 * notificationService.markAllRead();
 *
 * // Disconnect when user logs out
 * notificationService.disconnect();
 */

import NotificationWebSocket from './NotificationWebSocket.js';
import {get} from 'svelte/store';
import {apiFetch} from './ApiMiddleware.js';
import {exportProgress} from 'store/exportProgressStore.js';

class NotificationService {
    constructor() {
        this.ws = null;
        this.notificationsStore = null;
        this.unreadCounterStore = null;
        this.isInitialized = false;
        this.onNewNotificationCallback = null;
        this.visibilityHandler = null;
        this.activeExportSync = null;
    }

    /**
     * Initialize the notification service
     * @param {import('svelte/store').Writable} notificationsStore - The notifications store
     * @param {import('svelte/store').Writable} unreadCounterStore - The unread counter store
     * @param {string} [token] - Optional JWT token for authentication (if not using cookies)
     */
    init(notificationsStore, unreadCounterStore, token = null) {
        if (this.isInitialized) {
            console.warn('[NotificationService] Already initialized');
            return;
        }

        this.notificationsStore = notificationsStore;
        this.unreadCounterStore = unreadCounterStore;

        this.ws = new NotificationWebSocket(token);

        // Handle receiving all notifications
        this.ws.setOnNotifications((notifications, unreadCount) => {
            this.notificationsStore.set(notifications);
            this.unreadCounterStore.set(unreadCount);
        });

        // Handle new notification push
        this.ws.setOnNewNotification(notification => {
            const currentNotifications = get(this.notificationsStore) || [];
            this.notificationsStore.set([notification, ...currentNotifications]);

            const currentUnread = get(this.unreadCounterStore) || 0;
            this.unreadCounterStore.set(currentUnread + 1);

            // Call external callback if set
            this.onNewNotificationCallback?.(notification);
        });

        this.ws.setOnExportProgress(event => exportProgress.applyProgress(event));
        this.ws.setOnExportCompleted(event => exportProgress.applyCompleted(event));
        this.ws.setOnExportFailed(event => exportProgress.applyFailed(event));
        this.ws.setOnConnect(() => this.syncActiveExport());

        // Handle read confirmation - update local state
        this.ws.setOnReadConfirmed(notificationId => {
            const currentNotifications = get(this.notificationsStore) || [];
            const updatedNotifications = currentNotifications.map(n =>
                n.id === notificationId ? {...n, read: true} : n
            );
            this.notificationsStore.set(updatedNotifications);

            const currentUnread = get(this.unreadCounterStore) || 0;
            this.unreadCounterStore.set(Math.max(0, currentUnread - 1));
        });

        // Handle all read confirmation
        this.ws.setOnAllReadConfirmed(() => {
            const currentNotifications = get(this.notificationsStore) || [];
            const updatedNotifications = currentNotifications.map(n => ({...n, read: true}));
            this.notificationsStore.set(updatedNotifications);
            this.unreadCounterStore.set(0);
        });

        // Connect
        this.ws.connect();
        this.isInitialized = true;

        // Add visibility change handler to reconnect when tab becomes visible
        this.visibilityHandler = () => {
            if (document.visibilityState === 'visible' && !this.isConnected()) {
                console.log('[NotificationService] Tab visible, attempting reconnect...');
                this.ws.resetReconnectAttempts();
                this.ws.connect();
            }
        };
        document.addEventListener('visibilitychange', this.visibilityHandler);
    }

    /**
     * Mark a single notification as read
     * @param {string} notificationId - The notification UUID
     */
    markRead(notificationId) {
        if (!this.ws) {
            console.warn('[NotificationService] Not initialized');
            return;
        }
        this.ws.markRead(notificationId);
    }

    /**
     * Mark all notifications as read
     */
    markAllRead() {
        if (!this.ws) {
            console.warn('[NotificationService] Not initialized');
            return;
        }
        this.ws.markAllRead();
    }

    /**
     * Fetch notifications manually
     */
    fetch() {
        if (!this.ws) {
            console.warn('[NotificationService] Not initialized');
            return;
        }
        this.ws.fetch();
    }

    async syncActiveExport() {
        if (this.activeExportSync) return this.activeExportSync;
        this.activeExportSync = (async () => {
            try {
                const endpoint = __bakney.env.API.ASSOCIATION.EXPORT.ACTIVE;
                const response = await apiFetch(endpoint);
                if (!response.error) {
                    if (response.response.active) {
                        exportProgress.applySnapshot(response.response);
                    } else if (response.response.terminal) {
                        const terminal = response.response.terminal;
                        if (terminal.status === 'SUCCESS') {
                            exportProgress.applyCompleted(terminal, false);
                        } else {
                            exportProgress.applyFailed(terminal, false);
                        }
                    } else {
                        exportProgress.reset();
                    }
                }
                return response;
            } catch (error) {
                console.warn('[NotificationService] Unable to synchronize export state:', error);
                return {error: true, response: {}};
            } finally {
                this.activeExportSync = null;
            }
        })();
        return this.activeExportSync;
    }

    /**
     * Set callback for new notification push (e.g., to show toast)
     * @param {function(Object): void} callback - (notification) => {}
     */
    setOnNewNotification(callback) {
        this.onNewNotificationCallback = callback;
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
     * Reconnect (e.g., after token refresh)
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
const notificationService = new NotificationService();
export default notificationService;

import {getWebSocketUrl} from './websocketUrl.js';

/**
 * AgentWebSocket - AI Agent chat via WebSocket
 *
 * Connects to /ws/agent/ for real-time AI assistant communication.
 * Follows the NotificationWebSocket pattern with lazy connection.
 *
 * Message types received:
 *   status        – agent lifecycle (processing, done, history_cleared)
 *   message       – non-streamed text (e.g. welcome message on connect)
 *   message_chunk – single token/fragment from the LLM stream
 *   message_end   – marks the end of a streamed response segment
 *   tool_call     – agent is invoking a tool (query_data, export_data, etc.)
 *   export_ready  – downloadable file is ready (base64 payload)
 *   error         – server-side error
 *
 * Terminal close codes (no reconnect):
 *   4001 – authentication failed
 *   4002 – session replaced (another tab)
 *   4003 – rate limited
 */

const TERMINAL_CODES = {
    4001: 'Autenticazione fallita. Effettua nuovamente il login.',
    4002: "Sessione sostituita da un'altra scheda.",
    4003: 'Troppe richieste. Riprova tra qualche minuto.',
};

class AgentWebSocket {
    constructor(token = null) {
        this.ws = null;
        this.token = token;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 2000;
        this.reconnectTimer = null;
        this._disconnecting = false;

        // Callbacks
        this.onStatus = null;
        this.onMessage = null;
        this.onMessageChunk = null;
        this.onMessageEnd = null;
        this.onToolCall = null;
        this.onExportReady = null;
        this.onReportSaved = null;
        this.onError = null;
        this.onDone = null;
        this.onConnect = null;
        this.onDisconnect = null;
    }

    connect() {
        if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        const wsPath = __bakney.env.WS?.AGENT || '/ws/agent/';
        const baseUrl = getWebSocketUrl(wsPath);
        const url = this.token ? `${baseUrl}?token=${this.token}` : baseUrl;

        this.ws = new WebSocket(url);

        this.ws.onopen = () => {
            console.log('[AgentWebSocket] Connected');
            this.reconnectAttempts = 0;
            this.onConnect?.();
        };

        this.ws.onmessage = event => {
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (error) {
                console.error('[AgentWebSocket] Failed to parse message:', error);
            }
        };

        this.ws.onclose = event => {
            console.log('[AgentWebSocket] Closed:', event.code);
            this.onDisconnect?.(event.code);

            // Manual disconnect — don't reconnect
            if (this._disconnecting) {
                this._disconnecting = false;
                return;
            }

            // Terminal close codes – don't reconnect
            if (TERMINAL_CODES[event.code]) {
                console.error('[AgentWebSocket]', TERMINAL_CODES[event.code]);
                this.onError?.(TERMINAL_CODES[event.code]);
                return;
            }

            this.attemptReconnect();
        };

        this.ws.onerror = error => {
            console.error('[AgentWebSocket] Error:', error);
        };
    }

    handleMessage(data) {
        switch (data.type) {
            case 'status':
                this.onStatus?.(data.status);
                if (data.status === 'done') {
                    this.onDone?.();
                }
                break;

            case 'message':
                this.onMessage?.(data.content);
                break;

            case 'message_chunk':
                this.onMessageChunk?.(data.content);
                break;

            case 'message_end':
                this.onMessageEnd?.();
                break;

            case 'tool_call':
                this.onToolCall?.(data.tool, data.arguments);
                break;

            case 'export_ready':
                this.onExportReady?.({
                    data_base64: data.data_base64,
                    content_type: data.content_type,
                    filename: data.filename,
                    row_count: data.row_count,
                    can_save: data.can_save || false,
                    default_name: data.default_name || data.filename || 'Report',
                    default_description: data.default_description || '',
                    description_hint: data.description_hint || '',
                });
                break;

            case 'report_saved':
                this.onReportSaved?.({
                    saved_report_id: data.saved_report_id,
                    name: data.name,
                });
                break;

            case 'done':
                this.onStatus?.('done');
                this.onDone?.();
                break;

            case 'error':
                console.error('[AgentWebSocket] Server error:', data.message);
                this.onError?.(data.message);
                break;

            default:
                console.warn('[AgentWebSocket] Unknown message type:', data.type);
        }
    }

    attemptReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.error('[AgentWebSocket] Max reconnection attempts reached');
            this.onError?.('Connessione persa. Ricarica la pagina per riprovare.');
            return;
        }

        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

        console.log(`[AgentWebSocket] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
        this.reconnectTimer = setTimeout(() => this.connect(), delay);
    }

    send(data) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
            return true;
        }
        console.warn('[AgentWebSocket] Cannot send - WebSocket not open');
        return false;
    }

    // Public API

    sendMessage(text) {
        this.send({type: 'user_message', message: text});
    }

    cancel() {
        this.send({type: 'cancel'});
    }

    clearHistory() {
        this.send({type: 'clear_history'});
    }

    saveReport(name, description) {
        const payload = {type: 'save_report', name};
        if (description) payload.description = description;
        this.send(payload);
    }

    disconnect() {
        this._disconnecting = true;
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }

    isConnected() {
        return this.ws?.readyState === WebSocket.OPEN;
    }

    resetReconnectAttempts() {
        this.reconnectAttempts = 0;
    }

    // Callback setters

    setOnStatus(handler) {
        this.onStatus = handler;
    }
    setOnMessage(handler) {
        this.onMessage = handler;
    }
    setOnMessageChunk(handler) {
        this.onMessageChunk = handler;
    }
    setOnMessageEnd(handler) {
        this.onMessageEnd = handler;
    }
    setOnToolCall(handler) {
        this.onToolCall = handler;
    }
    setOnExportReady(handler) {
        this.onExportReady = handler;
    }
    setOnReportSaved(handler) {
        this.onReportSaved = handler;
    }
    setOnError(handler) {
        this.onError = handler;
    }
    setOnDone(handler) {
        this.onDone = handler;
    }
    setOnConnect(handler) {
        this.onConnect = handler;
    }
    setOnDisconnect(handler) {
        this.onDisconnect = handler;
    }
}

export default AgentWebSocket;

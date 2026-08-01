<script>
    import {fly, slide, scale, fade} from 'svelte/transition';
    import {quintOut} from 'svelte/easing';
    import Portal from 'svelte-portal';
    import SvelteMarkdown from 'svelte-markdown';
    import {
        Robot,
        X,
        ArrowUp,
        Minus,
        Stop,
        TrashSimple,
        ArrowDown,
        FileArrowDown,
        CircleNotch,
        Flag,
        ArrowsOut,
        ArrowsIn,
        Check,
        BookmarkSimple,
        DownloadSimple,
    } from 'phosphor-svelte';
    import AgentWebSocket from 'utils/AgentWebSocket.js';
    import {sessionToken, userData} from 'store/stores.js';
    import {isAgentOpen, agentProcessing, reportSavedTrigger} from 'store/agentStore.js';
    import {oemConfig} from 'store/instanceStore.js';
    import {onMount, onDestroy, afterUpdate, tick} from 'svelte';
    import BasicModal from 'components/modals/BasicModal.svelte';

    let messages = [];
    let inputText = '';
    let status = 'idle'; // idle | processing | reconnecting
    let isConnected = false;
    let isReconnecting = false;
    let ws = null;
    let messagesContainer;
    let textareaEl;
    let panelEl;
    let shouldAutoScroll = true;
    let isExpanded = false;
    let isMinimized = false;
    let inputFocused = false;
    let isStreaming = false;
    let streamBuffer = '';
    let msgIdCounter = 0;
    let streamingMsgId = null;

    // Save modal state
    let showSaveModal = false;
    let saveDefaultName = '';
    let saveDefaultDescription = '';
    let saveDescriptionHint = '';
    let saveReportName = '';
    let saveReportDescription = '';

    // Keep the store in sync with local status
    $: agentProcessing.set(status === 'processing');

    // Tool label mapping (Italian)
    const TOOL_LABELS = {
        get_schema: 'Lettura schema',
        query_data: 'Ricerca dati',
        count_data: 'Conteggio',
        get_field_values: 'Lettura valori',
        export_data: 'Preparazione export',
        save_report: 'Salvataggio report',
        list_reports: 'Elenco report salvati',
    };

    function getToolLabel(tool) {
        return TOOL_LABELS[tool] || tool;
    }

    function findMsgById(id) {
        for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].id === id) return i;
        }
        return -1;
    }

    function finalizeStream() {
        if (!isStreaming) return;
        if (streamingMsgId !== null) {
            const idx = findMsgById(streamingMsgId);
            if (idx >= 0) {
                messages[idx] = {...messages[idx], streaming: false};
                messages = messages;
            }
        }
        isStreaming = false;
        streamBuffer = '';
        streamingMsgId = null;
    }

    function initWebSocket() {
        if (ws) return;

        ws = new AgentWebSocket($sessionToken);

        ws.setOnConnect(() => {
            isConnected = true;
            isReconnecting = false;
        });

        ws.setOnDisconnect(code => {
            isConnected = false;
            if (code !== 4001 && code !== 4002 && code !== 4003) {
                isReconnecting = true;
            }
            if (status === 'processing') {
                status = 'idle';
            }
        });

        ws.setOnStatus(s => {
            if (s === 'processing') {
                status = 'processing';
            } else if (s === 'done') {
                status = 'idle';
                messages = messages.map(m => (m.role === 'tool_call' && !m.done ? {...m, done: true} : m));
            } else if (s === 'history_cleared') {
                messages = [];
                status = 'idle';
            }
        });

        ws.setOnMessage(content => {
            const last = messages[messages.length - 1];
            if (last && last.role === 'agent') {
                messages = [...messages.slice(0, -1), {...last, content: last.content + content}];
            } else {
                messages = [...messages, {role: 'agent', content, timestamp: new Date()}];
            }
        });

        ws.setOnMessageChunk(content => {
            if (!isStreaming) {
                isStreaming = true;
                streamBuffer = content;
                streamingMsgId = ++msgIdCounter;
                messages = [...messages, {id: streamingMsgId, role: 'agent', content, streaming: true, timestamp: new Date()}];
            } else {
                streamBuffer += content;
                const idx = findMsgById(streamingMsgId);
                if (idx >= 0) {
                    messages[idx] = {...messages[idx], content: streamBuffer};
                    messages = messages;
                }
            }
        });

        ws.setOnMessageEnd(() => {
            if (isStreaming) {
                const idx = findMsgById(streamingMsgId);
                if (idx >= 0) {
                    messages[idx] = {...messages[idx], streaming: false};
                    messages = messages;
                }
                isStreaming = false;
                streamBuffer = '';
                streamingMsgId = null;
            }
        });

        ws.setOnToolCall((tool, args) => {
            // Remove any pending (non-done) tool_call chips, then add the new one
            messages = [
                ...messages.filter(m => !(m.role === 'tool_call' && !m.done)),
                {role: 'tool_call', tool, args, done: false, timestamp: new Date()},
            ];
        });

        ws.setOnExportReady(data => {
            messages = [...messages, {role: 'export', ...data, timestamp: new Date()}];
        });

        ws.setOnReportSaved(data => {
            messages = [...messages, {role: 'report_saved', ...data, timestamp: new Date()}];
            reportSavedTrigger.set(Date.now());
        });

        ws.setOnError(errorMsg => {
            isReconnecting = false;
            messages = [...messages, {role: 'error', content: errorMsg, timestamp: new Date()}];
            if (status === 'processing') {
                status = 'idle';
            }
        });

        ws.setOnDone(() => {
            finalizeStream();
            status = 'idle';
        });

        ws.connect();
    }

    // React to store changes (Header button toggles this)
    $: if ($isAgentOpen) {
        if (!ws) {
            initWebSocket();
        }
        tick().then(() => {
            textareaEl?.focus();
            scrollToBottom();
        });
    }

    function closePanel() {
        isExpanded = false;
        $isAgentOpen = false;
    }

    function sendMessage() {
        const text = inputText.trim();
        if (!text || !isConnected || status === 'processing') return;

        messages = [...messages, {role: 'user', content: text, timestamp: new Date()}];
        inputText = '';
        resizeTextarea();
        ws.sendMessage(text);
    }

    function handleCancel() {
        if (ws && status === 'processing') {
            ws.cancel();
        }
    }

    function handleClearHistory() {
        if (!ws || !isConnected) return;
        // Clear client-side immediately for instant feedback
        messages = [];
        status = 'idle';
        isStreaming = false;
        streamBuffer = '';
        // Then tell the server to clear its history too
        ws.clearHistory();
    }

    function serializeChat() {
        return messages
            .map(m => {
                if (m.role === 'user') return `[Utente] ${m.content}`;
                if (m.role === 'agent') return `[Assistente] ${m.content}`;
                if (m.role === 'tool_call') return `[Operazione] ${getToolLabel(m.tool)}`;
                if (m.role === 'export') return `[Export] ${m.filename || 'file'}`;
                if (m.role === 'report_saved') return `[Report salvato] ${m.name || 'report'}`;
                if (m.role === 'error') return `[Errore] ${m.content}`;
                return '';
            })
            .filter(Boolean)
            .join('\n');
    }

    function handleReport() {
        if (!$oemConfig?.supportUrl) return;
        const chatLog = serializeChat();
        const email = $userData?.email || '';
        const name = `${$userData?.first_name || ''} ${$userData?.last_name || ''}`.trim();
        const url = `${$oemConfig.supportUrl}?email=${encodeURIComponent(email)}&name=${encodeURIComponent(
            name
        )}&message=${encodeURIComponent(chatLog)}`;
        window.open(url, '_blank', 'width=700,height=850');
    }

    function handleKeydown(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (status === 'processing') return;
            sendMessage();
        }
    }

    function resizeTextarea() {
        if (!textareaEl) return;
        const style = getComputedStyle(textareaEl);
        const fontSize = parseFloat(style.fontSize) || 16;
        const lh = parseFloat(style.lineHeight);
        const lineH = isNaN(lh) ? fontSize * 1.45 : lh;
        const max15 = Math.ceil(lineH * 15);
        const maxPanel = panelEl ? Math.floor(panelEl.clientHeight / 3) : max15;
        const maxH = Math.min(max15, maxPanel);
        textareaEl.style.height = 'auto';
        const newH = Math.min(textareaEl.scrollHeight, maxH);
        textareaEl.style.height = newH + 'px';
        textareaEl.style.overflowY = textareaEl.scrollHeight > maxH ? 'auto' : 'hidden';
    }

    function handleScroll() {
        if (!messagesContainer) return;
        const {scrollTop, scrollHeight, clientHeight} = messagesContainer;
        shouldAutoScroll = scrollHeight - scrollTop - clientHeight < 60;
    }

    function scrollToBottom() {
        if (messagesContainer) {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
            shouldAutoScroll = true;
        }
    }

    function downloadExport(msg) {
        let url;
        try {
            const byteChars = atob(msg.data_base64);
            const byteArray = new Uint8Array(byteChars.length);
            for (let i = 0; i < byteChars.length; i++) {
                byteArray[i] = byteChars.charCodeAt(i);
            }
            const blob = new Blob([byteArray], {type: msg.content_type || 'application/octet-stream'});
            url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = msg.filename || 'export';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } catch (err) {
            console.error('[AgentChat] Download failed:', err);
        } finally {
            if (url) URL.revokeObjectURL(url);
        }
    }

    function openSaveModal(msg) {
        saveDefaultName = msg.default_name || 'Report';
        saveDefaultDescription = msg.default_description || '';
        saveDescriptionHint = msg.description_hint || '';
        saveReportName = saveDefaultName;
        saveReportDescription = saveDefaultDescription;
        showSaveModal = true;
    }

    function handleConfirmSave() {
        if (!saveReportName.trim()) return;

        ws.saveReport(saveReportName.trim(), saveReportDescription.trim() || undefined);

        showSaveModal = false;
        saveDefaultName = '';
        saveDefaultDescription = '';
        saveDescriptionHint = '';
        saveReportName = '';
        saveReportDescription = '';
    }

    afterUpdate(() => {
        if (shouldAutoScroll && messagesContainer) {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
    });

    function handleVisibilityChange() {
        if (document.visibilityState === 'visible' && ws && !ws.isConnected()) {
            ws.resetReconnectAttempts();
            ws.connect();
        }
    }

    onMount(() => {
        document.addEventListener('visibilitychange', handleVisibilityChange);
    });

    onDestroy(() => {
        if (ws) {
            ws.disconnect();
            ws = null;
        }
        if (typeof document !== 'undefined') {
            document.removeEventListener('visibilitychange', handleVisibilityChange);
        }
    });
</script>

<Portal target="#portal-elements">
    {#if isExpanded && $isAgentOpen}
        <div class="agent-backdrop" transition:fade={{duration: 250}} />
    {/if}
    {#if $isAgentOpen}
        <div
            bind:this={panelEl}
            class="agent-panel"
            class:agent-panel-expanded={isExpanded}
            class:agent-panel-minimized={isMinimized}
            transition:scale={{duration: 280, start: 0.92, opacity: 0, easing: quintOut}}>
            <!-- Header -->
            <div class="agent-header">
                <div class="agent-header-title">
                    <Robot size={20} weight="duotone" class="text-primary" />
                    <span>Agente AI</span>
                </div>
                <div class="agent-header-actions ml-2">
                    {#if !isMinimized}
                        <button
                            class="agent-header-btn"
                            on:click={handleReport}
                            title="Segnala un problema"
                            disabled={messages.length === 0}>
                            <Flag size={16} weight="bold" />
                        </button>
                        <button
                            class="agent-header-btn"
                            on:click={handleClearHistory}
                            title="Cancella cronologia"
                            disabled={!isConnected || messages.length === 0}>
                            <TrashSimple size={16} weight="bold" />
                        </button>
                    {/if}
                    <button
                        class="agent-header-btn"
                        on:click={() => {
                            isMinimized = !isMinimized;
                            if (isMinimized) isExpanded = false;
                        }}
                        title={isMinimized ? 'Espandi' : 'Minimizza'}>
                        {#if isMinimized}
                            <ArrowUp size={16} weight="bold" />
                        {:else}
                            <Minus size={16} weight="bold" />
                        {/if}
                    </button>
                    <button
                        class="agent-header-btn"
                        on:click={() => {
                            isExpanded = !isExpanded;
                            if (isExpanded) isMinimized = false;
                        }}
                        title={isExpanded ? 'Riduci' : 'Espandi'}>
                        {#if isExpanded}
                            <ArrowsIn size={16} weight="bold" />
                        {:else}
                            <ArrowsOut size={16} weight="bold" />
                        {/if}
                    </button>
                    <button class="agent-header-btn" on:click={closePanel} title="Chiudi">
                        <X size={18} weight="bold" />
                    </button>
                </div>
            </div>

            {#if !isMinimized}
                <!-- Reconnecting banner -->
                {#if isReconnecting}
                    <div class="agent-reconnecting" transition:slide={{duration: 200}}>
                        <CircleNotch size={14} weight="duotone" class="agent-spinner" />
                        Riconnessione in corso...
                    </div>
                {/if}

                <!-- Messages -->
                <div class="agent-messages" bind:this={messagesContainer} on:scroll={handleScroll}>
                    {#if messages.length === 0}
                        <div class="agent-empty">
                            <Robot size={40} weight="duotone" class="text-primary" />
                            <p>
                                Ciao! Sono l'agente AI di {$userData?.sport_association?.denomination ||
                                    'questo gestionale'}.
                            </p>
                            <p class="agent-empty-sub">Chiedimi informazioni su soci, corsi, pagamenti e altro.</p>
                        </div>
                    {/if}

                    {#each messages as msg, i (i)}
                        {#if msg.role === 'user'}
                            <div class="agent-msg agent-msg-user" in:fly={{x: 20, duration: 200}}>
                                <div class="agent-bubble agent-bubble-user">
                                    {msg.content}
                                </div>
                            </div>
                        {:else if msg.role === 'agent'}
                            <div class="agent-msg agent-msg-agent" in:fly={{x: -20, duration: 200}}>
                                <div class="agent-bubble agent-bubble-agent">
                                    {#if msg.streaming}
                                        <span class="agent-streaming-text">{msg.content}</span>
                                    {:else}
                                        <SvelteMarkdown source={msg.content} options={{mangle: false}} />
                                    {/if}
                                </div>
                            </div>
                        {:else if msg.role === 'tool_call' && !msg.done}
                            <div class="agent-msg agent-msg-tool" in:fly={{y: 10, duration: 150}}>
                                <div class="agent-tool-chip">
                                    <CircleNotch size={13} weight="duotone" class="agent-spinner" />
                                    <span>{getToolLabel(msg.tool)}</span>
                                </div>
                            </div>
                        {:else if msg.role === 'export'}
                            <div class="agent-msg agent-msg-agent" in:fly={{x: -20, duration: 200}}>
                                <div class="agent-export-card">
                                    <div class="agent-export-info">
                                        <FileArrowDown size={20} weight="duotone" class="text-primary" />
                                        <div>
                                            <div class="agent-export-filename">{msg.filename || 'export'}</div>
                                            {#if msg.row_count}
                                                <div class="agent-export-meta">{msg.row_count} righe</div>
                                            {/if}
                                        </div>
                                    </div>
                                    <div class="agent-export-actions">
                                        <button class="agent-export-btn" on:click={() => downloadExport(msg)}>
                                            <DownloadSimple size={16} weight="bold" />
                                            <span>Scarica</span>
                                        </button>
                                    </div>
                                </div>
                            </div>
                            {#if msg.can_save}
                                <div class="agent-msg agent-msg-agent" in:fly={{x: -20, duration: 200}}>
                                    <div class="agent-bubble agent-bubble-agent agent-save-prompt">
                                        <span>Puoi salvare il modello di report per usarlo o modificarlo per futuri export</span>
                                        <button class="btn btn-primary btn-sm agent-save-prompt-btn" on:click={() => openSaveModal(msg)}>Salva</button>
                                    </div>
                                </div>
                            {/if}
                        {:else if msg.role === 'report_saved'}
                            <div class="agent-msg agent-msg-agent" in:fly={{x: -20, duration: 200}}>
                                <div class="agent-report-saved-card">
                                    <div class="agent-report-saved-icon">
                                        <BookmarkSimple size={20} weight="duotone" />
                                    </div>
                                    <div class="agent-report-saved-info">
                                        <div class="agent-report-saved-name">{msg.name || 'Report'}</div>
                                        <div class="agent-report-saved-meta">
                                            <Check size={12} weight="bold" />
                                            Salvato con successo
                                        </div>
                                    </div>
                                    <a href="/#/saved-reports" class="agent-report-saved-btn" on:click={() => reportSavedTrigger.set(Date.now())}> Visualizza </a>
                                </div>
                            </div>
                        {:else if msg.role === 'error'}
                            <div class="agent-msg agent-msg-agent" in:fly={{x: -20, duration: 200}}>
                                <div class="agent-bubble agent-bubble-error">
                                    {msg.content}
                                </div>
                            </div>
                        {/if}
                    {/each}

                    <!-- Typing indicator -->
                    {#if status === 'processing' && (messages.length === 0 || messages[messages.length - 1]?.role !== 'agent')}
                        <div class="agent-msg agent-msg-agent" in:fly={{y: 10, duration: 150}}>
                            <div class="agent-typing">
                                <span class="agent-dot" />
                                <span class="agent-dot" />
                                <span class="agent-dot" />
                            </div>
                        </div>
                    {/if}
                </div>

                <!-- Scroll to bottom -->
                {#if !shouldAutoScroll}
                    <button class="agent-scroll-btn" on:click={scrollToBottom} transition:fly={{y: 10, duration: 150}}>
                        <ArrowDown size={16} weight="duotone" />
                    </button>
                {/if}

                <!-- Footer -->
                <div class="agent-footer">
                    <div class="agent-input-box" class:agent-input-box-focus={inputFocused}>
                        <textarea
                            bind:this={textareaEl}
                            bind:value={inputText}
                            on:input={resizeTextarea}
                            on:keydown={handleKeydown}
                            on:focus={() => (inputFocused = true)}
                            on:blur={() => (inputFocused = false)}
                            placeholder="Scrivi un messaggio... (Shift+Invio per andare a capo)"
                            rows="3"
                            disabled={!isConnected || status === 'processing'}
                            class="agent-textarea" />
                        <div class="agent-input-bar align-items-center">
                            <span class="agent-tools-hint my-auto" />
                            {#if status === 'processing'}
                                <button class="agent-send-btn agent-cancel-btn" on:click={handleCancel} title="Annulla">
                                    <CircleNotch size={20} weight="duotone" class="agent-spinner" />
                                </button>
                            {:else}
                                <button
                                    class="agent-send-btn mb-0"
                                    on:click={sendMessage}
                                    disabled={!inputText.trim() || !isConnected}
                                    title="Invia">
                                    <ArrowUp size={20} weight="bold" />
                                </button>
                            {/if}
                        </div>
                    </div>
                    <p class="agent-disclaimer">
                        I dati esportati o le informazioni fornite potrebbero non essere del tutto accurate.
                    </p>
                </div>
            {/if}

            <!-- Save Report Modal -->
            {#if showSaveModal}
                <BasicModal
                    id="save-report-modal"
                    bind:show={showSaveModal}
                    title="Salva Report"
                    showTitle={true}
                    cancelButton="Annulla"
                    actionButton="Salva"
                    modalSize="sm"
                    target="#portal-elements-foreground"
                    on:confirm={handleConfirmSave}
                    on:cancel={() => {
                        showSaveModal = false;
                        saveDefaultName = '';
                        saveDefaultDescription = '';
                        saveDescriptionHint = '';
                        saveReportName = '';
                        saveReportDescription = '';
                    }}>
                    <div class="save-report-modal-content">
                        {#if saveDescriptionHint}
                            <div class="save-report-hint">{saveDescriptionHint}</div>
                        {/if}
                        <div class="form-group">
                            <label for="save-report-name" class="font-weight-bold font-size-sm text-dark-75 mb-2">Nome</label>
                            <input
                                id="save-report-name"
                                type="text"
                                class="form-control form-control-solid"
                                bind:value={saveReportName}
                                placeholder="Nome del report"
                                on:keydown={e => e.key === 'Enter' && handleConfirmSave()} />
                        </div>
                        <div class="form-group mb-0">
                            <label for="save-report-description" class="font-weight-bold font-size-sm text-dark-75 mb-2">Descrizione <span class="text-muted font-weight-normal">(opzionale)</span></label>
                            <textarea
                                id="save-report-description"
                                class="form-control form-control-solid"
                                bind:value={saveReportDescription}
                                placeholder="Descrizione del report"
                                rows="2" />
                        </div>
                    </div>
                </BasicModal>
            {/if}
        </div>
    {/if}
</Portal>

<style>
    /* ====== Backdrop ====== */
    .agent-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.25);
        backdrop-filter: blur(4px);
        -webkit-backdrop-filter: blur(4px);
        z-index: 10000;
    }

    /* ====== Panel ====== */
    .agent-panel {
        position: fixed;
        bottom: 2rem;
        right: 2rem;
        width: 50rem;
        height: 52.5rem;
        max-height: calc(100vh - 4rem);
        border-radius: 1rem;
        background: var(--bg-surface, #ffffff);
        border: 1px solid var(--border-color, #ebedf3);
        box-shadow: 0 8px 40px rgba(0, 0, 0, 0.12);
        display: flex;
        flex-direction: column;
        overflow: hidden;
        z-index: 10001;
        transform-origin: top right;
        transition: width 0.3s ease, height 0.3s ease, bottom 0.3s ease, right 0.3s ease, border-radius 0.3s ease;
    }
    .agent-panel-expanded {
        width: calc(100vw - 4rem);
        height: calc(100vh - 4rem);
        max-height: calc(100vh - 4rem);
        bottom: 2rem;
        right: 2rem;
        border-radius: 0.75rem;
    }
    .agent-panel-minimized {
        height: auto;
        width: auto;
        min-width: 14rem;
    }

    /* ====== Header ====== */
    .agent-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0.75rem 1rem;
        background: var(--bg-surface, #ffffff);
        flex-shrink: 0;
    }
    .agent-header-title {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-weight: 700;
        font-size: 1rem;
        color: var(--text-primary, #181c32);
    }
    .agent-header-actions {
        display: flex;
        align-items: center;
        gap: 0.25rem;
    }
    .agent-header-btn {
        background: none;
        border: none;
        cursor: pointer;
        padding: 0.35rem;
        border-radius: 0.375rem;
        color: var(--text-secondary, #7e8299);
        display: flex;
        align-items: center;
        margin-bottom: 0;
        transition: background 0.15s, color 0.15s;
    }
    .agent-header-btn:hover {
        background: var(--bg-hover, #f4f6f9);
        color: var(--text-primary, #181c32);
    }
    .agent-header-btn:disabled {
        opacity: 0.4;
        cursor: default;
    }
    .agent-header-btn:disabled:hover {
        background: none;
        color: var(--text-secondary, #7e8299);
    }

    /* ====== Reconnecting ====== */
    .agent-reconnecting {
        display: flex;
        align-items: center;
        gap: 0.4rem;
        padding: 0.4rem 1rem;
        background: var(--warning, #f5ce01);
        color: #181c32;
        font-size: 0.8rem;
        font-weight: 650;
        flex-shrink: 0;
    }

    /* ====== Messages ====== */
    .agent-messages {
        flex: 1;
        overflow-y: auto;
        padding: 1rem;
        padding-bottom: 0;
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
    }
    .agent-messages::-webkit-scrollbar {
        width: 5px;
    }
    .agent-messages::-webkit-scrollbar-thumb {
        background: var(--border-color, #ebedf3);
        border-radius: 3px;
    }

    .agent-empty {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100%;
        color: var(--text-muted, #92929c);
        text-align: center;
        gap: 0.25rem;
    }
    .agent-empty p {
        margin: 0;
        font-size: 1.1rem;
        font-weight: 500;
        color: var(--text-secondary, #5e6278);
    }
    .agent-empty-sub {
        font-size: 1rem !important;
        font-weight: 400;
        color: var(--text-muted, #92929c);
    }

    /* ====== Message rows ====== */
    .agent-msg {
        display: flex;
        flex-direction: column;
        max-width: 88%;
    }
    .agent-msg-user {
        align-self: flex-end;
        align-items: flex-end;
    }
    .agent-msg-agent {
        align-self: flex-start;
        align-items: flex-start;
    }
    .agent-msg-tool {
        align-self: flex-start;
    }

    /* ====== Bubbles ====== */
    .agent-bubble {
        padding: 0.65rem 0.9rem;
        border-radius: 0.85rem;
        font-size: 1.1rem;
        line-height: 1.55;
        font-weight: 450;
        word-break: break-word;
    }
    .agent-bubble-user {
        background: var(--bg-surface-secondary, #f3f6f9);
        color: var(--dark, #181c32);
        border-bottom-right-radius: 0.25rem;
        white-space: pre-wrap;
        font-weight: 500;
    }
    .agent-bubble-agent {
        background: none;
        color: var(--dark, #181c32);
        border-bottom-left-radius: 0.25rem;
    }
    .agent-bubble-error {
        background: color-mix(in srgb, var(--danger, #ff3d60) 12%, var(--bg-surface, #ffffff));
        color: var(--danger, #ff3d60);
        border-bottom-left-radius: 0.25rem;
        font-size: 1rem;
        font-weight: 500;
    }

    /* Markdown inside agent bubbles */
    .agent-bubble-agent :global(p) {
        margin: 0 0 0.4rem;
    }
    .agent-bubble-agent :global(p:last-child) {
        margin-bottom: 0;
    }
    .agent-bubble-agent :global(ul),
    .agent-bubble-agent :global(ol) {
        margin: 0.25rem 0;
        padding-left: 1.25rem;
    }
    .agent-bubble-agent :global(code) {
        font-size: 0.95rem;
        padding: 0.12rem 0.35rem;
        border-radius: 0.25rem;
        background: var(--bg-hover, #f4f6f9);
    }
    .agent-bubble-agent :global(pre) {
        margin: 0.35rem 0;
        padding: 0.5rem;
        border-radius: 0.4rem;
        background: var(--bg-hover, #f4f6f9);
        overflow-x: auto;
        font-size: 0.95rem;
    }
    .agent-bubble-agent :global(pre code) {
        padding: 0;
        background: none;
    }
    .agent-streaming-text {
        white-space: pre-wrap;
    }
    .agent-bubble-agent :global(strong) {
        font-weight: 700;
    }
    .agent-bubble-agent :global(table) {
        border-collapse: collapse;
        margin: 0.35rem 0;
        font-size: 1rem;
        width: 100%;
    }
    .agent-bubble-agent :global(th),
    .agent-bubble-agent :global(td) {
        border: 1px solid var(--border-color, #ebedf3);
        padding: 0.3rem 0.5rem;
        text-align: left;
    }
    .agent-bubble-agent :global(th) {
        background: var(--bg-hover, #f4f6f9);
        font-weight: 700;
    }

    /* ====== Tool chip ====== */
    .agent-tool-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.3rem;
        padding: 0.28rem 0.65rem;
        border-radius: 1rem;
        font-size: 0.9rem;
        font-weight: 550;
        color: var(--text-secondary, #5e6278);
        background: var(--bg-surface-secondary, #f3f6f9);
        border: 1px solid var(--border-color, #ebedf3);
    }
    /* ====== Export card ====== */
    .agent-export-card {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        padding: 0.6rem 0.85rem;
        border-radius: 0.7rem;
        background: var(--bg-surface-secondary, #f3f6f9);
        border: 1px solid var(--border-color, #ebedf3);
    }
    .agent-export-info {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        color: var(--text-primary, #181c32);
    }
    .agent-export-filename {
        font-size: 1.05rem;
        font-weight: 650;
        color: var(--dark, #181c32);
    }
    .agent-export-meta {
        font-size: 0.9rem;
        color: var(--text-secondary, #5e6278);
        font-weight: 450;
    }
    .agent-export-btn {
        display: flex;
        align-items: center;
        gap: 0.35rem;
        background: var(--primary, #351dc2);
        color: #ffffff;
        border: none;
        border-radius: 0.4rem;
        padding: 0.35rem 0.75rem;
        font-size: 0.95rem;
        font-weight: 650;
        cursor: pointer;
        white-space: nowrap;
        transition: opacity 0.15s;
    }
    .agent-export-btn:hover {
        opacity: 0.85;
    }
    .agent-export-actions {
        display: flex;
        gap: 0.5rem;
    }
    /* ====== Save prompt ====== */
    .agent-save-prompt {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 0.6rem;
        font-size: 1rem;
    }
    .agent-save-prompt-btn {
        flex-shrink: 0;
        margin-bottom: 0;
    }

    /* ====== Save Report Modal ====== */
    .save-report-modal-content {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
    }
    .save-report-hint {
        font-size: 0.9rem;
        color: var(--text-secondary, #5e6278);
        font-weight: 450;
        padding: 0.4rem 0.6rem;
        background: var(--bg-surface-secondary, #f3f6f9);
        border-radius: 0.4rem;
        margin-bottom: 0.25rem;
    }

    /* ====== Report saved card ====== */
    .agent-report-saved-card {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        padding: 0.6rem 0.85rem;
        border-radius: 0.7rem;
        background: color-mix(in srgb, var(--success, #1bc5bd) 10%, var(--bg-surface, #ffffff));
        border: 1px solid color-mix(in srgb, var(--success, #1bc5bd) 25%, transparent);
    }
    .agent-report-saved-icon {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 2.25rem;
        height: 2.25rem;
        border-radius: 0.5rem;
        background: color-mix(in srgb, var(--success, #1bc5bd) 15%, transparent);
        color: var(--success, #1bc5bd);
    }
    .agent-report-saved-info {
        display: flex;
        flex-direction: column;
        gap: 0.15rem;
        flex: 1;
        min-width: 0;
    }
    .agent-report-saved-name {
        font-size: 1.05rem;
        font-weight: 650;
        color: var(--dark, #181c32);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .agent-report-saved-meta {
        display: flex;
        align-items: center;
        gap: 0.25rem;
        font-size: 0.85rem;
        color: var(--success, #1bc5bd);
        font-weight: 550;
    }
    .agent-report-saved-btn {
        background: var(--success, #1bc5bd);
        color: #ffffff;
        border: none;
        border-radius: 0.4rem;
        padding: 0.35rem 0.75rem;
        font-size: 0.95rem;
        font-weight: 650;
        cursor: pointer;
        white-space: nowrap;
        text-decoration: none;
        transition: opacity 0.15s;
    }
    .agent-report-saved-btn:hover {
        opacity: 0.85;
    }

    /* ====== Typing indicator ====== */
    .agent-typing {
        display: flex;
        align-items: center;
        gap: 0.25rem;
        padding: 0.65rem 0.85rem;
        border-radius: 0.85rem;
        background: var(--bg-surface-secondary, #f3f6f9);
        border-bottom-left-radius: 0.25rem;
    }
    .agent-dot {
        width: 0.4rem;
        height: 0.4rem;
        border-radius: 50%;
        background: var(--text-muted, #92929c);
        animation: agent-bounce 1.2s infinite;
    }
    .agent-dot:nth-child(2) {
        animation-delay: 0.15s;
    }
    .agent-dot:nth-child(3) {
        animation-delay: 0.3s;
    }

    /* ====== Scroll-to-bottom ====== */
    .agent-scroll-btn {
        position: absolute;
        bottom: 4.5rem;
        left: 50%;
        transform: translateX(-50%);
        background: var(--bg-surface, #ffffff);
        border: 1px solid var(--border-color, #ebedf3);
        border-radius: 50%;
        width: 2rem;
        height: 2rem;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        color: var(--text-secondary, #7e8299);
        transition: background 0.15s;
    }
    .agent-scroll-btn:hover {
        background: var(--bg-hover, #f4f6f9);
    }

    /* ====== Footer ====== */
    .agent-footer {
        max-width: 60rem;
        width: 100%;
        margin: 0 auto;
        flex-shrink: 0;
        padding: 0.5rem 0.75rem;
    }
    .agent-input-box {
        display: flex;
        flex-direction: column;
        border: 1px solid var(--border-color, #ebedf3);
        border-radius: 0.75rem;
        background: var(--bg-input, #f3f6f9);
        transition: border-color 0.15s, box-shadow 0.15s;
    }
    .agent-input-box-focus {
        border-color: var(--border-color-dark, #b5b5c3);
        box-shadow: 0 0 0 1px var(--border-color-dark, #b5b5c3);
    }
    .agent-textarea {
        resize: none;
        border: none;
        padding: 0.65rem 0.75rem;
        padding-bottom: 0.25rem;
        font-size: 1rem;
        line-height: 1.45;
        font-family: inherit;
        font-weight: 450;
        background: transparent;
        color: var(--dark, #181c32);
        outline: none;
        overflow-y: auto;
        scrollbar-width: thin;
        scrollbar-color: transparent transparent;
    }
    .agent-textarea:hover {
        scrollbar-color: var(--border-color, #ebedf3) transparent;
    }
    .agent-textarea::-webkit-scrollbar {
        width: 4px;
    }
    .agent-textarea::-webkit-scrollbar-thumb {
        background: transparent;
        border-radius: 2px;
    }
    .agent-textarea:hover::-webkit-scrollbar-thumb {
        background: var(--border-color, #ebedf3);
    }
    .agent-textarea::placeholder {
        color: var(--text-muted, #92929c);
    }
    .agent-textarea:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }
    .agent-input-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0.35rem 0.5rem 0.5rem 0.75rem;
    }
    .agent-tools-hint {
        font-size: 0.85rem;
        color: var(--text-muted, #92929c);
        font-weight: 450;
    }
    .agent-disclaimer {
        margin: 0;
        padding: 0.35rem 0.25rem 0.15rem;
        font-size: 0.85rem;
        color: var(--text-primary, #7e8299);
        text-align: center;
        flex-shrink: 0;
    }
    .agent-send-btn {
        background: var(--primary, #351dc2);
        color: #ffffff;
        border: none;
        border-radius: 0.5rem;
        width: 2.5rem;
        height: 2.5rem;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        flex-shrink: 0;
        transition: opacity 0.15s;
    }
    .agent-send-btn:hover:not(:disabled) {
        opacity: 0.85;
    }
    .agent-send-btn:disabled {
        opacity: 0.4;
        cursor: default;
    }
    .agent-cancel-btn {
        background: var(--danger, #ff3d60);
    }

    /* ====== Spinner ====== */
    :global(.agent-spinner) {
        animation: agent-spin 0.8s linear infinite;
    }

    /* ====== Animations ====== */
    @keyframes agent-bounce {
        0%,
        60%,
        100% {
            transform: translateY(0);
        }
        30% {
            transform: translateY(-0.35rem);
        }
    }
    @keyframes agent-spin {
        from {
            transform: rotate(0deg);
        }
        to {
            transform: rotate(360deg);
        }
    }

    /* ====== Mobile ====== */
    @media (max-width: 768px) {
        .agent-panel {
            bottom: 0;
            right: 0;
            width: 100vw;
            height: 100vh;
            max-height: 100vh;
            border-radius: 0;
            transform-origin: center center;
        }
    }
</style>

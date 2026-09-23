// All provider and agent settings supported by the backend configuration.
export const aiFields = [
    {key: 'base_url', label: 'URL del provider', type: 'url', help: 'Endpoint compatibile OpenAI. Predefinito: https://api.deepseek.com. Lascia vuoto per usare l’endpoint predefinito del client.', maxLength: 2000},
    {key: 'model', label: 'Modello principale', type: 'text', help: 'Identificativo del modello usato dal bot. Predefinito: deepseek-v4-flash.', maxLength: 255},
    {key: 'cheap_model', label: 'Modello economico', type: 'text', help: 'Impostazione disponibile sul server, riservata a flussi futuri: il bot attuale usa il modello principale. Predefinito: deepseek-v4-flash.', maxLength: 255},
    {key: 'max_iterations', label: 'Iterazioni massime', type: 'number', min: 1, max: 100, help: 'Passaggi del bot per richiesta. Predefinito: 15.'},
    {key: 'max_results', label: 'Risultati massimi', type: 'number', min: 1, max: 100000, help: 'Limite di righe restituite da una query. Predefinito: 5000.'},
    {key: 'query_timeout', label: 'Timeout query (secondi)', type: 'number', min: 1, max: 300, help: 'Tempo massimo per una query sul database PostgreSQL. Predefinito: 10.'},
    {key: 'ws_rate_limit', label: 'Messaggi al minuto', type: 'number', min: 1, max: 1000, help: 'Limite di richieste per utente. Predefinito: 10.'},
    {key: 'ws_timeout', label: 'Timeout risposta (secondi)', type: 'number', min: 1, max: 3600, help: 'Durata massima di elaborazione di un messaggio. Predefinito: 240.'},
    {key: 'history_cap', label: 'Messaggi nella cronologia', type: 'number', min: 2, max: 1000, help: 'Numero massimo di messaggi conservati nel contesto del bot. Predefinito: 50.'},
];

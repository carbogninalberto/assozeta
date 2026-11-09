// All provider and agent settings supported by the backend configuration.
export const aiFields = [
    {key: 'base_url', label: 'URL del provider', type: 'url', help: 'Compatibile OpenAI. Vuoto: https://api.deepseek.com.', maxLength: 2000},
    {key: 'model', label: 'Modello principale', type: 'text', help: 'Predefinito: deepseek-v4-flash.', maxLength: 255},
    {key: 'cheap_model', label: 'Modello economico', type: 'text', help: 'Non ancora usato. Predefinito: deepseek-v4-flash.', maxLength: 255},
    {key: 'max_iterations', label: 'Iterazioni massime', type: 'number', min: 1, max: 100, help: 'Per richiesta. Predefinito: 15.'},
    {key: 'max_results', label: 'Risultati massimi', type: 'number', min: 1, max: 100000, help: 'Righe per query. Predefinito: 5000.'},
    {key: 'query_timeout', label: 'Timeout query (secondi)', type: 'number', min: 1, max: 300, help: 'Predefinito: 10.'},
    {key: 'ws_rate_limit', label: 'Messaggi al minuto', type: 'number', min: 1, max: 1000, help: 'Per utente. Predefinito: 10.'},
    {key: 'ws_timeout', label: 'Timeout risposta (secondi)', type: 'number', min: 1, max: 3600, help: 'Predefinito: 240.'},
    {key: 'history_cap', label: 'Messaggi nella cronologia', type: 'number', min: 2, max: 1000, help: 'Predefinito: 50.'},
];

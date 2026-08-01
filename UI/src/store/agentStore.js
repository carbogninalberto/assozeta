import {writable} from 'svelte/store';

export const isAgentOpen = writable(false);
export const agentProcessing = writable(false);
export const reportSavedTrigger = writable(null);

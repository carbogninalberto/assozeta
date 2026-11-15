/**
 * Theme state for the marketing site.
 *
 * Light is the default and the server-rendered value; a stored dark choice is applied
 * before first paint by the inline script in `src/app.html`. We never follow the OS
 * preference, so the toggle is the only thing that switches theme.
 */

type ThemeMode = 'light' | 'dark';

const STORAGE_KEY = 'assozeta-theme';
const THEME_COLORS: Record<ThemeMode, string> = {
	light: '#ffffff',
	dark: '#131320'
};

class Theme {
	/** Current theme as applied to `<html>`. Server render = light. */
	current = $state<ThemeMode>('light');

	/** Read the class already applied by the pre-paint script. */
	sync() {
		if (typeof document === 'undefined') return;
		const isDark = document.documentElement.classList.contains('dark');
		this.current = isDark ? 'dark' : 'light';

		// The prerendered HTML always carries the light colour; match the browser UI
		// chrome to the theme the pre-paint script restored.
		const meta = document.querySelector('meta[name="theme-color"]');
		if (meta) meta.setAttribute('content', THEME_COLORS[this.current]);
	}

	set(mode: ThemeMode) {
		this.current = mode;

		if (typeof document === 'undefined') return;

		document.documentElement.classList.toggle('dark', mode === 'dark');

		try {
			localStorage.setItem(STORAGE_KEY, mode);
		} catch {
			// Private mode or blocked storage: the choice simply does not persist.
		}

		const meta = document.querySelector('meta[name="theme-color"]');
		if (meta) meta.setAttribute('content', THEME_COLORS[mode]);
	}

	toggle() {
		this.set(this.current === 'dark' ? 'light' : 'dark');
	}
}

export const theme = new Theme();

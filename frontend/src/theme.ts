/** Тема интерфейса: как в системе, светлая или тёмная. Выбор хранится в браузере. */
export type Theme = 'system' | 'light' | 'dark';

const KEY = 'rapporteur.theme';

export function storedTheme(): Theme {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

export function systemDark(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/** Тема, которую видит пользователь сейчас: system раскрывается по настройке ОС. */
export function effectiveTheme(theme: Theme): 'light' | 'dark' {
  if (theme === 'system') return systemDark() ? 'dark' : 'light';
  return theme;
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === 'system') delete root.dataset.theme;
  else root.dataset.theme = theme;
  try {
    if (theme === 'system') window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, theme);
  } catch {
    // Без доступа к хранилищу тема действует до перезагрузки страницы.
  }
}

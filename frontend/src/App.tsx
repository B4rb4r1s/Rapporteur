import { useEffect, useState } from 'react';

import { Home, type ConferenceItem } from './Home';
import { Materials, request } from './Materials';
import { Processing } from './Processing';
import { ReportPanel } from './ReportPanel';
import { Settings } from './Settings';
import { SummaryReview } from './SummaryReview';
import { applyTheme, effectiveTheme, storedTheme, type Theme } from './theme';

type Tab = 'materials' | 'processing' | 'report';
type Route =
  | { page: 'home' }
  | { page: 'settings' }
  | { page: 'conference'; id: string; tab: Tab }
  | { page: 'talk'; id: string; talkId: string };

const tabs: [Tab, string][] = [['materials', 'Материалы'], ['processing', 'Обработка'], ['report', 'Справка']];

/** Адрес страницы в hash: работают «назад», перезагрузка и ссылки на доклад. */
export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  if (parts[0] === 'settings') return { page: 'settings' };
  if (parts[0] === 'c' && parts[1]) {
    if (parts[2] === 'talk' && parts[3]) return { page: 'talk', id: parts[1], talkId: parts[3] };
    const tab = tabs.find(([value]) => value === parts[2])?.[0] ?? 'materials';
    return { page: 'conference', id: parts[1], tab };
  }
  return { page: 'home' };
}

export function routeHash(route: Route): string {
  switch (route.page) {
    case 'home': return '#/';
    case 'settings': return '#/settings';
    case 'conference': return `#/c/${encodeURIComponent(route.id)}/${route.tab}`;
    case 'talk': return `#/c/${encodeURIComponent(route.id)}/talk/${encodeURIComponent(route.talkId)}`;
  }
}

function go(route: Route) {
  window.location.hash = routeHash(route);
}

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const [conference, setConference] = useState<ConferenceItem>();
  const [error, setError] = useState('');
  const conferenceId = route.page === 'conference' || route.page === 'talk' ? route.id : '';

  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);

  useEffect(() => applyTheme(theme), [theme]);

  useEffect(() => {
    if (!conferenceId) return;
    const controller = new AbortController();
    void request(`/api/v1/conferences/${conferenceId}`, { signal: controller.signal })
      .then(async (response) => {
        setConference(await response.json());
        setError('');
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [conferenceId]);

  const dark = effectiveTheme(theme) === 'dark';
  return (
    <>
      <header className="app-header">
        <h1><a href="#/">Репортёр</a></h1>
        <span className="tagline">Справки по материалам научных конференций.</span>
        <nav aria-label="Разделы приложения">
          <button aria-current={route.page === 'home' ? 'page' : undefined} onClick={() => go({ page: 'home' })}>
            Конференции
          </button>
          <button aria-current={route.page === 'settings' ? 'page' : undefined} onClick={() => go({ page: 'settings' })}>
            Управление
          </button>
        </nav>
        <button
          className="theme-toggle"
          aria-label={dark ? 'Включить светлую тему' : 'Включить тёмную тему'}
          title={dark ? 'Светлая тема' : 'Тёмная тема'}
          onClick={() => setTheme(dark ? 'light' : 'dark')}
        >
          {dark ? '☀' : '☾'}
        </button>
      </header>
      <main>
        {route.page === 'home' && <Home onOpen={(id) => go({ page: 'conference', id, tab: 'materials' })} />}
        {route.page === 'settings' && <Settings theme={theme} onTheme={setTheme} />}
        {conferenceId && (
          <>
            <div className="subnav">
              <h2>{conference?.id === conferenceId ? conference.title ?? 'Без названия' : 'Конференция'}</h2>
              <nav aria-label="Разделы конференции" className="row">
                {tabs.map(([value, label]) => (
                  <button
                    key={value}
                    aria-current={
                      (route.page === 'conference' && route.tab === value)
                      || (route.page === 'talk' && value === 'processing') ? 'page' : undefined
                    }
                    onClick={() => go({ page: 'conference', id: conferenceId, tab: value })}
                  >
                    {label}
                  </button>
                ))}
              </nav>
            </div>
            {error && <p role="alert">{error}</p>}
          </>
        )}
        {route.page === 'conference' && route.tab === 'materials' && (
          <Materials
            key={route.id}
            conferenceId={route.id}
            onStarted={() => go({ page: 'conference', id: route.id, tab: 'processing' })}
          />
        )}
        {route.page === 'conference' && route.tab === 'processing' && (
          <Processing
            key={route.id}
            conferenceId={route.id}
            onOpen={(talk) => go({ page: 'talk', id: route.id, talkId: talk.id })}
          />
        )}
        {route.page === 'conference' && route.tab === 'report' && <ReportPanel key={route.id} conferenceId={route.id} />}
        {route.page === 'talk' && (
          <SummaryReview
            key={route.talkId}
            talkId={route.talkId}
            onClose={() => go({ page: 'conference', id: route.id, tab: 'processing' })}
          />
        )}
      </main>
    </>
  );
}

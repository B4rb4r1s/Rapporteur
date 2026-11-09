import { useEffect, useState } from 'react';

import { request } from './Materials';
import type { Theme } from './theme';

type SystemInfo = {
  version: string;
  ollama_url: string;
  ollama_available: boolean;
  ollama_models: string[];
  profiles: { name: string; model: string; reasoning: boolean; prompt_version: string; default: boolean }[];
  templates: { name: string; version: string }[];
  queue: Partial<Record<'queued' | 'running' | 'failed', number>>;
};

const themes: [Theme, string][] = [['system', 'Как в системе'], ['light', 'Светлая'], ['dark', 'Тёмная']];

export function Settings({ theme, onTheme }: { theme: Theme; onTheme: (theme: Theme) => void }) {
  const [info, setInfo] = useState<SystemInfo>();
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    void request('/api/v1/system', { signal: controller.signal })
      .then(async (response) => setInfo(await response.json()))
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, []);

  return (
    <section aria-labelledby="settings-title">
      <div className="card">
        <h2 id="settings-title">Управление</h2>
        <fieldset>
          <legend>Тема оформления</legend>
          <div className="row">
            {themes.map(([value, label]) => (
              <label key={value} className="check">
                <input type="radio" name="theme" checked={theme === value} onChange={() => onTheme(value)} />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        {error && <p role="alert">{error}</p>}
      </div>

      {info && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Установка</h3>
          <div className="kv">
            <div><b>Версия</b>{info.version}</div>
            <div>
              <b>Ollama</b>
              {info.ollama_available
                ? <span className="pill ok">доступна</span>
                : <span className="pill bad">недоступна</span>}
            </div>
            <div><b>В очереди</b>{info.queue.queued ?? 0}</div>
            <div><b>Обрабатывается</b>{info.queue.running ?? 0}</div>
            <div><b>С ошибкой</b>{info.queue.failed ?? 0}</div>
          </div>
          {info.ollama_available && (
            <p className="muted">
              Модели Ollama: {info.ollama_models.length ? info.ollama_models.join(', ') : 'не загружены'}.
            </p>
          )}
          <h3>Профили моделей</h3>
          {info.profiles.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th scope="col">Профиль</th><th scope="col">Модель</th><th scope="col">Рассуждение</th><th scope="col">Промпты</th></tr></thead>
                <tbody>
                  {info.profiles.map((profile) => (
                    <tr key={profile.name}>
                      <th scope="row">
                        {profile.name}{' '}
                        {profile.default && <span className="pill info">по умолчанию</span>}
                      </th>
                      <td>{profile.model}</td>
                      <td>{profile.reasoning ? 'да' : 'нет'}</td>
                      <td>{profile.prompt_version}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="muted">Файл профилей недоступен.</p>}
          <p className="muted">
            Профиль по умолчанию задаётся в profiles.yaml; смена профиля перезапускает шаги сводки.
            Доклады с ошибкой повторяются на вкладке «Обработка» конференции.
          </p>
          <h3>Бланки справки</h3>
          <ul>{info.templates.map((item) => <li key={item.name}>{item.name} — версия {item.version}</li>)}</ul>
        </div>
      )}
    </section>
  );
}

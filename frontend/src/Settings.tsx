import { useEffect, useState } from 'react';

import { request } from './Materials';
import type { Theme } from './theme';

type SystemInfo = {
  version: string;
  mode: 'dev' | 'gpu';
  migration: string;
  profiles: { name: string; model: string; reasoning: boolean; prompt_version: string; default: boolean }[];
  templates: { name: string; version: string }[];
  queue: Record<string, Record<string, number>>;
};
type Job = {
  id: string;
  stage: string | null;
  conference_id: string | null;
  talk_id: string | null;
  error_code: string | null;
  created_at: string;
};

const themes: [Theme, string][] = [['system', 'Как в системе'], ['light', 'Светлая'], ['dark', 'Тёмная']];
const modes = { dev: 'dev — без GPU, шаги модели заменены заглушками', gpu: 'gpu — настоящая модель через Ollama' };
const resources: Record<string, string> = { cpu: 'Процессор', gpu: 'Видеокарта' };
const statuses: Record<string, string> = {
  queued: 'в очереди',
  running: 'идёт',
  succeeded: 'готово',
  failed: 'ошибка',
  cancelled: 'отменено',
  cancel_requested: 'отменяется',
  retry_wait: 'повтор',
};

async function fetchState(signal?: AbortSignal): Promise<[SystemInfo, Job[]]> {
  const info: SystemInfo = await (await request('/api/v1/system', { signal })).json();
  const page: { items: Job[] } = await (await request('/api/v1/jobs?status=failed&limit=50', { signal })).json();
  return [info, page.items];
}

export function Settings({ theme, onTheme }: { theme: Theme; onTheme: (theme: Theme) => void }) {
  const [info, setInfo] = useState<SystemInfo>();
  const [failed, setFailed] = useState<Job[]>([]);
  const [busy, setBusy] = useState('');
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  function show([system, jobs]: [SystemInfo, Job[]]) {
    setInfo(system);
    setFailed(jobs);
  }

  useEffect(() => {
    const controller = new AbortController();
    void fetchState(controller.signal)
      .then(([system, jobs]) => {
        setInfo(system);
        setFailed(jobs);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, []);

  async function retry(job: Job) {
    setBusy('Повтор…');
    setError('');
    setStatus('');
    try {
      await request(`/api/v1/jobs/${job.id}/retry`, { method: 'POST' });
      show(await fetchState());
      setStatus(`Задача «${job.stage}» поставлена заново.`);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy('');
    }
  }

  return (
    <section aria-labelledby="settings-title" aria-busy={!!busy}>
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
        <p role="status">{busy || status}</p>
        {error && <p role="alert">{error}</p>}
      </div>

      {info && (
        <>
          <div className="card">
            <h3 style={{ marginTop: 0 }}>Установка</h3>
            <div className="kv">
              <div><b>Версия</b>{info.version}</div>
              <div><b>Режим воркеров</b>{modes[info.mode] ?? info.mode}</div>
              <div><b>Миграция БД</b>{info.migration}</div>
            </div>
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
            <p className="muted">Профиль по умолчанию задаётся в pipeline/profiles.yaml; смена профиля перезапускает шаги сводки.</p>
            <h3>Бланки справки</h3>
            <ul>{info.templates.map((item) => <li key={item.name}>{item.name} — версия {item.version}</li>)}</ul>
          </div>

          <div className="card">
            <h3 style={{ marginTop: 0 }}>Очередь задач</h3>
            {Object.keys(info.queue).length ? (
              <div className="kv">
                {Object.entries(info.queue).map(([resource, counts]) => (
                  <div key={resource}>
                    <b>{resources[resource] ?? resource}</b>
                    {Object.entries(counts).map(([name, count]) => `${statuses[name] ?? name}: ${count}`).join(', ')}
                  </div>
                ))}
              </div>
            ) : <p className="muted">Задач нет.</p>}
            <h3>Задачи с ошибкой</h3>
            {failed.length ? (
              <div className="table-wrap">
                <table>
                  <thead><tr><th scope="col">Этап</th><th scope="col">Ошибка</th><th scope="col">Создана</th><th scope="col"><span hidden>Действие</span></th></tr></thead>
                  <tbody>
                    {failed.map((job) => (
                      <tr key={job.id}>
                        <th scope="row">{job.stage}</th>
                        <td><span className="pill bad">{job.error_code ?? 'ошибка'}</span></td>
                        <td>{new Date(job.created_at).toLocaleString('ru-RU')}</td>
                        <td><button disabled={!!busy} onClick={() => void retry(job)}>Повторить</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="muted">Ошибок нет.</p>}
          </div>
        </>
      )}
    </section>
  );
}

import { useEffect, useState } from 'react';

import { request } from './Materials';
import type { Progress } from './Processing';

export type ConferenceItem = {
  id: string;
  title: string | null;
  start_date: string | null;
  end_date: string | null;
  place: string | null;
};
type Stats = { talks: number; summaries: number; reviewed: number; started: boolean };

function period(item: ConferenceItem) {
  const format = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString('ru-RU');
  if (!item.start_date) return 'даты не указаны';
  if (!item.end_date || item.end_date === item.start_date) return format(item.start_date);
  return `${format(item.start_date)} – ${format(item.end_date)}`;
}

export async function loadConferences(signal?: AbortSignal) {
  const items: ConferenceItem[] = [];
  for (;;) {
    const result = await request(`/api/v1/conferences?limit=100&offset=${items.length}`, { signal });
    const page: { items: ConferenceItem[]; page: { total: number } } = await result.json();
    items.push(...page.items);
    if (items.length >= page.page.total || !page.items.length) return items;
  }
}

export function Home({ onOpen }: { onOpen: (conferenceId: string) => void }) {
  const [conferences, setConferences] = useState<ConferenceItem[]>();
  const [stats, setStats] = useState<Record<string, Stats>>({});
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState('');
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const items = await loadConferences(controller.signal);
      setConferences(items);
      // Ход работы по каждой конференции; ошибка одной не мешает остальным.
      await Promise.all(items.map(async (item) => {
        try {
          const response = await request(`/api/v1/conferences/${item.id}/progress`, { signal: controller.signal });
          const progress: Progress = await response.json();
          const talks = progress.talks ?? [];
          setStats((previous) => ({
            ...previous,
            [item.id]: {
              talks: talks.length,
              summaries: talks.filter((talk) => talk.summary_number !== null).length,
              reviewed: talks.filter((talk) => talk.status === 'reviewed').length,
              started: progress.processing_started,
            },
          }));
        } catch {
          // Конференция без очереди или удалённая в другом окне остаётся без сводки цифр.
        }
      }));
    })().catch((reason: Error) => {
      if (!controller.signal.aborted) setError(reason.message);
    });
    return () => controller.abort();
  }, []);

  async function create() {
    setBusy('Создание…');
    setError('');
    try {
      const response = await request('/api/v1/conferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title.trim() }),
      });
      const conference: ConferenceItem = await response.json();
      setTitle('');
      onOpen(conference.id);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy('');
    }
  }

  async function remove(item: ConferenceItem) {
    const name = item.title ?? 'Без названия';
    if (!window.confirm(`Удалить конференцию «${name}» вместе с реестром, сводками и справками? Отменить удаление нельзя.`)) return;
    setBusy('Удаление…');
    setError('');
    setStatus('');
    try {
      const current = await request(`/api/v1/conferences/${item.id}`);
      await request(`/api/v1/conferences/${item.id}`, {
        method: 'DELETE',
        headers: { 'If-Match': current.headers.get('ETag') ?? '' },
      });
      setConferences((previous) => previous?.filter(({ id }) => id !== item.id));
      setStatus(`Конференция «${name}» удалена.`);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy('');
    }
  }

  return (
    <section aria-labelledby="home-title" aria-busy={!!busy}>
      <div className="card">
        <h2 id="home-title">Конференции</h2>
        <p className="muted">
          Загрузите папку с материалами конференции, подтвердите привязки файлов к докладам, проверьте
          сводки и соберите справку по бланку.
        </p>
        <form className="row" onSubmit={(event) => { event.preventDefault(); void create(); }}>
          <label className="field">
            <span>Название новой конференции</span>
            <input value={title} onChange={(event) => setTitle(event.target.value)} required />
          </label>
          <button className="primary" disabled={!!busy || !title.trim()} style={{ alignSelf: 'end' }}>Создать</button>
        </form>
        <p role="status">{busy || status}</p>
        {error && <p role="alert">{error}</p>}
      </div>
      {conferences && !conferences.length && <p className="muted">Конференций пока нет.</p>}
      <div className="conference-list">
        {conferences?.map((item) => {
          const counts = stats[item.id];
          return (
            <article key={item.id} className="card" aria-label={item.title ?? 'Без названия'}>
              <h3>{item.title ?? 'Без названия'}</h3>
              <span className="muted">{period(item)}{item.place && ` · ${item.place}`}</span>
              {counts && (
                <div className="kv">
                  <div><b>Доклады</b>{counts.talks}</div>
                  <div><b>Сводки</b>{counts.summaries}</div>
                  <div><b>Проверено</b>{counts.reviewed}</div>
                </div>
              )}
              {counts && !counts.started && <span className="pill">обработка не запущена</span>}
              <div className="row">
                <button className="primary" onClick={() => onOpen(item.id)}>Открыть</button>
                <button className="danger" disabled={!!busy} onClick={() => void remove(item)}>Удалить</button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

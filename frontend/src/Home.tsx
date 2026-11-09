import { useEffect, useState } from 'react';

import { request } from './Materials';

export type ConferenceItem = {
  id: string;
  title: string | null;
  start_date: string | null;
  end_date: string | null;
  place: string | null;
  organizer: string | null;
  processing_started: boolean;
  talks: number;
  summaries: number;
  reviewed: number;
};

function period(item: ConferenceItem) {
  const format = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString('ru-RU');
  if (!item.start_date) return 'даты не указаны';
  if (!item.end_date || item.end_date === item.start_date) return format(item.start_date);
  return `${format(item.start_date)} – ${format(item.end_date)}`;
}

export function Home({ onOpen }: { onOpen: (conferenceId: string) => void }) {
  const [conferences, setConferences] = useState<ConferenceItem[]>();
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState('');
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const response = await request('/api/v1/conferences', { signal: controller.signal });
      setConferences(await response.json());
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
      await request(`/api/v1/conferences/${item.id}`, { method: 'DELETE' });
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
        {conferences?.map((item) => (
          <article key={item.id} className="card" aria-label={item.title ?? 'Без названия'}>
            <h3>{item.title ?? 'Без названия'}</h3>
            <span className="muted">{period(item)}{item.place && ` · ${item.place}`}</span>
            <div className="kv">
              <div><b>Доклады</b>{item.talks}</div>
              <div><b>Сводки</b>{item.summaries}</div>
              <div><b>Проверено</b>{item.reviewed}</div>
            </div>
            {!item.processing_started && <span className="pill">обработка не запущена</span>}
            <div className="row">
              <button className="primary" onClick={() => onOpen(item.id)}>Открыть</button>
              <button className="danger" disabled={!!busy} onClick={() => void remove(item)}>Удалить</button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

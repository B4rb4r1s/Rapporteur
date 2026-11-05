import { useEffect, useState } from 'react';

import { Materials, request, type Conference } from './Materials';

export function App() {
  const [conferences, setConferences] = useState<Conference[]>([]);
  const [selected, setSelected] = useState('');
  const [title, setTitle] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function loadConferences() {
      const items: Conference[] = [];
      for (;;) {
        const result = await request(`/api/v1/conferences?limit=100&offset=${items.length}`, {
          signal: controller.signal,
        });
        const page: { items: Conference[]; page: { total: number } } = await result.json();
        items.push(...page.items);
        if (items.length >= page.page.total || !page.items.length) break;
      }
      setConferences((current) => [
        ...items,
        ...current.filter((conference) => !items.some(({ id }) => id === conference.id)),
      ]);
    }
    void loadConferences().catch((reason: Error) => {
      if (!controller.signal.aborted) setError(reason.message);
    });
    return () => controller.abort();
  }, []);

  async function createConference() {
    setSaving(true);
    setError('');
    try {
      const response = await request('/api/v1/conferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title.trim() }),
      });
      const conference: Conference = await response.json();
      setConferences((previous) => [...previous.filter(({ id }) => id !== conference.id), conference]);
      setSelected(conference.id);
      setTitle('');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <main>
      <h1>Репортёр</h1>
      <p>Справки по материалам научных конференций.</p>
      <label>
        Конференция{' '}
        <select value={selected} onChange={(event) => setSelected(event.target.value)}>
          <option value="">Выберите конференцию</option>
          {conferences.map((conference) => (
            <option key={conference.id} value={conference.id}>{conference.title ?? 'Без названия'}</option>
          ))}
        </select>
      </label>
      <form onSubmit={(event) => { event.preventDefault(); void createConference(); }}>
        <label>
          Название новой конференции{' '}
          <input value={title} onChange={(event) => setTitle(event.target.value)} required />
        </label>{' '}
        <button disabled={saving || !title.trim()}>Создать</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {selected && <Materials key={selected} conferenceId={selected} />}
    </main>
  );
}

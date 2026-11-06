import { useEffect, useState } from 'react';

import { Materials, request, type Conference } from './Materials';
import { Processing, type TalkProgress } from './Processing';
import { ReportPanel } from './ReportPanel';
import { SummaryReview } from './SummaryReview';

type Tab = 'materials' | 'processing' | 'report';
const tabs: [Tab, string][] = [['materials', 'Материалы'], ['processing', 'Обработка'], ['report', 'Справка']];

export function App() {
  const [conferences, setConferences] = useState<Conference[]>([]);
  const [selected, setSelected] = useState('');
  const [title, setTitle] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<Tab>('materials');
  const [talk, setTalk] = useState<TalkProgress>();

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
      choose(conference.id);
      setTitle('');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  }

  function choose(conferenceId: string) {
    setSelected(conferenceId);
    setTab('materials');
    setTalk(undefined);
  }

  return (
    <>
      <header className="app-header">
        <h1>Репортёр</h1>
        <span className="tagline">Справки по материалам научных конференций.</span>
        {selected && (
          <nav aria-label="Разделы конференции">
            {tabs.map(([value, label]) => (
              <button
                key={value}
                aria-current={tab === value ? 'page' : undefined}
                onClick={() => { setTab(value); setTalk(undefined); }}
              >
                {label}
              </button>
            ))}
          </nav>
        )}
      </header>
      <main>
        <div className="card">
          <div className="row">
            <label className="field">
              <span>Конференция</span>
              <select value={selected} onChange={(event) => choose(event.target.value)}>
                <option value="">Выберите конференцию</option>
                {conferences.map((conference) => (
                  <option key={conference.id} value={conference.id}>{conference.title ?? 'Без названия'}</option>
                ))}
              </select>
            </label>
            <form className="row" onSubmit={(event) => { event.preventDefault(); void createConference(); }}>
              <label className="field">
                <span>Название новой конференции</span>
                <input value={title} onChange={(event) => setTitle(event.target.value)} required />
              </label>
              <button disabled={saving || !title.trim()} style={{ alignSelf: 'end' }}>Создать</button>
            </form>
          </div>
          {error && <p role="alert">{error}</p>}
        </div>
        {selected && tab === 'materials' && <Materials key={selected} conferenceId={selected} />}
        {selected && tab === 'processing' && (talk
          ? <SummaryReview talkId={talk.id} title={talk.title ?? 'Доклад без названия'} onClose={() => setTalk(undefined)} />
          : <Processing key={selected} conferenceId={selected} onOpen={setTalk} />)}
        {selected && tab === 'report' && <ReportPanel key={selected} conferenceId={selected} />}
      </main>
    </>
  );
}

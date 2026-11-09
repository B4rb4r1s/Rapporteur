import { useEffect, useState } from 'react';

import type { ConferenceItem } from './Home';
import { request } from './Materials';
import type { Progress } from './Processing';

type ReportVersion = {
  id: string;
  number: number;
  created_at: string;
  files: { format: 'docx' | 'md' | 'pdf' }[];
  variables: { talks_count: number };
  warnings: string[];
};
const json = { 'Content-Type': 'application/json' };
const formats: Record<string, string> = { docx: 'DOCX', md: 'Markdown', pdf: 'PDF' };

export function ReportPanel({ conferenceId }: { conferenceId: string }) {
  const base = `/api/v1/conferences/${conferenceId}`;
  const [conference, setConference] = useState<ConferenceItem>();
  const [dates, setDates] = useState({ start: '', end: '', place: '' });
  const [talks, setTalks] = useState(0);
  const [versions, setVersions] = useState<ReportVersion[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState('');
  const [status, setStatus] = useState('');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      const signal = controller.signal;
      const value: ConferenceItem = await (await request(base, { signal })).json();
      setConference(value);
      setDates({ start: value.start_date ?? '', end: value.end_date ?? '', place: value.place ?? '' });
      const progress: Progress = await (await request(`${base}/progress`, { signal })).json();
      setTalks(progress.talks.length);
      setVersions(await (await request(`${base}/reports`, { signal })).json());
    }
    void load().catch((reason: Error) => {
      if (!controller.signal.aborted) setError(reason.message);
    });
    return () => controller.abort();
  }, [base]);

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError('');
    setStatus('');
    setWarnings([]);
    try {
      await action();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy('');
    }
  }

  function saveDates() {
    if (!conference) return;
    void run('Сохранение…', async () => {
      const response = await request(base, {
        method: 'PUT',
        headers: json,
        body: JSON.stringify({
          title: conference.title,
          start_date: dates.start || null,
          end_date: dates.end || dates.start || null,
          place: dates.place.trim() || null,
          organizer: conference.organizer,
        }),
      });
      setConference(await response.json());
      setStatus('Сведения о конференции сохранены.');
    });
  }

  function build() {
    // DOCX по бланку и PDF через LibreOffice собираются за несколько секунд прямо в запросе.
    void run('Справка собирается…', async () => {
      const response = await request(`${base}/reports`, {
        method: 'POST',
        headers: json,
        body: JSON.stringify({ unreviewed_confirmed: confirmed }),
      });
      const version: ReportVersion = await response.json();
      setVersions((previous) => [version, ...previous]);
      setStatus(`Собрана версия ${version.number}.`);
      setWarnings(version.warnings);
    });
  }

  return (
    <section aria-labelledby="report-title" aria-busy={!!busy}>
      <div className="card">
        <h2 id="report-title">Справка</h2>
        <p role="status">{busy && <span className="spinner" aria-hidden="true" />}{busy || status}</p>
        {error && <p role="alert">{error}</p>}
        {!!warnings.length && <ul className="muted">{warnings.map((item) => <li key={item}>{item}</li>)}</ul>}
        {conference && (
          <form onSubmit={(event) => { event.preventDefault(); saveDates(); }}>
            <fieldset>
              <legend>Сведения для справки</legend>
              <div className="fields">
                <label className="field"><span>Начало</span>
                  <input type="date" value={dates.start} onChange={(event) => setDates({ ...dates, start: event.target.value })} />
                </label>
                <label className="field"><span>Окончание</span>
                  <input type="date" value={dates.end} onChange={(event) => setDates({ ...dates, end: event.target.value })} />
                </label>
                <label className="field"><span>Место</span>
                  <input value={dates.place} placeholder="г. Москва" onChange={(event) => setDates({ ...dates, place: event.target.value })} />
                </label>
                <button disabled={!!busy} style={{ alignSelf: 'end' }}>Сохранить</button>
              </div>
              {!conference.start_date && (
                <p className="pill warn" style={{ marginTop: '.8rem' }}>
                  Без дат в справке не будет периода проведения, сборка не пройдёт.
                </p>
              )}
            </fieldset>
          </form>
        )}
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Сборка</h3>
        <p className="muted">
          В справку идут все доклады конференции ({talks}). Доклады без готовой сводки в неё не войдут.
        </p>
        <div className="row">
          <button className="primary" disabled={!!busy || !talks} onClick={build}>Собрать справку</button>
          <label className="check">
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
            Собрать, даже если не все сводки проверены
          </label>
        </div>
        <h3>Версии</h3>
        {versions.length > 0 ? (
          <ul className="versions">
            {versions.map((version) => (
              <li key={version.id}>
                <b>Версия {version.number}</b>{' '}
                <span className="muted">
                  от {new Date(version.created_at).toLocaleString('ru-RU')}, докладов {version.variables.talks_count}
                </span>{' '}
                <span className="files">
                  {version.files.map((file) => (
                    <a key={file.format} className="pill info" href={`/api/v1/report-versions/${version.id}/files/${file.format}`}>
                      {formats[file.format]}
                    </a>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        ) : <p className="muted">Собранных версий пока нет.</p>}
      </div>
    </section>
  );
}

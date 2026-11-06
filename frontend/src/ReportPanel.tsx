import { useCallback, useEffect, useState } from 'react';

import { request } from './Materials';
import type { Progress } from './Processing';

type ConferenceFull = {
  id: string;
  title: string | null;
  start_date: string | null;
  end_date: string | null;
  place: string | null;
  organizer: string | null;
};
type Report = { id: string; selection: { talk_ids: string[] } };
type ReportVersion = {
  id: string;
  number: number;
  created_at: string;
  files: { format: 'docx' | 'md' | 'pdf' }[];
  variables: { talks_count: number };
};
const json = { 'Content-Type': 'application/json' };
const formats: Record<string, string> = { docx: 'DOCX', md: 'Markdown', pdf: 'PDF' };
const POLL_MS = 3000;

export function ReportPanel({ conferenceId }: { conferenceId: string }) {
  const base = `/api/v1/conferences/${conferenceId}`;
  const [conference, setConference] = useState<{ value: ConferenceFull; etag: string }>();
  const [dates, setDates] = useState({ start: '', end: '', place: '' });
  const [report, setReport] = useState<Report | null>();
  const [versions, setVersions] = useState<ReportVersion[]>([]);
  const [talkIds, setTalkIds] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [waiting, setWaiting] = useState('');
  const [busy, setBusy] = useState('');
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  const loadVersions = useCallback(async (reportId: string) => {
    const response = await request(`/api/v1/reports/${reportId}/versions?limit=20`);
    const page: { items: ReportVersion[] } = await response.json();
    setVersions(page.items);
    return page.items;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      const signal = controller.signal;
      const conferenceResponse = await request(base, { signal });
      const value: ConferenceFull = await conferenceResponse.json();
      setConference({ value, etag: conferenceResponse.headers.get('ETag') ?? '' });
      setDates({ start: value.start_date ?? '', end: value.end_date ?? '', place: value.place ?? '' });
      const progress: Progress = await (await request(`${base}/progress`, { signal })).json();
      setTalkIds(progress.talks.map((talk) => talk.id));
      const reports: { items: Report[] } = await (await request(`${base}/reports?limit=1`, { signal })).json();
      const current = reports.items[0] ?? null;
      setReport(current);
      if (current) await loadVersions(current.id);
    }
    void load().catch((reason: Error) => {
      if (!controller.signal.aborted) setError(reason.message);
    });
    return () => controller.abort();
  }, [base, loadVersions]);

  // Ждём, пока задача сборки закончится и планировщик перенесёт версию в БД.
  useEffect(() => {
    if (!waiting || !report) return;
    const known = versions.length;
    const timer = setInterval(() => {
      void (async () => {
        const job: { status: string; error_code: string | null } = await (await request(waiting)).json();
        if (job.status === 'failed' || job.status === 'cancelled') {
          setWaiting('');
          setStatus('');
          setError(`Справка не собрана: ${job.error_code ?? job.status}`);
          return;
        }
        const items = await loadVersions(report.id);
        if (items.length > known) {
          setWaiting('');
          setStatus(`Собрана версия ${items[0].number}.`);
        }
      })().catch((reason: Error) => setError(reason.message));
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, report, versions.length, loadVersions]);

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError('');
    setStatus('');
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
        headers: { ...json, 'If-Match': conference.etag },
        body: JSON.stringify({
          title: conference.value.title,
          start_date: dates.start || null,
          end_date: dates.end || dates.start || null,
          place: dates.place.trim() || null,
          organizer: conference.value.organizer,
        }),
      });
      setConference({ value: await response.json(), etag: response.headers.get('ETag') ?? '' });
      setStatus('Сведения о конференции сохранены.');
    });
  }

  function createReport() {
    void run('Создание…', async () => {
      const response = await request(`${base}/reports`, {
        method: 'POST',
        headers: json,
        body: JSON.stringify({ selection: { days: [], section_ids: [], talk_ids: talkIds } }),
      });
      setReport(await response.json());
      setVersions([]);
    });
  }

  function build() {
    if (!report) return;
    void run('Постановка сборки…', async () => {
      const response = await request(`/api/v1/reports/${report.id}/build`, {
        method: 'POST',
        headers: json,
        body: JSON.stringify({ unreviewed_confirmed: confirmed }),
      });
      const accepted: { status_url: string } = await response.json();
      setWaiting(accepted.status_url);
      setStatus('Справка собирается…');
    });
  }

  return (
    <section aria-labelledby="report-title" aria-busy={!!busy || !!waiting}>
      <div className="card">
        <h2 id="report-title">Справка</h2>
        <p role="status">{waiting && <span className="spinner" aria-hidden="true" />}{busy || status}</p>
        {error && <p role="alert">{error}</p>}
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
              {!conference.value.start_date && (
                <p className="pill warn" style={{ marginTop: '.8rem' }}>
                  Без дат в справке не будет периода проведения, сборка не пройдёт.
                </p>
              )}
            </fieldset>
          </form>
        )}
      </div>
      {report === null && (
        <div className="card">
          <button className="primary" disabled={!!busy || !talkIds.length} onClick={createReport}>
            Создать справку по всем докладам
          </button>
        </div>
      )}
      {report && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Сборка</h3>
          <p className="muted">
            В справке докладов: {report.selection.talk_ids.length}. Доклады без готовой сводки в неё не войдут.
          </p>
          <div className="row">
            <button className="primary" disabled={!!busy || !!waiting} onClick={build}>Собрать справку</button>
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
      )}
    </section>
  );
}

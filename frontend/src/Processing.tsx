import { useEffect, useState } from 'react';

import { request, statusTone } from './Materials';

type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'retry_wait';
export type TalkProgress = {
  id: string;
  section: string;
  order: number;
  title: string | null;
  status: string;
  stages: Partial<Record<'pack' | 'facts' | 'verify' | 'summary', JobStatus>>;
  error_code: string | null;
  summary_number: number | null;
  summary_status: 'ready' | 'needs_attention' | 'stale' | null;
};
export type Progress = {
  processing_started: boolean;
  stages: Record<string, Partial<Record<JobStatus, number>>>;
  talks: TalkProgress[];
};

export const talkStatuses: Record<string, string> = {
  draft: 'Черновик',
  matched: 'Материалы привязаны',
  insufficient_data: 'Недостаточно материалов',
  processed: 'Сводка готова, не проверена',
  reviewed: 'Проверен',
  stale: 'Требуется обновление',
  not_held: 'Не состоялся',
};
const jobStatuses: Record<JobStatus, string> = {
  queued: 'в очереди',
  running: 'идёт',
  succeeded: 'готово',
  failed: 'ошибка',
  cancelled: 'отменено',
  retry_wait: 'повтор',
};
const jobTone: Record<JobStatus, string> = {
  queued: '',
  running: 'info',
  succeeded: 'ok',
  failed: 'bad',
  cancelled: '',
  retry_wait: 'warn',
};
const steps = [
  ['pack', 'Пакет'],
  ['facts', 'Факты'],
  ['verify', 'Сверка'],
  ['summary', 'Сводка'],
] as const;
const fileStages = [['inventory', 'Опись'], ['slides', 'Слайды'], ['notes', 'Заметки']] as const;
const POLL_MS = 5000;

function counts(byStatus: Partial<Record<JobStatus, number>> | undefined) {
  if (!byStatus) return null;
  const total = Object.values(byStatus).reduce((sum, value) => sum + (value ?? 0), 0);
  const failed = byStatus.failed ? `, ошибок ${byStatus.failed}` : '';
  return `${byStatus.succeeded ?? 0} из ${total}${failed}`;
}

export function Processing(
  { conferenceId, onOpen }: { conferenceId: string; onOpen: (talk: TalkProgress) => void },
) {
  const [progress, setProgress] = useState<Progress>();
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await request(`/api/v1/conferences/${conferenceId}/progress`, {
          signal: controller.signal,
        });
        setProgress(await response.json());
        setError('');
      } catch (reason) {
        if (!controller.signal.aborted) setError((reason as Error).message);
      }
    }
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [conferenceId]);

  const ready = progress?.talks.filter((talk) => talk.summary_number !== null).length ?? 0;
  return (
    <section className="card" aria-labelledby="processing-title">
      <h2 id="processing-title">Обработка</h2>
      {error && <p role="alert">{error}</p>}
      {progress && !progress.processing_started && (
        <p className="notice">Обработка ещё не запущена: подтвердите привязки на вкладке «Материалы».</p>
      )}
      {progress?.processing_started && !progress.talks.length && (
        <p className="notice">
          В реестре нет докладов, поэтому сводок не будет. На вкладке «Материалы» добавьте доклады
          или загрузите папку, где у каждого доклада своя подпапка, и подтвердите привязки.
        </p>
      )}
      {progress?.processing_started && !!progress.talks.length
        && progress.talks.every((talk) => talk.status === 'draft' || talk.status === 'insufficient_data') && (
        <p className="notice">Ни к одному докладу не привязаны материалы: подтвердите привязки на вкладке «Материалы».</p>
      )}
      {progress && (
        <>
          <div className="kv">
            {fileStages.filter(([stage]) => progress.stages[stage]).map(([stage, label]) => (
              <div key={stage}><b>{label}</b>{counts(progress.stages[stage])}</div>
            ))}
            <div><b>Сводки</b>{ready} из {progress.talks.length}</div>
          </div>
          <div className="row">
            <div className="progressbar" aria-hidden="true">
              <span style={{ width: `${progress.talks.length ? (100 * ready) / progress.talks.length : 0}%` }} />
            </div>
            <small>Сводки готовы у {ready} из {progress.talks.length} докладов. Обновляется каждые 5 с.</small>
          </div>
          <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Доклад</th>
                <th scope="col">Статус</th>
                {steps.map(([, label]) => <th key={label} scope="col">{label}</th>)}
                <th scope="col"><span hidden>Действие</span></th>
              </tr>
            </thead>
            <tbody>
              {progress.talks.map((talk) => (
                <tr key={talk.id}>
                  <th scope="row">{talk.section} · {talk.order}. {talk.title ?? 'Без названия'}</th>
                  <td>
                    <span className={`pill ${statusTone[talk.status] ?? ''}`}>{talkStatuses[talk.status] ?? talk.status}</span>
                    {talk.summary_status === 'needs_attention' && <><br /><small>Сводка требует внимания</small></>}
                    {talk.error_code && <><br /><small>Ошибка: {talk.error_code}</small></>}
                  </td>
                  {steps.map(([stage]) => {
                    const job = talk.stages[stage];
                    return (
                      <td key={stage}>
                        {job ? (
                          <span className={`pill ${jobTone[job]}`}>
                            {job === 'running' && <span className="spinner" aria-hidden="true" />}
                            {jobStatuses[job]}
                          </span>
                        ) : <span className="muted">—</span>}
                      </td>
                    );
                  })}
                  <td>
                    <button disabled={talk.summary_number === null} onClick={() => onOpen(talk)}>
                      Открыть сводку
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </>
      )}
    </section>
  );
}

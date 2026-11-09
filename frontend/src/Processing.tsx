import { useEffect, useState } from 'react';

import { request, statusTone } from './Materials';

type Job = 'queued' | 'running' | 'done' | 'failed';
export type TalkProgress = {
  id: string;
  section: string;
  order: number;
  title: string | null;
  status: string;
  job: Job | null;
  step: 'parse' | 'facts' | 'verify' | 'summary' | null;
  error: string | null;
  summary_number: number | null;
  summary_status: 'ready' | 'needs_attention' | 'stale' | null;
};
export type Progress = {
  processing_started: boolean;
  files: number;
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
const steps: Record<string, string> = {
  parse: 'разбор материалов',
  facts: 'факты и цитаты',
  verify: 'сверка цитат',
  summary: 'текст сводки',
};
const POLL_MS = 5000;

function jobLabel(talk: TalkProgress) {
  switch (talk.job) {
    case 'queued': return ['в очереди', ''];
    case 'running': return [talk.step ? steps[talk.step] : 'идёт', 'info'];
    case 'done': return ['готово', 'ok'];
    case 'failed': return ['ошибка', 'bad'];
    default: return null;
  }
}

export function Processing(
  { conferenceId, onOpen }: { conferenceId: string; onOpen: (talk: TalkProgress) => void },
) {
  const [progress, setProgress] = useState<Progress>();
  const [error, setError] = useState('');
  const [tick, setTick] = useState(0);

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
  }, [conferenceId, tick]);

  async function retry(talk: TalkProgress) {
    try {
      await request(`/api/v1/talks/${talk.id}/retry`, { method: 'POST' });
      setTick((value) => value + 1);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

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
            <div><b>Файлы</b>{progress.files}</div>
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
                <th scope="col">Обработка</th>
                <th scope="col"><span hidden>Действие</span></th>
              </tr>
            </thead>
            <tbody>
              {progress.talks.map((talk) => {
                const job = jobLabel(talk);
                return (
                  <tr key={talk.id}>
                    <th scope="row">{talk.section} · {talk.order}. {talk.title ?? 'Без названия'}</th>
                    <td>
                      <span className={`pill ${statusTone[talk.status] ?? ''}`}>{talkStatuses[talk.status] ?? talk.status}</span>
                      {talk.summary_status === 'needs_attention' && <><br /><small>Сводка требует внимания</small></>}
                    </td>
                    <td>
                      {job ? (
                        <span className={`pill ${job[1]}`}>
                          {talk.job === 'running' && <span className="spinner" aria-hidden="true" />}
                          {job[0]}
                        </span>
                      ) : <span className="muted">—</span>}
                      {talk.error && <><br /><small>{talk.error}</small></>}
                    </td>
                    <td className="row">
                      <button disabled={talk.summary_number === null} onClick={() => onOpen(talk)}>
                        Открыть сводку
                      </button>
                      {talk.job === 'failed' && <button onClick={() => void retry(talk)}>Повторить</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </>
      )}
    </section>
  );
}

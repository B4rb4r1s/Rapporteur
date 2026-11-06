import { useEffect, useState } from 'react';

import { request } from './Materials';

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
    <section aria-labelledby="processing-title">
      <h2 id="processing-title">Обработка</h2>
      {error && <p role="alert">{error}</p>}
      {progress && !progress.processing_started && (
        <p>Обработка ещё не запущена: подтвердите привязки на вкладке «Материалы».</p>
      )}
      {progress && (
        <>
          <p>
            {fileStages
              .filter(([stage]) => progress.stages[stage])
              .map(([stage, label]) => `${label}: ${counts(progress.stages[stage])}`)
              .join(' · ') || 'Задач пока нет.'}
          </p>
          <p>Сводки готовы у {ready} из {progress.talks.length} докладов. Обновляется каждые 5 с.</p>
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
                    {talkStatuses[talk.status] ?? talk.status}
                    {talk.summary_status === 'needs_attention' && <><br /><small>Сводка требует внимания</small></>}
                    {talk.error_code && <><br /><small>Ошибка: {talk.error_code}</small></>}
                  </td>
                  {steps.map(([stage]) => (
                    <td key={stage}>{talk.stages[stage] ? jobStatuses[talk.stages[stage]] : '—'}</td>
                  ))}
                  <td>
                    <button disabled={talk.summary_number === null} onClick={() => onOpen(talk)}>
                      Открыть сводку
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

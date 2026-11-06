import { useEffect, useState, type ReactNode } from 'react';

import { request, statusTone } from './Materials';
import { talkStatuses } from './Processing';

type Line = { text: string; claim_ids: string[] };
type Evidence = { label: string; quote: string; fragment_revision_id: string; span: [number, number] };
type Rejected = { label: string; quote: string; reason: string };
type ClaimStatus = 'rejected' | 'verified' | 'needs_human' | 'confirmed';
type Claim = {
  key: string;
  type: string;
  text: string;
  status: ClaimStatus;
  evidence: Evidence[];
  rejected_evidence: Rejected[];
};
type Locator =
  | { kind: 'page'; page_no: number }
  | { kind: 'time'; start_ms: number; end_ms: number }
  | { kind: 'lines'; line_start: number; line_end: number };
type Fragment = {
  label: string;
  kind: string;
  revision_id: string;
  source_file_id: string;
  locator: Locator;
  text: string;
};
type Summary = {
  version: number;
  status: 'ready' | 'needs_attention' | 'stale';
  profile: string;
  claims: Claim[];
  sentences: Line[];
  conclusions: Line[];
  edited_by: string | null;
  created_at: string;
};
export type SummaryView = { summary: Summary; pack: { fragments: Fragment[] }; talk_status: string };
type Draft = { sentences: Line[]; conclusions: Line[]; claims: Record<string, string> };

const claimTypes: Record<string, string> = {
  problem: 'задача',
  method: 'метод',
  result: 'результат',
  conclusion: 'вывод',
  listener_remark: 'реплика слушателя',
};
const claimStatuses: Record<ClaimStatus, string> = {
  rejected: 'цитата не найдена',
  verified: 'цитата найдена, не проверено человеком',
  needs_human: 'нужна проверка человеком',
  confirmed: 'подтверждено',
};
const claimTone: Record<ClaimStatus, string> = {
  rejected: 'bad',
  verified: 'info',
  needs_human: 'warn',
  confirmed: 'ok',
};
const rejections: Record<string, string> = {
  label_not_found: 'метки нет в материалах',
  quote_not_found: 'цитата не найдена',
  below_threshold: 'совпадение слишком слабое',
  number_mismatch: 'не совпали числа',
  negation_mismatch: 'не совпало отрицание',
  too_short: 'цитата слишком короткая',
};
const json = { 'Content-Type': 'application/json' };

/** Диапазон цитаты считается в кодовых точках (ADR-003), а строки JS — в UTF-16:
 * Array.from режет по кодовым точкам, поэтому символы вне базовой плоскости не ломают подсветку. */
export function highlight(text: string, [start, end]: [number, number]): ReactNode {
  const points = Array.from(text);
  return (
    <>
      {points.slice(0, start).join('')}
      <mark>{points.slice(start, end).join('')}</mark>
      {points.slice(end).join('')}
    </>
  );
}

function place(fragment: Fragment) {
  const { locator } = fragment;
  if (locator.kind === 'page') return `слайд ${locator.page_no}`;
  if (locator.kind === 'lines') return `строки ${locator.line_start}–${locator.line_end}`;
  return `с ${Math.floor(locator.start_ms / 1000)} с`;
}

export function SummaryReview(
  { talkId, title, onClose }: { talkId: string; title: string; onClose: () => void },
) {
  const url = `/api/v1/talks/${talkId}/summary`;
  const [snapshot, setSnapshot] = useState<{ view: SummaryView; etag: string }>();
  const [selected, setSelected] = useState('');
  const [draft, setDraft] = useState<Draft>();
  const [busy, setBusy] = useState('');
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  async function read(response: Response) {
    const etag = response.headers.get('ETag');
    if (!etag) throw new Error('Сервер не вернул версию сводки');
    const view: SummaryView = await response.json();
    setSnapshot({ view, etag });
  }

  useEffect(() => {
    const controller = new AbortController();
    void request(url, { signal: controller.signal })
      .then(read)
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [url]);

  async function act(label: string, done: string, call: () => Promise<Response>) {
    setBusy(label);
    setError('');
    setStatus('');
    try {
      await read(await call());
      setDraft(undefined);
      setStatus(done);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy('');
    }
  }

  if (!snapshot) {
    return (
      <section aria-label="Проверка сводки">
        <p><button className="link" onClick={onClose}><span aria-hidden="true">← </span>К списку докладов</button></p>
        {error ? <p role="alert">{error}</p> : <p className="muted"><span className="spinner" aria-hidden="true" />Загрузка сводки…</p>}
      </section>
    );
  }

  const { summary, pack, talk_status: talkStatus } = snapshot.view;
  const claims = new Map(summary.claims.map((claim) => [claim.key, claim]));
  const fragments = new Map(pack.fragments.map((fragment) => [fragment.revision_id, fragment]));
  const byLabel = new Map(pack.fragments.map((fragment) => [fragment.label, fragment]));
  const claim = claims.get(selected);
  const used = new Set([...summary.sentences, ...summary.conclusions].flatMap((line) => line.claim_ids));

  function startEdit() {
    setDraft({
      sentences: summary.sentences.map((line) => ({ ...line })),
      conclusions: summary.conclusions.map((line) => ({ ...line })),
      claims: Object.fromEntries(summary.claims.map((item) => [item.key, item.text])),
    });
  }

  function save() {
    if (!draft || !snapshot) return;
    const edited = summary.claims
      .filter((item) => draft.claims[item.key].trim() && draft.claims[item.key] !== item.text)
      .map((item) => ({ key: item.key, text: draft.claims[item.key].trim() }));
    const lines = (items: Line[]) => items
      .map((line) => ({ ...line, text: line.text.trim() }))
      .filter((line) => line.text);
    void act('Сохранение…', 'Правка сохранена, создана новая версия.', () => request(url, {
      method: 'PUT',
      headers: { ...json, 'If-Match': snapshot.etag },
      body: JSON.stringify({
        sentences: lines(draft.sentences),
        conclusions: lines(draft.conclusions),
        claims: edited,
      }),
    }));
  }

  function lineEditor(kind: 'sentences' | 'conclusions', label: string) {
    if (!draft) return null;
    return draft[kind].map((line, index) => (
      <li key={index}>
        <textarea
          aria-label={`${label} ${index + 1}`}
          value={line.text}
          rows={3}
          cols={80}
          disabled={!!busy}
          onChange={(event) => {
            const text = event.target.value;
            setDraft((previous) => previous && ({
              ...previous,
              [kind]: previous[kind].map((item, position) => (position === index ? { ...item, text } : item)),
            }));
          }}
        />
        <br /><small>Опирается на {line.claim_ids.join(', ')}</small>
      </li>
    ));
  }

  function lineView(line: Line, index: number) {
    return (
      <li key={index}>
        {line.text}{' '}
        {line.claim_ids.map((key) => (
          <button
            key={key}
            className="chip"
            aria-pressed={selected === key}
            title={claims.get(key)?.text}
            onClick={() => setSelected(selected === key ? '' : key)}
          >
            {key}
          </button>
        ))}
      </li>
    );
  }

  const lines = (kind: 'sentences' | 'conclusions') => summary[kind];
  return (
    <section aria-labelledby="summary-title" aria-busy={!!busy}>
      <p><button className="link" onClick={onClose}><span aria-hidden="true">← </span>К списку докладов</button></p>
      <div className="card">
        <h2 id="summary-title">{title}</h2>
        <div className="kv">
          <div><b>Доклад</b><span className={`pill ${statusTone[talkStatus] ?? ''}`}>{talkStatuses[talkStatus] ?? talkStatus}</span></div>
          <div><b>Версия сводки</b>{summary.version}</div>
          <div><b>Автор версии</b>{summary.edited_by ? 'правка человека' : `модель, профиль ${summary.profile}`}</div>
          {summary.status === 'needs_attention' && (
            <div><b>Проверки</b><span className="pill warn">не пройдены, нужна правка</span></div>
          )}
        </div>
        <p role="status">{busy || status}</p>
        {error && <p role="alert">{error}</p>}
      </div>

      <div className="review">
        <div className="card">
          <h3>Сводка</h3>
          {draft
            ? <ol className="summary-text">{lineEditor('sentences', 'Предложение')}</ol>
            : lines('sentences').length
              ? <ol className="summary-text">{lines('sentences').map(lineView)}</ol>
              : <p className="muted">Текста сводки нет.</p>}
          <h3>Выводы</h3>
          {draft
            ? (draft.conclusions.length ? <ul className="summary-text">{lineEditor('conclusions', 'Вывод')}</ul> : <p className="muted">Выводов нет.</p>)
            : lines('conclusions').length
              ? <ul className="summary-text">{lines('conclusions').map(lineView)}</ul>
              : <p className="muted">Выводов нет.</p>}

          {draft && (
            <>
              <h3>Утверждения</h3>
              <p className="muted">Правка текста утверждения снимает с него подтверждение.</p>
              {summary.claims.filter((item) => used.has(item.key)).map((item) => (
                <label key={item.key} className="field" style={{ marginBottom: '.5rem' }}>
                  <span>{item.key} · {claimTypes[item.type] ?? item.type}</span>
                  <input
                    value={draft.claims[item.key]}
                    disabled={!!busy}
                    onChange={(event) => {
                      const text = event.target.value;
                      setDraft((previous) => previous && ({
                        ...previous, claims: { ...previous.claims, [item.key]: text },
                      }));
                    }}
                  />
                </label>
              ))}
            </>
          )}

          <div className="row" style={{ marginTop: '1rem' }}>
            {draft ? (
              <>
                <button className="primary" disabled={!!busy} onClick={save}>Сохранить правку</button>
                <button disabled={!!busy} onClick={() => setDraft(undefined)}>Отменить</button>
              </>
            ) : (
              <>
                <button
                  className="primary"
                  disabled={!!busy || talkStatus === 'reviewed' || !summary.sentences.length}
                  onClick={() => void act('Подтверждение…', 'Сводка проверена.', () => request(`${url}/review`, {
                    method: 'POST',
                    headers: { 'If-Match': snapshot.etag },
                  }))}
                >
                  Подтвердить проверку
                </button>
                <button disabled={!!busy} onClick={startEdit}>Править</button>
                <button
                  disabled={!!busy}
                  onClick={() => void act('Обновление…', 'Загружена текущая версия.', () => request(url))}
                >
                  Обновить
                </button>
              </>
            )}
          </div>
        </div>

        {claim ? (
          <aside className="card claim-card" aria-label={`Утверждение ${claim.key}`}>
            <h3 style={{ marginTop: 0 }}>{claim.key} · {claimTypes[claim.type] ?? claim.type}</h3>
            <p>{claim.text}</p>
            <p><span className={`pill ${claimTone[claim.status]}`}>{claimStatuses[claim.status]}</span></p>
            {claim.evidence.map((item, index) => {
              const fragment = fragments.get(item.fragment_revision_id);
              return (
                <figure key={index}>
                  <figcaption>
                    {item.label}{fragment && ` · ${place(fragment)}`}
                  </figcaption>
                  {fragment && <blockquote>{highlight(fragment.text, item.span)}</blockquote>}
                  {fragment?.locator.kind === 'page' && fragment.kind.startsWith('slide') && (
                    <img
                      src={`/api/v1/source-files/${fragment.source_file_id}/slides/${fragment.locator.page_no}`}
                      alt={`Слайд ${fragment.locator.page_no}`}
                      loading="lazy"
                    />
                  )}
                </figure>
              );
            })}
            {claim.rejected_evidence.map((item, index) => (
              <p key={`rejected-${index}`}>
                <small>
                  Не подтверждено ({rejections[item.reason] ?? item.reason}): {item.label}
                  {byLabel.has(item.label) ? '' : ' (метки нет в пакете)'} — «{item.quote}»
                </small>
              </p>
            ))}
          </aside>
        ) : (
          <div className="card claim-card muted">
            Нажмите на ссылку утверждения рядом с предложением, например c1, чтобы увидеть
            цитату из источника и слайд.
          </div>
        )}
      </div>
    </section>
  );
}

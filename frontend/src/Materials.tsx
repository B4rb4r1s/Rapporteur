import { useEffect, useState } from 'react';

export type Conference = { id: string; title: string | null };
type Section = { id: string; title: string };
type Talk = {
  id: string;
  section_id: string;
  section: string;
  order: number;
  title: string | null;
  status: string;
};
type Material = {
  source: {
    id: string;
    kind: 'presentation' | 'audio' | 'video' | 'notes' | 'program';
    encoding: string | null;
    duration_ms: number | null;
  };
  relative_path: string;
  title_candidates: { title: string | null; speakers: string[] } | null;
  warning: string | null;
  binding: { confirmed: boolean; talk_id: string | null };
  suggested_talk_id: string | null;
  confidence: number | null;
  alternate_of: string | null;
};
type Review = { processing_started: boolean; files: Material[]; sections: Section[]; talks: Talk[] };
const json = { 'Content-Type': 'application/json' };

const kinds: Record<Material['source']['kind'], string> = {
  presentation: 'Презентация',
  audio: 'Аудио',
  video: 'Видео',
  notes: 'Заметки',
  program: 'Программа',
};
// Цвет статуса доклада: зелёный — проверен, жёлтый — ждёт человека, синий — в работе.
export const statusTone: Record<string, string> = {
  matched: 'info',
  processed: 'warn',
  reviewed: 'ok',
  stale: 'warn',
  insufficient_data: 'bad',
};
const statuses: Record<string, string> = {
  draft: 'Черновик',
  matched: 'Материалы привязаны',
  insufficient_data: 'Недостаточно материалов',
  processed: 'Обработан',
  reviewed: 'Проверен',
  stale: 'Требуется обновление',
  not_held: 'Не состоялся',
};

export async function request(path: string, init?: RequestInit) {
  const response = await fetch(path, init);
  if (!response.ok) {
    const error: { message?: string } | null = await response.json().catch(() => null);
    throw new Error(error?.message ?? `Ошибка HTTP ${response.status}`);
  }
  return response;
}

async function readReview(response: Response): Promise<Review> {
  return response.json();
}

function selectionFor(file: Material) {
  if (file.binding.confirmed) return file.binding.talk_id ?? '';
  if (file.alternate_of) return '';
  return file.suggested_talk_id ?? '';
}

export function Materials(
  { conferenceId, onStarted }: { conferenceId: string; onStarted?: () => void },
) {
  const base = `/api/v1/conferences/${conferenceId}`;
  const [review, setReview] = useState<Review>();
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState('');
  const [status, setStatus] = useState('');
  const [errors, setErrors] = useState<string[]>([]);
  const [talkTitle, setTalkTitle] = useState('');
  const [sectionId, setSectionId] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    void request(`${base}/materials`, { signal: controller.signal })
      .then(readReview)
      .then(setReview)
      .catch((error: Error) => {
        if (!controller.signal.aborted) setErrors([error.message]);
      });
    return () => controller.abort();
  }, [base]);

  async function reload() {
    setReview(await readReview(await request(`${base}/materials`)));
    setChoices({});
  }

  async function upload(files: File[]) {
    const failures: string[] = [];
    let uploaded = 0;
    let duplicates = 0;
    setErrors([]);
    setStatus('');
    const entries = files.flatMap((file) => {
      const path = file.webkitRelativePath
        ? file.webkitRelativePath.split('/').slice(1).join('/')
        : file.name;
      return file.webkitRelativePath && path === 'conference.yaml' ? [] : [{ file, path }];
    });
    // Презентации идут первыми, чтобы заголовок нового доклада взять с титульного слайда.
    entries.sort((a, b) =>
      Number(/\.(pptx|pdf)$/i.test(b.path)) - Number(/\.(pptx|pdf)$/i.test(a.path))
      || a.path.localeCompare(b.path, 'ru'),
    );
    setBusy('Загрузка…');
    try {
      for (const [index, { file, path }] of entries.entries()) {
        setBusy(`Загрузка ${index + 1} из ${entries.length}…`);
        try {
          const query = new URLSearchParams({ path });
          const result = await request(`${base}/materials?${query}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/octet-stream' },
            body: file,
          });
          if (result.status === 200) duplicates++;
          else uploaded++;
        } catch (reason) {
          failures.push(`${path} — ${(reason as Error).message}`);
        }
      }
      await reload();
    } catch (reason) {
      failures.push((reason as Error).message);
    } finally {
      setBusy('');
      setErrors(failures);
      setStatus(`Загружено ${uploaded}, уже было в конференции ${duplicates}.`);
    }
  }

  async function save() {
    if (!review) return;
    setBusy('Сохранение…');
    setErrors([]);
    setStatus('');
    try {
      const bindings = review.files.map((file) => ({
        source_file_id: file.source.id,
        talk_id: (choices[file.source.id] ?? selectionFor(file)) || null,
      }));
      const response = await request(`${base}/materials/bindings`, {
        method: 'PUT',
        headers: json,
        body: JSON.stringify({ bindings }),
      });
      setReview(await readReview(response));
      setChoices({});
      setStatus('Привязки сохранены.');
    } catch (reason) {
      setErrors([(reason as Error).message]);
    } finally {
      setBusy('');
    }
  }

  function selectFiles(input: HTMLInputElement) {
    const files = Array.from(input.files ?? []);
    input.value = '';
    if (files.length) void upload(files);
  }

  async function startProcessing() {
    setErrors([]);
    setStatus('');
    setBusy('Запуск обработки…');
    try {
      await request(`${base}/processing`, { method: 'POST' });
      setReview((previous) => previous && { ...previous, processing_started: true });
      setStatus('Обработка запрошена.');
      onStarted?.();
    } catch (reason) {
      setErrors([(reason as Error).message]);
    } finally {
      setBusy('');
    }
  }

  async function addTalk() {
    if (!review || !talkTitle.trim()) return;
    setErrors([]);
    setStatus('');
    setBusy('Добавление доклада…');
    try {
      const section = review.sections.find(({ id }) => id === sectionId) ?? review.sections[0];
      const response = await request(`${base}/talks`, {
        method: 'POST',
        headers: json,
        body: JSON.stringify({ title: talkTitle.trim(), section_id: section?.id ?? null }),
      });
      setTalkTitle('');
      setReview(await readReview(response));
      setStatus('Доклад добавлен.');
    } catch (reason) {
      setErrors([(reason as Error).message]);
    } finally {
      setBusy('');
    }
  }

  const files = review?.files ?? [];
  const talks = review?.talks ?? [];
  const sections = review?.sections ?? [];
  const materials = files.filter((file) => file.source.kind !== 'program');
  const unconfirmed = materials.some((file) => !file.binding.confirmed);
  const bound = materials.filter((file) => file.binding.confirmed && file.binding.talk_id).length;
  let warning = '';
  if (materials.length && !talks.length) {
    warning = 'Реестр докладов пуст. Загрузите папку, где у каждого доклада своя подпапка '
      + '(например, Иванов/ или 01_Иванов/), или добавьте доклады вручную. Без докладов '
      + 'обработка разберёт файлы, но сводок не будет.';
  } else if (materials.length && unconfirmed) {
    warning = 'Проверьте и подтвердите привязки: пока они не подтверждены, шаги сводки ждут.';
  } else if (materials.length && !bound) {
    warning = 'Ни один файл не привязан к докладу: сводок не будет.';
  }
  return (
    <section className="card" aria-labelledby="materials-title" aria-busy={!!busy}>
      <h2 id="materials-title">Материалы и привязки</h2>
      <p className="muted">Для создания реестра из папки используйте подпапки вида 01_Иванов.</p>
      <div className="fields">
        <label className="field"><span>Папка конференции</span>
          <input
            type="file"
            ref={(input) => { if (input) input.setAttribute('webkitdirectory', ''); }}
            disabled={!!busy}
            onChange={(event) => selectFiles(event.currentTarget)}
          />
        </label>
        <label className="field"><span>Отдельные файлы</span>
          <input type="file" multiple disabled={!!busy} onChange={(event) => selectFiles(event.currentTarget)} />
        </label>
      </div>
      <p role="status">{busy || status}</p>
      {!!errors.length && <ul role="alert">{errors.map((message, index) => <li key={index}>{message}</li>)}</ul>}
      {review && (
        <form className="row" onSubmit={(event) => { event.preventDefault(); void addTalk(); }}>
          <label className="field">
            <span>Название доклада</span>
            <input value={talkTitle} onChange={(event) => setTalkTitle(event.target.value)} disabled={!!busy} required />
          </label>
          {sections.length > 1 && (
            <label className="field">
              <span>Секция</span>
              <select value={sectionId || sections[0].id} disabled={!!busy} onChange={(event) => setSectionId(event.target.value)}>
                {sections.map(({ id, title }) => <option key={id} value={id}>{title}</option>)}
              </select>{' '}
            </label>
          )}
          <button disabled={!!busy || !talkTitle.trim()} style={{ alignSelf: 'end' }}>Добавить доклад</button>
        </form>
      )}
      {files.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th scope="col">Файл</th><th scope="col">Подсказка</th><th scope="col">Доклад</th></tr></thead>
            <tbody>
              {files.map((file) => {
                const alternate = files.find(({ source }) => source.id === file.alternate_of);
                const suggested = talks.find(({ id }) => id === file.suggested_talk_id);
                return (
                  <tr key={file.source.id}>
                    <th scope="row">
                      {file.relative_path}<br />
                      <small>
                        {kinds[file.source.kind]}
                        {file.source.encoding && ` · ${file.source.encoding}`}
                        {file.source.duration_ms !== null && ` · ${Math.ceil(file.source.duration_ms / 1000)} с`}
                      </small>
                      {file.warning && <p>{file.warning}</p>}
                    </th>
                    <td>
                      {alternate ? `Копия презентации ${alternate.relative_path}` : suggested
                        ? `${suggested.title ?? 'Доклад без названия'} · ${Math.round((file.confidence ?? 0) * 100)} %`
                        : 'Требуется выбор'}
                      {file.title_candidates?.title && <p>{file.title_candidates.title}</p>}
                      {!!file.title_candidates?.speakers.length && <p>{file.title_candidates.speakers.join(', ')}</p>}
                    </td>
                    <td>
                      <select
                        aria-label={`Доклад для ${file.relative_path}`}
                        value={choices[file.source.id] ?? selectionFor(file)}
                        disabled={!!busy || file.source.kind === 'program'}
                        onChange={(event) => {
                          const value = event.target.value;
                          setChoices((previous) => ({ ...previous, [file.source.id]: value }));
                        }}
                      >
                        <option value="">{file.source.kind === 'program' ? 'Программа конференции' : 'Без привязки'}</option>
                        {talks.map((talk) => (
                          <option key={talk.id} value={talk.id}>
                            {talk.section} · {talk.order}. {talk.title ?? 'Без названия'}
                          </option>
                        ))}
                      </select>
                      {file.binding.confirmed && !(file.source.id in choices) && <span className="pill ok">Подтверждено</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {(files.length > 0 || talks.length > 0) && (
        <p><button className="primary" disabled={!!busy} onClick={() => void save()}>Подтвердить привязки</button></p>
      )}
      {!!talks.length && (
        <>
          <h3>Реестр докладов</h3>
          <ul>{talks.map((talk) => (
            <li key={talk.id}>
              {talk.section} · {talk.order}. {talk.title ?? 'Без названия'}{' '}
              <span className={`pill ${statusTone[talk.status] ?? ''}`}>{statuses[talk.status]}</span>
            </li>
          ))}</ul>
        </>
      )}
      {review && <p><a href={`${base}/conference.yaml`}>Скачать conference.yaml</a></p>}
      {warning && <p className="notice">{warning}</p>}
      {review?.processing_started
        ? (
          <p className="row">
            <span className="pill ok">Обработка запущена</span>
            {onStarted && <button onClick={onStarted}>Открыть ход обработки</button>}
          </p>
        )
        : files.length > 0 && (
          <p><button className="primary" disabled={!!busy} onClick={() => void startProcessing()}>Запустить обработку</button></p>
        )}
      <button disabled={!!busy} onClick={() => {
        setErrors([]);
        setBusy('Обновление…');
        void reload().catch((error: Error) => setErrors([error.message])).finally(() => setBusy(''));
      }}>Обновить список</button>
    </section>
  );
}

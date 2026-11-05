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

async function readReview(response: Response) {
  const etag = response.headers.get('ETag');
  if (!etag) throw new Error('Сервер не вернул версию привязок');
  const review: Review = await response.json();
  return { review, etag };
}

function selectionFor(file: Material) {
  if (file.binding.confirmed) return file.binding.talk_id ?? '';
  if (file.alternate_of) return '';
  return file.suggested_talk_id ?? '';
}

export function Materials({ conferenceId }: { conferenceId: string }) {
  const base = `/api/v1/conferences/${conferenceId}`;
  const [snapshot, setSnapshot] = useState<{ review: Review; etag: string }>();
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
      .then(setSnapshot)
      .catch((error: Error) => {
        if (!controller.signal.aborted) setErrors([error.message]);
      });
    return () => controller.abort();
  }, [base]);

  async function reload() {
    setSnapshot(await readReview(await request(`${base}/materials`)));
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
    if (!snapshot) return;
    setBusy('Сохранение…');
    setErrors([]);
    setStatus('');
    try {
      const bindings = snapshot.review.files.map((file) => ({
        source_file_id: file.source.id,
        talk_id: (choices[file.source.id] ?? selectionFor(file)) || null,
      }));
      const response = await request(`${base}/materials/bindings`, {
        method: 'PUT',
        headers: { ...json, 'If-Match': snapshot.etag },
        body: JSON.stringify({ bindings }),
      });
      setSnapshot(await readReview(response));
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
      setSnapshot((previous) => previous && ({
        ...previous, review: { ...previous.review, processing_started: true },
      }));
      setStatus('Обработка запрошена.');
    } catch (reason) {
      setErrors([(reason as Error).message]);
    } finally {
      setBusy('');
    }
  }

  async function addTalk() {
    if (!snapshot || !talkTitle.trim()) return;
    setErrors([]);
    setStatus('');
    setBusy('Добавление доклада…');
    try {
      let section = snapshot.review.sections.find(({ id }) => id === sectionId)
        ?? snapshot.review.sections[0];
      if (!section) {
        const response = await request(`${base}/sections`, {
          method: 'POST',
          headers: json,
          body: JSON.stringify({ title: 'Основная секция', order: 1 }),
        });
        section = await response.json() as Section;
      }
      const order = snapshot.review.talks
        .filter((talk) => talk.section_id === section.id)
        .reduce((maximum, talk) => Math.max(maximum, talk.order), 0) + 1;
      await request(`/api/v1/sections/${section.id}/talks`, {
        method: 'POST',
        headers: json,
        body: JSON.stringify({ title: talkTitle.trim(), order }),
      });
      setTalkTitle('');
      setSnapshot(await readReview(await request(`${base}/materials`)));
      setStatus('Доклад добавлен.');
    } catch (reason) {
      setErrors([(reason as Error).message]);
    } finally {
      setBusy('');
    }
  }

  const files = snapshot?.review.files ?? [];
  const talks = snapshot?.review.talks ?? [];
  const sections = snapshot?.review.sections ?? [];
  return (
    <section aria-labelledby="materials-title" aria-busy={!!busy}>
      <h2 id="materials-title">Материалы и привязки</h2>
      <p>Для создания реестра из папки используйте подпапки вида 01_Иванов.</p>
      <p>
        <label>Папка конференции{' '}
          <input
            type="file"
            ref={(input) => { if (input) input.setAttribute('webkitdirectory', ''); }}
            disabled={!!busy}
            onChange={(event) => selectFiles(event.currentTarget)}
          />
        </label>
      </p>
      <p>
        <label>Отдельные файлы{' '}
          <input type="file" multiple disabled={!!busy} onChange={(event) => selectFiles(event.currentTarget)} />
        </label>
      </p>
      <p role="status">{busy || status}</p>
      {!!errors.length && <ul role="alert">{errors.map((message, index) => <li key={index}>{message}</li>)}</ul>}
      {snapshot && (
        <form onSubmit={(event) => { event.preventDefault(); void addTalk(); }}>
          <label>
            Название доклада{' '}
            <input value={talkTitle} onChange={(event) => setTalkTitle(event.target.value)} disabled={!!busy} required />
          </label>{' '}
          {sections.length > 1 && (
            <label>
              Секция{' '}
              <select value={sectionId || sections[0].id} disabled={!!busy} onChange={(event) => setSectionId(event.target.value)}>
                {sections.map(({ id, title }) => <option key={id} value={id}>{title}</option>)}
              </select>{' '}
            </label>
          )}
          <button disabled={!!busy || !talkTitle.trim()}>Добавить доклад</button>
        </form>
      )}
      {files.length > 0 && (
        <>
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
                      {file.binding.confirmed && !(file.source.id in choices) && <small> Подтверждено</small>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
      {(files.length > 0 || talks.length > 0) && (
        <p><button disabled={!!busy} onClick={() => void save()}>Подтвердить привязки</button></p>
      )}
      {!!talks.length && (
        <>
          <h3>Реестр докладов</h3>
          <ul>{talks.map((talk) => (
            <li key={talk.id}>{talk.section} · {talk.order}. {talk.title ?? 'Без названия'} — {statuses[talk.status]}</li>
          ))}</ul>
        </>
      )}
      {snapshot && <p><a href={`${base}/conference.yaml`}>Скачать conference.yaml</a></p>}
      {snapshot?.review.processing_started
        ? <p><a href={`/api/v1/jobs?conference_id=${conferenceId}`}>Задачи обработки (JSON)</a></p>
        : files.length > 0 && (
          <p><button disabled={!!busy} onClick={() => void startProcessing()}>Запустить обработку</button></p>
        )}
      <button disabled={!!busy} onClick={() => {
        setErrors([]);
        setBusy('Обновление…');
        void reload().catch((error: Error) => setErrors([error.message])).finally(() => setBusy(''));
      }}>Обновить список</button>
    </section>
  );
}

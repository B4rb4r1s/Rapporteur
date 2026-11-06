"""Выполнение Stage вне транзакции; запись результатов после проверки владения."""

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import psycopg
from pydantic import ValidationError

from rapporteur_contracts import StageOutput
from rapporteur_contracts.enums import ResourceKind
from rapporteur_pipeline.stage import StageContext, StageError, config_hash
from rapporteur_pipeline.storage import DirectoryStorage, Storage
from rapporteur_workers.cache import cached_output
from rapporteur_workers.queue import Mode, Queue, QueueJob
from rapporteur_workers.stages import RESOURCES, get_stage, llm_client


def log(event: str, **fields: str | int | bool) -> None:
    # Нет текстов материалов, имени исходного файла, DSN или исключений драйвера.
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def write_health(path: Path, resource: ResourceKind, mode: Mode) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(
            json.dumps({"at": time.time(), "resource": resource.value, "mode": mode}).encode()
        )
    os.replace(tmp.name, path)


def healthy(path: Path, resource: ResourceKind, mode: Mode, max_age: float = 30) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - float(data["at"])
        return 0 <= age < max_age and data["resource"] == resource.value and data["mode"] == mode
    except (OSError, ValueError, KeyError, TypeError):
        return False


class BufferedStorage:
    """Пишет во временный каталог попытки; общие файлы не трогаются до fence в БД."""

    def __init__(self, target: Storage, scratch: Path) -> None:
        self.target = target
        self.pending = DirectoryStorage(scratch)
        self.keys: set[str] = set()

    def put(self, key: str, data: bytes) -> None:
        self.pending.put(key, data)
        self.keys.add(key)

    def get(self, key: str) -> bytes:
        return self.pending.get(key) if key in self.keys else self.target.get(key)

    def exists(self, key: str) -> bool:
        return key in self.keys or self.target.exists(key)

    def delete(self, key: str) -> None:
        # Stage не должен удалять существующие результаты других попыток.
        if key not in self.keys:
            raise ValueError("Нельзя удалить файл вне текущей попытки")
        self.pending.delete(key)
        self.keys.remove(key)

    def publish(self) -> None:
        # Сначала производные файлы, затем JSON артефакта со ссылками на них.
        for key in sorted(self.keys, key=lambda item: (item.startswith("artifacts/"), item)):
            self.target.put(key, self.pending.get(key))


def execute(queue: Queue, job: QueueJob, storage: Storage, health_file: Path) -> bool:
    done = threading.Event()
    lost = threading.Event()

    def keep_lease() -> None:
        while not done.wait(queue.lease_seconds / 3):
            try:
                if not queue.heartbeat(job):
                    lost.set()
                    return
                write_health(health_file, job.resource, job.mode)
            except (psycopg.Error, OSError):
                lost.set()
                return

    thread = threading.Thread(target=keep_lease, daemon=True)
    thread.start()
    try:
        stage = get_stage(job.stage, job.mode, job.config)
        if RESOURCES[job.stage] != job.resource:
            raise ValueError("Ресурс задачи не соответствует этапу")
        config = stage.config_model.model_validate(job.config)
        if config_hash(config) != job.inputs.config_hash:
            raise ValueError("Хэш конфигурации не соответствует входу")
        cached = cached_output(stage, job.inputs, job.mode, storage)
        if cached is not None:
            return queue.finish(job, cached, lambda: cached)
        with tempfile.TemporaryDirectory(prefix="rapporteur-attempt-") as tmp:
            buffered = BufferedStorage(storage, Path(tmp) / "pending")
            output = stage.run(
                StageContext(
                    storage=buffered, config=config, tmp_dir=Path(tmp), llm=llm_client(config)
                ),
                job.inputs,
            )
            if job.mode == "gpu" and output.meta.stub:
                raise ValueError("Заглушка запрещена в режиме gpu")
            if lost.is_set():
                queue.fail(job, "lease_lost", transient=True)
                log("discarded", job_id=str(job.id))
                return False

            def publish_once() -> StageOutput:
                existing = cached_output(stage, job.inputs, job.mode, storage)
                if existing is not None:
                    return existing
                buffered.publish()
                return output

            published = queue.finish(job, output, publish_once)
            log(
                "completed" if published else "discarded", job_id=str(job.id), stub=output.meta.stub
            )
            return published
    except StageError as error:
        queue.fail(job, error.code.value)
        log("failed", job_id=str(job.id), code=error.code.value)
    except (ValidationError, ValueError, KeyError):
        queue.fail(job, "invalid_input")
        log("failed", job_id=str(job.id), code="invalid_input")
    except (psycopg.Error, OSError):
        # Если БД недоступна, lease истечёт, и reaper вернёт задачу в очередь.
        try:
            queue.fail(job, "temporary_failure", transient=True)
        except psycopg.Error:
            pass
        log("failed", job_id=str(job.id), code="temporary_failure")
    except Exception:
        queue.fail(job, "handler_failure")
        log("failed", job_id=str(job.id), code="handler_failure")
    finally:
        done.set()
        thread.join(timeout=6)
    return False


def run(
    queue: Queue,
    storage: Storage,
    resource: ResourceKind,
    mode: Mode,
    health_file: Path,
    stop: threading.Event,
    *,
    poll_seconds: float = 1,
) -> None:
    queue.check()
    log("worker_started", resource=resource.value, mode=mode, stub=mode == "dev")
    previous_stage = ""
    while not stop.is_set():
        try:
            job = queue.claim(resource, mode, previous_stage)
            write_health(health_file, resource, mode)
            if job is not None:
                execute(queue, job, storage, health_file)
                previous_stage = job.stage
                continue
        except (psycopg.Error, OSError):
            log("poll_failed", code="database_or_storage_unavailable")
        stop.wait(poll_seconds)

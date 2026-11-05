# Реестр зависимостей: дополнение P1-04

Версии закреплены в backend/uv.lock; новые версии моложе двух недель не выбирались
(exclude-newer 2026-10-20). Лицензии проверены по метаданным установленных пакетов
и файлам исходных релизов. Это запись о добавленных зависимостях backend, не полный
реестр будущего офлайн-комплекта или весов P0-07.

| Пакет | Версия | Лицензия | Источник |
| --- | --- | --- | --- |
| SQLAlchemy | 2.0.55 | MIT | [релиз](https://github.com/sqlalchemy/sqlalchemy/blob/rel_2_0_55/LICENSE) |
| Alembic | 1.20.0 | MIT | [релиз](https://github.com/sqlalchemy/alembic/blob/rel_1_20_0/LICENSE) |
| psycopg | 3.3.6 | LGPL-3.0-only | [релиз](https://github.com/psycopg/psycopg/blob/3.3.6/LICENSE.txt) |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | метаданные пакета; лицензии поставляемых библиотек остаются в wheel |

Backend использует SQLAlchemy Core и явные транзакции; версию проверяет под блокировкой
строки, не через неявный ORM flush. В Alembic 1.20.0 проверена сигнатура upgrade и
передача Connection через Config.attributes; миграция и откат проверены на PostgreSQL 17.11.

## P1-05. Приём материалов

Backend использует пакет `rapporteur-pipeline` из этого репозитория, а также AnyIO и PyYAML.
Версии Python-пакетов закреплены в `backend/uv.lock`.

| Пакет | Лицензия | Источник |
| --- | --- | --- |
| AnyIO | MIT | [лицензия проекта](https://github.com/agronholm/anyio/blob/master/LICENSE) |
| PyYAML | MIT | [лицензия проекта](https://github.com/yaml/pyyaml/blob/main/LICENSE) |
| Debian FFmpeg (`ffprobe`) | LGPL и GPL для разных компонентов | [пакет Debian и файл Copyright](https://packages.debian.org/stable/ffmpeg); `/usr/share/doc/ffmpeg/copyright` в образе |

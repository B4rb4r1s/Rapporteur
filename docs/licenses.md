# Реестр зависимостей

Версии Python-пакетов закреплены в `uv.lock`, npm-пакетов — в `frontend/package-lock.json`.
Новые версии моложе двух недель не выбираются (`exclude-newer` в `pyproject.toml`,
`NPM_BEFORE` в `Makefile`). Лицензии проверены по метаданным установленных пакетов
и файлам исходных релизов. Это запись о добавленных зависимостях, не полный реестр
будущего офлайн-комплекта или весов P0-07.

С ADR-014 база — SQLite из стандартной библиотеки Python: SQLAlchemy, Alembic и psycopg
(LGPL-3.0) из зависимостей убраны.

| Пакет | Лицензия | Источник |
| --- | --- | --- |
| AnyIO | MIT | [лицензия проекта](https://github.com/agronholm/anyio/blob/master/LICENSE) |
| PyYAML | MIT | [лицензия проекта](https://github.com/yaml/pyyaml/blob/main/LICENSE) |
| Debian FFmpeg (`ffprobe`) | LGPL и GPL для разных компонентов | [пакет Debian и файл Copyright](https://packages.debian.org/stable/ffmpeg); `/usr/share/doc/ffmpeg/copyright` в образе |

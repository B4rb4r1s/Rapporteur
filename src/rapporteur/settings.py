"""Пути к конфигурации и настройки из окружения (spec §8.2)."""

import os
from pathlib import Path

# Корень репозитория или образа: рядом лежат prompts/, templates/ и profiles.yaml.
ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = ROOT / "prompts"
TEMPLATES_DIR = ROOT / "templates"
PROFILES_FILE = ROOT / "profiles.yaml"

# Том с базой SQLite, оригиналами, кэшем шагов и файлами справок.
DATA_DIR = Path(os.environ.get("RAPPORTEUR_DATA_DIR", "/data"))
# Собранный фронтенд; без него приложение отдаёт только API.
STATIC_DIR = os.environ.get("RAPPORTEUR_STATIC_DIR")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")

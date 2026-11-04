"""Профили моделей из pipeline/profiles.yaml (spec §9, ADR-010)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from rapporteur_contracts import ModelProfile

PROFILES_FILE = Path(__file__).resolve().parents[3] / "profiles.yaml"


class ProfileError(Exception):
    pass


class ProfilesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str
    profiles: list[ModelProfile]

    @model_validator(mode="after")
    def _default_known(self) -> "ProfilesFile":
        names = [profile.name for profile in self.profiles]
        if len(names) != len(set(names)):
            raise ValueError("имена профилей повторяются")
        if self.default not in names:
            raise ValueError(f"профиль по умолчанию {self.default} не описан")
        return self


def load_profile(name: str | None = None, path: Path = PROFILES_FILE) -> ModelProfile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        profiles = ProfilesFile.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ProfileError(f"Профили моделей не читаются из {path}: {error}") from error
    wanted = name or profiles.default
    for profile in profiles.profiles:
        if profile.name == wanted:
            return profile
    known = ", ".join(profile.name for profile in profiles.profiles)
    raise ProfileError(f"Нет профиля {wanted}; есть: {known}")

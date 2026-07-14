from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

T = TypeVar("T", bound=BaseModel)


def load_model(path: Path, model: type[T]) -> T:
    return model.model_validate_json(path.read_text())


def load_model_list(path: Path, model: type[T]) -> list[T]:
    return TypeAdapter(list[model]).validate_python(json.loads(path.read_text()))


def write_json(path: Path, value: BaseModel | dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, BaseModel):
        data = value.model_dump(mode="json")
    else:
        data = value
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

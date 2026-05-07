"""Validate that every `json_schema_extra.examples` in Pydantic models still
round-trips through the model, catching drift between Swagger docs and code.
"""
import inspect

import pytest
from pydantic import BaseModel, ValidationError

from app import schemas, schemas_articles, schemas_rag


def _iter_models():
    for module in (schemas, schemas_articles, schemas_rag):
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is BaseModel:
                continue
            if issubclass(obj, BaseModel) and obj.__module__ == module.__name__:
                yield f"{module.__name__}.{name}", obj


def _extract_examples(model):
    extra = getattr(model, "model_config", {}).get("json_schema_extra") or {}
    return extra.get("examples") or []


@pytest.mark.parametrize("label,model", list(_iter_models()))
def test_all_schema_examples_roundtrip(label, model):
    examples = _extract_examples(model)
    if not examples:
        pytest.skip(f"{label} has no json_schema_extra examples")
    for idx, example in enumerate(examples):
        try:
            model.model_validate(example)
        except ValidationError as e:
            pytest.fail(f"{label} example[{idx}] failed validation: {e}")

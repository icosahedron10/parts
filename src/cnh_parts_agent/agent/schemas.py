"""Response-schema projection at the model boundary."""

from copy import copy
from functools import reduce
from operator import or_
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, create_model

from ..catalog.models import DATA_MODELS, AssemblyRoute, OpResult, StrictModel


class ToolAssemblyRoute(StrictModel):
    """Compact navigation projection; full route provenance stays in host observations."""

    assembly_ref: str
    title: str
    stale: bool = False


_tool_models = {}


def _tool_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is list:
        return list.__class_getitem__(_tool_annotation(get_args(annotation)[0]))
    if origin in (Union, UnionType):
        return reduce(or_, (_tool_annotation(item) for item in get_args(annotation)))
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _tool_response_model(annotation)
    return annotation


def _tool_response_model(model: type[BaseModel]) -> type[BaseModel]:
    if model is AssemblyRoute:
        return ToolAssemblyRoute
    if model not in _tool_models:
        fields: dict[str, Any] = {}
        for name, info in model.model_fields.items():
            if name.endswith("_ref") or name == "next_cursor":
                annotation = str | None if type(None) in get_args(info.annotation) else str
            else:
                annotation = _tool_annotation(info.annotation)
            fields[name] = (annotation, copy(info))
        _tool_models[model] = create_model(model.__name__ + "Tool", __base__=model, **fields)
    return _tool_models[model]


TOOL_RESPONSE_MODELS = {name: _tool_response_model(OpResult[model]) for name, model in DATA_MODELS.items()}

"""Bounded catalog evidence for orchestrators."""

from .catalog.client import open_catalog


def create_parts_model(model=None):
    from .agent.model import create_parts_model as create

    return create(model)


def make_lookup_parts_tool(*, catalog, model):
    from .agent.lookup_tool import make_lookup_parts_tool as create

    return create(catalog=catalog, model=model)


__all__ = ["open_catalog", "create_parts_model", "make_lookup_parts_tool"]

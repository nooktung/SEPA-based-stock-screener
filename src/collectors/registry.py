"""Provider instantiation and failure handling.

The runner asks the registry for providers of a kind, in priority order, and
tries each until one succeeds. Failures never fabricate: they mark the dataset
stale and let confidence fall.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from src.collectors.base import BaseProvider, ProviderError
from src.config import SourceSpec, load_config

log = logging.getLogger(__name__)

T = TypeVar("T")


class ProviderLoadError(RuntimeError):
    pass


def instantiate(spec: SourceSpec) -> BaseProvider:
    module_path, _, class_name = spec.provider_class.rpartition(".")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ProviderLoadError(f"cannot import {module_path} for source '{spec.id}': {exc}") from exc
    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise ProviderLoadError(f"{module_path} has no class {class_name}") from exc
    return cls(source_id=spec.id, source_level=spec.source_level, config=spec.config)


@dataclass
class FetchResult:
    value: Any
    source_id: str | None
    stale: bool
    errors: dict[str, str]

    @property
    def ok(self) -> bool:
        return self.source_id is not None and not self.stale

    @property
    def from_fixture_only(self) -> bool:
        return bool(self.source_id and self.source_id.startswith("fixture"))


def fetch_with_fallback(kind: str, call: Callable[[BaseProvider], T], capability: str | None = None) -> FetchResult:
    """Try each enabled provider of `kind` in priority order.

    Returns the first success. If every provider fails, returns a stale result
    with an empty value and the collected errors -- never a guessed value.
    """
    cfg = load_config()
    errors: dict[str, str] = {}

    for spec in cfg.sources.for_kind(kind):
        if not spec.has_credentials:
            errors[spec.id] = f"missing credential in ${spec.auth_env}"
            continue
        try:
            provider = instantiate(spec)
        except ProviderLoadError as exc:
            errors[spec.id] = str(exc)
            continue
        if capability and not provider.supports(capability):
            errors[spec.id] = f"does not support capability '{capability}'"
            continue
        try:
            return FetchResult(value=call(provider), source_id=spec.id, stale=False, errors=errors)
        except ProviderError as exc:
            errors[spec.id] = f"{type(exc).__name__}: {exc}"
            log.warning("source %s failed: %s", spec.id, exc)

    return FetchResult(value=None, source_id=None, stale=True, errors=errors)

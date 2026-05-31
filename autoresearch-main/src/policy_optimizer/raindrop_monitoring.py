from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any, Callable, TypeVar


F = TypeVar("F", bound=Callable[..., Any])

_raindrop: Any | None = None
_initialized = False


def interaction(name: str) -> Callable[[F], F]:
    def decorate(func: F) -> F:
        if not _otel_enabled():
            return func
        raindrop = _init_raindrop()
        if raindrop is None or not hasattr(raindrop, "interaction"):
            return func
        return raindrop.interaction(name)(func)

    return decorate


def tool(name: str) -> Callable[[F], F]:
    def decorate(func: F) -> F:
        if not _otel_enabled():
            return func
        raindrop = _init_raindrop()
        if raindrop is None or not hasattr(raindrop, "tool"):
            return func
        return raindrop.tool(name)(func)

    return decorate


def traced_event(event: str, *, input_value: str | None = None) -> Callable[[F], F]:
    def decorate(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            raindrop = _init_raindrop()
            interaction_obj = None
            if raindrop is not None and hasattr(raindrop, "begin"):
                interaction_obj = raindrop.begin(
                    user_id=os.getenv("USER", "local"),
                    event=event,
                    input=input_value or event,
                )
            try:
                result = func(*args, **kwargs)
            except Exception as error:
                if interaction_obj is not None and hasattr(interaction_obj, "finish"):
                    interaction_obj.finish(output=f"error: {error}")
                raise
            if interaction_obj is not None and hasattr(interaction_obj, "finish"):
                interaction_obj.finish(output="ok")
            return result

        return wrapper  # type: ignore[return-value]

    return decorate


def _otel_enabled() -> bool:
    return bool(os.getenv("RAINDROP_WRITE_KEY"))


def flush_and_shutdown() -> None:
    if not _initialized:
        return
    raindrop = _init_raindrop()
    if raindrop is None:
        return
    if hasattr(raindrop, "flush"):
        raindrop.flush()
    if hasattr(raindrop, "shutdown"):
        raindrop.shutdown()


def begin_interaction(
    *,
    event: str,
    input_value: str,
    properties: dict[str, Any] | None = None,
    convo_id: str | None = None,
) -> Any | None:
    raindrop = _init_raindrop()
    if raindrop is None or not hasattr(raindrop, "begin"):
        return None
    return raindrop.begin(
        user_id=os.getenv("USER", "local"),
        event=event,
        input=_non_empty(input_value, fallback=event),
        properties=properties,
        convo_id=convo_id,
    )


def finish_interaction(interaction_obj: Any | None, *, output: str) -> None:
    if interaction_obj is None or not hasattr(interaction_obj, "finish"):
        return
    interaction_obj.finish(output=_non_empty(output, fallback="completed"))


def track_llm_call(*, model: str, prompt: str, output: str, event: str = "policy_optimizer_llm") -> None:
    raindrop = _init_raindrop()
    if raindrop is None or not hasattr(raindrop, "track_ai"):
        return
    raindrop.track_ai(
        user_id=os.getenv("USER", "local"),
        event=event,
        model=model,
        input=_non_empty(prompt, fallback=event),
        output=_non_empty(output, fallback="completed"),
    )


def _non_empty(value: object, *, fallback: str) -> str:
    text = "" if value is None else str(value)
    if text.strip():
        return text
    return fallback


def _init_raindrop() -> Any | None:
    global _initialized, _raindrop
    if _initialized:
        return _raindrop
    _initialized = True

    load_env_file()
    local_debugger = os.getenv("RAINDROP_LOCAL_DEBUGGER")
    if not local_debugger:
        return None

    try:
        import raindrop.analytics as raindrop  # type: ignore[import-not-found]
    except Exception:
        return None

    write_key = os.getenv("RAINDROP_WRITE_KEY") or None
    raindrop.init(
        api_key=write_key,
        tracing_enabled=bool(write_key),
        local_workshop_url=local_debugger,
    )

    if os.getenv("RAINDROP_DEBUG_LOGS") and hasattr(raindrop, "set_debug_logs"):
        raindrop.set_debug_logs(True)

    _raindrop = raindrop
    return _raindrop


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key, value)

"""ANPR engine plug-ins (SPEC §6, DECISIONS D-076).

An engine turns one camera frame (a numpy BGR image from OpenCV) into plate reads. No Iranian-plate
model with a licence that allows closed-source commercial use was found, so the app ships:

* ``none`` – no engine (manual entry; smart cameras that read plates themselves still work),
* ``simulator`` – scripted reads for tests and demos,
* ``plugin:<file.py>:<Class>`` – a Python file in ``<data root>\\anpr`` that wraps a commercial SDK or a
  licensed ONNX model (ONNX Runtime can be used inside the plug-in). See docs/ANPR_PLUGINS.md.
"""

from __future__ import annotations

import importlib.util
import logging
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from caspian_parking.core.anpr import FrameRead

log = logging.getLogger(__name__)


class AnprError(RuntimeError):
    pass


@runtime_checkable
class AnprEngine(Protocol):
    name: str

    def read(self, frame: Any) -> list[FrameRead]:
        """Plate reads found in one frame (empty list = no vehicle / no plate)."""
        ...


class NullEngine:
    name = "none"

    def read(self, frame: Any) -> list[FrameRead]:
        return []


class SimulatorEngine:
    """Returns queued reads one frame at a time (an empty queue = nothing in view)."""

    name = "simulator"

    def __init__(self, script: Iterable[list[FrameRead]] = ()) -> None:
        self.script: deque[list[FrameRead]] = deque(script)

    def push(self, *frames: list[FrameRead]) -> None:
        self.script.extend(frames)

    def read(self, frame: Any) -> list[FrameRead]:
        return self.script.popleft() if self.script else []


def load_engine(spec: str, plugins_folder: Path) -> AnprEngine:
    spec = (spec or "none").strip()
    if spec == "none":
        return NullEngine()
    if spec == "simulator":
        return SimulatorEngine()
    if spec.startswith("plugin:"):
        try:
            _prefix, file_name, class_name = spec.split(":", 2)
        except ValueError as exc:
            raise AnprError("camera.engine_bad_spec") from exc
        path = (plugins_folder / file_name).resolve()
        if not path.is_file():
            raise AnprError("camera.engine_missing")
        module_spec = importlib.util.spec_from_file_location(f"caspian_anpr_{path.stem}", path)
        if module_spec is None or module_spec.loader is None:
            raise AnprError("camera.engine_missing")
        module = importlib.util.module_from_spec(module_spec)
        try:
            module_spec.loader.exec_module(module)
            engine = getattr(module, class_name)()
        except Exception as exc:  # a broken third-party plug-in must not stop the app
            log.exception("ANPR plug-in %s failed to load", path.name)
            raise AnprError("camera.engine_failed") from exc
        if not isinstance(engine, AnprEngine):
            raise AnprError("camera.engine_bad_spec")
        return engine
    raise AnprError("camera.engine_bad_spec")

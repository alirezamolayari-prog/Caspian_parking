"""Plate sources: simulator, RTSP reconnect + pass grouping (fake stream), smart camera HTTP push, engines."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

import numpy as np
import pytest

from caspian_parking.config.machine import CameraConfig, MachineConfig
from caspian_parking.core.anpr import FrameRead
from caspian_parking.core.clock import FixedClock
from caspian_parking.core.plate import parse_plate
from caspian_parking.devices.anpr import AnprError, NullEngine, SimulatorEngine, load_engine
from caspian_parking.devices.plate_source import (
    ManualPlateSource,
    PassCollector,
    RtspPlateSource,
    SimulatorPlateSource,
    SmartCameraSource,
    create_plate_source,
    ndarray_to_qimage,
    safe_url,
)

CLOCK = FixedClock(datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
PLATE = "12ب345-22"


def config(kind: str = "simulator", **kw) -> CameraConfig:
    return CameraConfig(lane=kw.pop("lane", "entry"), name="cam", kind=kind, **kw)


def test_simulator_pass_with_misreads_still_votes_right(qapp):
    source = SimulatorPlateSource(config(), CLOCK, misread_rate=0.3, frames=9, seed=4)
    seen = []
    source.passed.connect(seen.append)
    item = source.simulate(PLATE)
    assert seen == [item]
    assert item.result.plate == parse_plate(PLATE)
    assert item.snapshot[:2] == b"\xff\xd8"  # JPEG
    unreadable = source.simulate(None)
    assert unreadable.result.unidentified
    states = []
    source.online_changed.connect(states.append)
    source.start()
    source.set_online(False)
    assert states == [True, False]


def test_manual_source_never_online(qapp):
    source = ManualPlateSource(config("off"), CLOCK)
    source.start()
    assert not source.online


def test_pass_collector_groups_frames():
    collector = PassCollector(gap_frames=2, max_frames=5)
    assert collector.feed([], "f0") is None
    assert collector.feed([FrameRead(PLATE, 0.6)], "f1") is None
    assert collector.feed([FrameRead(PLATE, 0.9)], "f2") is None
    assert collector.feed([], "f3") is None
    reads, best = collector.feed([], "f4")
    assert len(reads) == 2
    assert best == "f2"
    for i in range(4):
        assert collector.feed([FrameRead(PLATE, 0.5)], i) is None
    reads, _best = collector.feed([FrameRead(PLATE, 0.5)], 4)  # max frames reached
    assert len(reads) == 5


class FakeCapture:
    def __init__(self, frames: int, opened: bool = True):
        self.frames = frames
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        if self.frames <= 0:
            return False, None
        self.frames -= 1
        return True, np.zeros((36, 64, 3), dtype=np.uint8)

    def release(self):
        self.released = True


def test_rtsp_reconnects_and_reads_passes(qapp, qtbot):
    engine = SimulatorEngine([[FrameRead(PLATE, 0.9, "van", 0.8)], [FrameRead(PLATE, 0.95)], [], [], [], []])
    captures = [FakeCapture(0, opened=False), FakeCapture(0, opened=False), FakeCapture(8), FakeCapture(30)]
    made = []

    def factory(url):
        capture = captures.pop(0) if captures else FakeCapture(0, opened=False)
        made.append(capture)
        return capture

    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        time.sleep(0.01)  # keep the reconnect loop from spinning while the test waits

    source = RtspPlateSource(
        config("rtsp", url="rtsp://admin:secret@10.0.0.5/stream"), CLOCK, engine, factory, fake_sleep
    )
    passes, previews = [], []
    source.passed.connect(passes.append)
    source.frame.connect(previews.append)
    source.start()
    qtbot.waitUntil(lambda: len(passes) == 1 and source.reconnects >= 3, timeout=30_000)
    source.stop()
    assert passes[0].result.plate == parse_plate(PLATE)
    assert passes[0].result.vehicle_type == "van"
    assert passes[0].snapshot[:2] == b"\xff\xd8"
    assert sleeps[:3] == [1.0, 2.0, 1.0]  # back-off grows while down, resets after a good connection
    assert previews
    assert all(c.released for c in made[:3])
    assert not source.online


def test_failing_engine_does_not_stop_the_stream(qapp):
    class Broken:
        name = "broken"

        def read(self, frame):
            raise RuntimeError("boom")

    source = RtspPlateSource(config("rtsp"), CLOCK, Broken(), lambda url: FakeCapture(1))
    assert source.process_frame(np.zeros((4, 4, 3), dtype=np.uint8)) is None


def test_smart_camera_http_push(qapp, qtbot):
    source = SmartCameraSource(config("smart", port=0, lane="exit"), CLOCK, host="127.0.0.1")
    passes = []
    source.passed.connect(passes.append)
    source.start()
    try:
        assert source.online
        body = {
            "plate": PLATE,
            "confidence": 0.93,
            "vehicle_type": "sedan",
            "image": base64.b64encode(b"jpg").decode(),
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{source.port}/plate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.loads(response.read())["ok"] is True
        qtbot.waitUntil(lambda: len(passes) == 1, timeout=3000)
        assert passes[0].lane == "exit"
        assert passes[0].result.accepted
        assert passes[0].snapshot == b"jpg"
        bad = urllib.request.Request(f"http://127.0.0.1:{source.port}/plate", data=b"not json")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad, timeout=5)
        multi = source.handle_push({"reads": [{"plate": PLATE, "confidence": 0.9}, {"plate": None, "confidence": 0}]})
        assert multi.result.frames == 2
    finally:
        source.stop()
    assert not source.online


def test_engines_and_plugins(tmp_path):
    assert isinstance(load_engine("none", tmp_path), NullEngine)
    assert isinstance(load_engine("simulator", tmp_path), SimulatorEngine)
    (tmp_path / "vendor.py").write_text(
        "from caspian_parking.core.anpr import FrameRead\n"
        "class Engine:\n"
        "    name = 'vendor'\n"
        "    def read(self, frame):\n"
        "        return [FrameRead('12ب345-22', 0.9)]\n",
        encoding="utf-8",
    )
    engine = load_engine("plugin:vendor.py:Engine", tmp_path)
    assert engine.read(None)[0].confidence == 0.9
    (tmp_path / "broken.py").write_text("raise ImportError('sdk missing')\n", encoding="utf-8")
    for spec, code in (
        ("plugin:missing.py:Engine", "missing"),
        ("plugin:broken.py:Engine", "failed"),
        ("plugin:vendor.py", "bad_spec"),
        ("magic", "bad_spec"),
    ):
        with pytest.raises(AnprError, match=code):
            load_engine(spec, tmp_path)


def test_factory_config_and_helpers(qapp):
    assert isinstance(create_plate_source(config("off"), CLOCK), ManualPlateSource)
    assert isinstance(create_plate_source(config("simulator"), CLOCK), SimulatorPlateSource)
    assert isinstance(create_plate_source(config("rtsp"), CLOCK), RtspPlateSource)
    assert isinstance(create_plate_source(config("smart"), CLOCK), SmartCameraSource)
    assert safe_url("rtsp://admin:secret@10.0.0.5:554/stream") == "rtsp://10.0.0.5:554/stream"
    assert safe_url("rtsp://10.0.0.5/s") == "rtsp://10.0.0.5/s"
    image = ndarray_to_qimage(np.zeros((10, 20, 3), dtype=np.uint8))
    assert (image.width(), image.height()) == (20, 10)
    machine = MachineConfig(cameras=[config("rtsp", lane="exit"), config("off")])
    restored = MachineConfig.from_json(json.loads(json.dumps(machine.to_json())))
    assert restored.camera_for("exit").kind == "rtsp"
    assert restored.camera_for("entry") is None

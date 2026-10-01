"""Multi-frame plate voting (SPEC §6): a vehicle passing at ~20 km/h gives several frames, each read by
the ANPR engine with its own confidence. Voting per character position over all frames turns several
noisy reads into one plate, and the agreement between frames becomes the final confidence.

Pure logic (no Qt, no OpenCV) so it is exhaustively unit-tested. Confidences are 0..1 floats
(they are scores, never money).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from caspian_parking.core.plate import Plate, PlateKind, parse_plate

VEHICLE_TYPES = ("sedan", "van", "truck", "motorcycle", "other")
DEFAULT_MIN_CONFIDENCE = 0.80


@dataclass(frozen=True)
class FrameRead:
    """One engine result for one frame (``text`` None = vehicle seen, plate not readable)."""

    text: str | None
    confidence: float
    vehicle_type: str | None = None
    vehicle_confidence: float = 0.0


@dataclass(frozen=True)
class VoteResult:
    plate: Plate | None
    confidence: float  # 0..1, agreement between frames × average engine confidence
    vehicle_type: str | None
    frames: int  # frames considered
    valid_frames: int  # frames with a parseable Iranian plate
    accepted: bool  # confidence reached the threshold

    @property
    def unidentified(self) -> bool:
        return self.plate is None


def _parse(text: str | None) -> Plate | None:
    if not text:
        return None
    try:
        return parse_plate(text, allow_free=False)
    except ValueError:
        return None


def _characters(plate: Plate) -> list[str]:
    """Plate → list of positions (car: 2 digits, letter, 3 digits, 2 region digits = 8 positions)."""
    return (
        list("".join(plate.parts))
        if plate.kind is PlateKind.MOTORCYCLE
        else [
            *plate.parts[0],
            plate.parts[1],
            *plate.parts[2],
            *plate.parts[3],
        ]
    )


def _from_characters(kind: PlateKind, chars: Sequence[str]) -> Plate:
    if kind is PlateKind.MOTORCYCLE:
        text = "".join(chars)
        return Plate(PlateKind.MOTORCYCLE, (text[:3], text[3:]))
    return Plate(PlateKind.CAR, ("".join(chars[:2]), chars[2], "".join(chars[3:6]), "".join(chars[6:8])))


def vote_vehicle_type(reads: Iterable[FrameRead]) -> str | None:
    scores: dict[str, float] = defaultdict(float)
    for read in reads:
        if read.vehicle_type in VEHICLE_TYPES:
            scores[read.vehicle_type] += max(read.vehicle_confidence, 0.01)
    if not scores:
        return None
    return max(sorted(scores), key=lambda t: scores[t])


def vote(reads: Sequence[FrameRead], min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> VoteResult:
    """Combine the reads of one pass into one plate.

    Frames are weighted by their confidence. The plate kind (car / motorcycle) with the larger weight
    wins; then every character position is voted separately. The final confidence is the weakest
    position's share of the weight times the average confidence of the frames of that kind.
    """
    vehicle = vote_vehicle_type(reads)
    parsed = [(plate, max(0.0, min(1.0, r.confidence))) for r in reads if (plate := _parse(r.text)) is not None]
    if not parsed:
        return VoteResult(None, 0.0, vehicle, len(reads), 0, False)
    kind_weight: dict[PlateKind, float] = defaultdict(float)
    for plate, weight in parsed:
        kind_weight[plate.kind] += weight
    kind = max((PlateKind.CAR, PlateKind.MOTORCYCLE), key=lambda k: kind_weight.get(k, -1.0))
    same_kind = [(plate, weight) for plate, weight in parsed if plate.kind is kind]
    total = sum(weight for _p, weight in same_kind)
    if total <= 0:
        return VoteResult(None, 0.0, vehicle, len(reads), len(parsed), False)
    columns = [_characters(plate) for plate, _w in same_kind]
    chosen: list[str] = []
    weakest = 1.0
    for position in range(len(columns[0])):
        scores: dict[str, float] = defaultdict(float)
        for chars, (_plate, weight) in zip(columns, same_kind, strict=True):
            scores[chars[position]] += weight
        best = max(sorted(scores), key=lambda c: scores[c])
        chosen.append(best)
        weakest = min(weakest, scores[best] / total)
    average = total / len(same_kind)
    confidence = round(weakest * average, 4)
    if kind is PlateKind.MOTORCYCLE:
        vehicle = "motorcycle"
    return VoteResult(
        _from_characters(kind, chosen),
        confidence,
        vehicle,
        len(reads),
        len(parsed),
        confidence >= min_confidence,
    )


def confidence_percent(confidence: float) -> int:
    """Stored as an integer percent (0..100)."""
    return max(0, min(100, round(confidence * 100)))

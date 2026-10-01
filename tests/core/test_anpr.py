from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from caspian_parking.core.anpr import FrameRead, confidence_percent, vote, vote_vehicle_type
from caspian_parking.core.plate import PlateKind, parse_plate

PLATE = "12ب345-22"


def test_single_clear_read():
    result = vote([FrameRead(PLATE, 0.95, "sedan", 0.9)])
    assert result.plate == parse_plate(PLATE)
    assert result.confidence == pytest.approx(0.95)
    assert result.accepted
    assert result.vehicle_type == "sedan"
    assert (result.frames, result.valid_frames) == (1, 1)


def test_character_voting_fixes_single_frame_errors():
    reads = [
        FrameRead("12ب345-22", 0.9),
        FrameRead("17ب345-22", 0.6),  # one wrong digit
        FrameRead("12ب346-22", 0.7),  # another wrong digit
        FrameRead("12ب345-22", 0.85),
    ]
    result = vote(reads)
    assert result.plate == parse_plate(PLATE)
    assert 0.5 < result.confidence < 0.85


def test_disagreement_lowers_confidence_below_threshold():
    result = vote([FrameRead("12ب345-22", 0.9), FrameRead("12ج345-22", 0.9)])
    assert result.plate is not None
    assert result.confidence == pytest.approx(0.45)
    assert not result.accepted


def test_unreadable_frames_give_unidentified_pass():
    result = vote([FrameRead(None, 0.0, "sedan", 0.8), FrameRead("garbage", 0.4)])
    assert result.unidentified
    assert result.vehicle_type == "sedan"
    assert result.valid_frames == 0
    assert not result.accepted
    assert vote([]).unidentified


def test_motorcycle_plates_and_mixed_kinds():
    moto = "123-45678"
    result = vote([FrameRead(moto, 0.9), FrameRead(moto, 0.8), FrameRead(PLATE, 0.3)])
    assert result.plate.kind is PlateKind.MOTORCYCLE
    assert result.plate == parse_plate(moto)
    assert result.vehicle_type == "motorcycle"
    assert result.valid_frames == 3


def test_threshold_is_configurable():
    reads = [FrameRead(PLATE, 0.7)]
    assert not vote(reads).accepted
    assert vote(reads, min_confidence=0.6).accepted


def test_vehicle_type_vote():
    reads = [FrameRead(None, 0, "van", 0.9), FrameRead(None, 0, "sedan", 0.4), FrameRead(None, 0, "sedan", 0.4)]
    assert vote_vehicle_type(reads) == "van"
    assert vote_vehicle_type([FrameRead(None, 0, "spaceship", 1.0)]) is None


def test_confidence_percent_bounds():
    assert confidence_percent(0.876) == 88
    assert confidence_percent(-1) == 0
    assert confidence_percent(2) == 100


@given(st.integers(min_value=3, max_value=12), st.integers(min_value=0, max_value=10_000))
def test_majority_of_correct_frames_always_wins(frames, seed):
    """With more than half the frames correct (and the rest wrong in one random position) the vote is right."""
    rng = random.Random(seed)
    correct = frames // 2 + 1
    reads = [FrameRead(PLATE, 0.8) for _ in range(correct)]
    for _ in range(frames - correct):
        chars = list("12345")
        index = rng.randrange(len(chars))
        chars[index] = str((int(chars[index]) + 1 + rng.randrange(8)) % 10)
        reads.append(FrameRead(f"{''.join(chars[:2])}ب{''.join(chars[2:])}-22", 0.8))
    rng.shuffle(reads)
    assert vote(reads).plate == parse_plate(PLATE)

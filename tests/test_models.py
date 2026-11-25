"""Tests for the black-box baseline, the hybrid model and the model card."""

import numpy as np
import pytest

from presswatch.features import features_of
from presswatch.models import TARGETS, deserialize, serialize, train_model
from presswatch.sim.doe import doe_cycles


def _dataset(n, seed):
    cycles = doe_cycles(n, seed=seed)
    rows = [features_of(c) for c in cycles]
    targets = {
        "center_thickness_mm": np.array([c.truth.center_thickness_mm for c in cycles]),
        "pv_um": np.array([c.truth.pv_um for c in cycles]),
    }
    return rows, targets


@pytest.fixture(scope="module")
def train_small():
    return _dataset(60, seed=1)


@pytest.fixture(scope="module")
def train_full():
    return _dataset(150, seed=2)


@pytest.fixture(scope="module")
def test_set():
    return _dataset(200, seed=99)


def _rmse(model, data, target):
    rows, y = data
    return float(np.sqrt(np.mean((model.predict(rows)[target] - y[target]) ** 2)))


@pytest.mark.parametrize("kind", ["baseline", "hybrid"])
def test_model_predicts_every_target_for_every_row(kind, train_small):
    trained = train_model(kind, *train_small, seed=0)
    prediction = trained.model.predict(train_small[0][:5])
    assert set(prediction) == set(TARGETS)
    assert all(len(values) == 5 for values in prediction.values())


@pytest.mark.parametrize("target", TARGETS)
def test_hybrid_beats_black_box_when_training_data_is_scarce(target, train_small, test_set):
    hybrid = train_model("hybrid", *train_small, seed=0).model
    baseline = train_model("baseline", *train_small, seed=0).model
    assert _rmse(hybrid, test_set, target) < _rmse(baseline, test_set, target)


def test_hybrid_is_accurate_across_the_whole_process_window(train_full, test_set):
    # The DoE spans 1.3–2.9 mm center thickness. Part of the scatter comes from the
    # unmeasured heat loss during transfer, so ~1.5 % of the range is the realistic floor.
    hybrid = train_model("hybrid", *train_full, seed=0).model
    assert _rmse(hybrid, test_set, "center_thickness_mm") < 0.03
    assert _rmse(hybrid, test_set, "pv_um") < 0.6


def test_reported_holdout_metrics_are_honest(train_full, test_set):
    trained = train_model("hybrid", *train_full, seed=0)
    reported = trained.metrics["pv_um"]["rmse"]
    actual = _rmse(trained.model, test_set, "pv_um")
    assert reported == pytest.approx(actual, rel=0.5)


def test_serialization_round_trip_gives_identical_predictions(train_small):
    trained = train_model("hybrid", *train_small, seed=0)
    restored = deserialize(serialize(trained.model))
    rows = train_small[0][:10]
    for target in TARGETS:
        np.testing.assert_allclose(
            restored.predict(rows)[target], trained.model.predict(rows)[target]
        )


def test_model_card_documents_training_data_and_validity_range(train_small):
    card = train_model("hybrid", *train_small, seed=0).card
    assert card["training"]["n_samples"] == 60
    low, high = card["validity_range"]["press_force_n"]
    assert 600 <= low < high <= 1800
    assert len(card["training"]["data_hash"]) == 64
    assert card["limitations"]


def test_rows_outside_the_training_range_are_flagged(train_small):
    trained = train_model("hybrid", *train_small, seed=0)
    row = dict(train_small[0][0])
    assert trained.out_of_range(row) == []
    row["press_force_n"] = 5000.0
    assert trained.out_of_range(row) == ["press_force_n"]


def test_unknown_model_kind_is_rejected(train_small):
    with pytest.raises(ValueError, match="unbekannt"):
        train_model("magic", *train_small)

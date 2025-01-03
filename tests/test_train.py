"""Tests for module train."""

from contextlib import nullcontext
from pathlib import Path

import pytest

from orthoseg import train
from orthoseg.train import _train_args
from tests import test_helper


@pytest.mark.parametrize(
    "args",
    [
        (
            [
                "--config",
                "X:/Monitoring/OrthoSeg/test/test.ini",
                "predict.image_layer=LT-2023",
            ]
        )
    ],
)
def test_train_args(args):
    valid_args = _train_args(args=args)
    assert valid_args is not None
    assert valid_args.config is not None
    assert valid_args.config_overrules is not None


@pytest.mark.parametrize("config_path, exp_error", [("INVALID", True)])
def test_train(config_path, exp_error):
    if exp_error:
        handler = pytest.raises(ValueError)
    else:
        handler = nullcontext()
    with handler:
        train(config_path=Path("INVALID"))


def test_train_error_handling():
    """Force an error so the general error handler in train is tested."""
    with pytest.raises(
        RuntimeError,
        match="ERROR in train for footballfields_train_test",
    ):
        train(
            config_path=test_helper.SampleProjectFootball.train_config_path,
            config_overrules=["train.force_model_traindata_id=INVALID_TYPE"],
        )

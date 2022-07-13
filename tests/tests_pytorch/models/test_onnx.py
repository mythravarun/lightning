# Copyright The PyTorch Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os

import numpy as np
import onnxruntime
import pytest
import torch

import tests_pytorch.helpers.pipelines as tpipes
import tests_pytorch.helpers.utils as tutils
from pytorch_lightning import Trainer
from pytorch_lightning.demos.boring_classes import BoringModel
from tests_pytorch.helpers.runif import RunIf
from tests_pytorch.utilities.test_model_summary import UnorderedModel


def test_model_saves_with_input_sample(tmpdir):
    """Test that ONNX model saves with input sample and size is greater than 3 MB."""
    model = BoringModel()
    trainer = Trainer(fast_dev_run=True)
    trainer.fit(model)

    file_path = os.path.join(tmpdir, "model.onnx")
    input_sample = torch.randn((1, 32))
    model.to_onnx(file_path, input_sample)
    assert os.path.isfile(file_path)
    assert os.path.getsize(file_path) > 4e2


@pytest.mark.parametrize(
    "accelerator", [pytest.param("mps", marks=RunIf(mps=True)), pytest.param("gpu", marks=RunIf(min_cuda_gpus=True))]
)
def test_model_saves_on_gpu(tmpdir, accelerator):
    """Test that model saves on gpu."""
    model = BoringModel()
    trainer = Trainer(accelerator=accelerator, devices=1, fast_dev_run=True)
    trainer.fit(model)

    file_path = os.path.join(tmpdir, "model.onnx")
    input_sample = torch.randn((1, 32))
    model.to_onnx(file_path, input_sample)
    assert os.path.isfile(file_path)
    assert os.path.getsize(file_path) > 4e2


@RunIf(max_torch="1.10")
def test_model_saves_with_example_output(tmpdir):
    """Test that ONNX model saves when provided with example output."""
    model = BoringModel()
    trainer = Trainer(fast_dev_run=True)
    trainer.fit(model)

    file_path = os.path.join(tmpdir, "model.onnx")
    input_sample = torch.randn((1, 32))
    model.eval()
    example_outputs = model.forward(input_sample)
    model.to_onnx(file_path, input_sample, example_outputs=example_outputs)
    assert os.path.exists(file_path) is True


@pytest.mark.parametrize(
    ["modelclass", "input_sample"],
    [
        (BoringModel, torch.randn(1, 32)),
        (UnorderedModel, (torch.rand(2, 3), torch.rand(2, 10))),
    ],
)
def test_model_saves_with_example_input_array(tmpdir, modelclass, input_sample):
    """Test that ONNX model saves with example_input_array and size is greater than 3 MB."""
    model = modelclass()
    model.example_input_array = input_sample

    file_path = os.path.join(tmpdir, "model.onnx")
    model.to_onnx(file_path)
    assert os.path.exists(file_path) is True
    assert os.path.getsize(file_path) > 4e2


@RunIf(min_cuda_gpus=2)
def test_model_saves_on_multi_gpu(tmpdir):
    """Test that ONNX model saves on a distributed backend."""
    tutils.set_random_main_port()

    trainer_options = dict(
        default_root_dir=tmpdir,
        max_epochs=1,
        limit_train_batches=10,
        limit_val_batches=10,
        accelerator="cuda",
        devices=[0, 1],
        strategy="ddp_spawn",
        enable_progress_bar=False,
    )

    model = BoringModel()
    model.example_input_array = torch.randn(5, 32)

    tpipes.run_model_test(trainer_options, model, min_acc=0.08)

    file_path = os.path.join(tmpdir, "model.onnx")
    model.to_onnx(file_path)
    assert os.path.exists(file_path) is True


def test_verbose_param(tmpdir, capsys):
    """Test that output is present when verbose parameter is set."""
    model = BoringModel()
    model.example_input_array = torch.randn(5, 32)

    file_path = os.path.join(tmpdir, "model.onnx")
    model.to_onnx(file_path, verbose=True)
    captured = capsys.readouterr()
    assert "graph(%" in captured.out


def test_error_if_no_input(tmpdir):
    """Test that an error is thrown when there is no input tensor."""
    model = BoringModel()
    model.example_input_array = None
    file_path = os.path.join(tmpdir, "model.onnx")
    with pytest.raises(
        ValueError,
        match=r"Could not export to ONNX since neither `input_sample` nor"
        r" `model.example_input_array` attribute is set.",
    ):
        model.to_onnx(file_path)


def test_if_inference_output_is_valid(tmpdir):
    """Test that the output inferred from ONNX model is same as from PyTorch."""
    model = BoringModel()
    model.example_input_array = torch.randn(5, 32)

    trainer = Trainer(fast_dev_run=True)
    trainer.fit(model)

    model.eval()
    with torch.no_grad():
        torch_out = model(model.example_input_array)

    file_path = os.path.join(tmpdir, "model.onnx")
    model.to_onnx(file_path, model.example_input_array, export_params=True)

    ort_session = onnxruntime.InferenceSession(file_path)

    def to_numpy(tensor):
        return tensor.detach().cpu().numpy() if tensor.requires_grad else tensor.cpu().numpy()

    # compute ONNX Runtime output prediction
    ort_inputs = {ort_session.get_inputs()[0].name: to_numpy(model.example_input_array)}
    ort_outs = ort_session.run(None, ort_inputs)

    # compare ONNX Runtime and PyTorch results
    assert np.allclose(to_numpy(torch_out), ort_outs[0], rtol=1e-03, atol=1e-05)

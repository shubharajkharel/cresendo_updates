"""Controls making modifications to hydra configurations. Modifiers come with
a bold yellow output style."""


import lightning as L
import numpy as np
from omegaconf import DictConfig, ListConfig
from rich.console import Console


CONSOLE = Console()
OUTPUT_STYLE = "bold yellow"


def _log(msg):
    CONSOLE.log(msg, style=OUTPUT_STYLE)


def seed_everything(config):
    """Runs the Lightning ``seed_everything`` method.

    Parameters
    ----------
    config : omegaconf.dictconfig.DictConfig
    """

    if config.get("seed"):
        L.seed_everything(config.seed, workers=True)
        _log(f"Config seed set: {config.seed}")


def _update_for_random_architectures_(config):
    """Helper function for parsing through the ``architecture`` argument. This
    can either be a list (the actual interior layers) or a dictionary
    containing parameters for random initialization. The function returns the
    new interior layers of the network.

    Parameters
    ----------
    config : omegaconf.dictconfig.DictConfig
        The configuration file which will be modified in-place.
    """

    if isinstance(config.model["architecture"], ListConfig):
        return

    if not isinstance(config.model["architecture"], DictConfig):
        t = type(config.model["architecture"])
        raise ValueError(
            "Config's MLP architecture must be list or dict. " f"Found type {t}"
        )

    if config.get("seed") is None:
        raise ValueError(
            "For reproducibility, seed must be set for the random "
            "architecture initialization"
        )

    # Otherwise, we do our magic. Note that the random state should've been
    # provided earlier (via ``seed_everything``)
    neurons_range = config.model["architecture"]["neurons_range"]
    ramp_std = config.model["architecture"]["ramp_std"]

    # These will have been set beforehand even if previously unset
    n_in = config.model["input_dims"]
    n_out = config.model["output_dims"]

    # Make a random choice of the number of interior neurons
    n_interior = np.random.randint(
        low=neurons_range[0], high=neurons_range[1] + 1
    )

    # Now we interpolate
    x = [1, n_interior]
    y = [n_in, n_out]
    x_eval = np.linspace(1, n_interior, n_interior)
    y_interp = np.interp(x_eval, x, y)
    _log(
        "Using randomized architecture: from parameters "
        f"neurons_range={neurons_range} and ramp_std={ramp_std:.05f}. "
        f"Interior architecture before noise: {y_interp.tolist()}"
    )

    # Add random noise
    y_interp = y_interp + np.random.normal(scale=ramp_std, size=len(y_interp))
    y_interp = y_interp.astype(int)
    y_interp[y_interp <= 0] = 1
    _log(f"Architecture after noise: {y_interp}")

    config.model["architecture"] = y_interp.tolist()


def update_architecture_in_out_(config, datamodule):
    """Given the datamodule with properties ``n_features`` and ``n_targets``,
    this updates the config if the model is of type mlp. Basically, if the
    ``input_dims`` and ``output_dims`` are set to "auto", this function will
    populate those values based on the dataloader. In addition,
    ``architecture`` can be a list (corresponding to the dimensions of the
    hidden layers themselves), or a dictionary. If this is a list, it is just
    the architecture itself. If it is a dictionary, the first required key is
    ``neurons_range``, which is a list of two integers (the low and high,
    inclusive) values for the number of interior layers, chosen in a uniform
    random way. The second key is ``ramp_std``. This is a standard deviation of
    a Guassian distribution which represents the spread of random noise around
    a linear interpolant between the number of input and output features. This
    is a way to generate some randomness in the architecture while still
    preserving the funnel-like structure of the network.

    Note
    ----
    This method will only execute on models matching the path
    ``crescendo.models.mlp``.

    Parameters
    ----------
    config : omegaconf.dictconfig.DictConfig
        The configuration file which will be modified in-place.
    datamodule : lightning.LightningDataModule
        The LightningDataModule corresponding to the dataset in use.
    """

    if "crescendo.models.mlp" not in config.model["_target_"]:
        return

    n_features = datamodule.n_features
    if config.model["input_dims"] == "auto":
        config.model["input_dims"] = n_features
        _log(
            "Input MLP dimensions automatically set from dataloader "
            f"to n_features={n_features}"
        )

    n_targets = datamodule.n_targets
    if config.model["output_dims"] == "auto":
        config.model["output_dims"] = n_targets
        _log(
            "Output MLP dimensions automatically set from dataloader "
            f"to n_targets={n_targets}"
        )

    _update_for_random_architectures_(config)


def compile_model(config, model):
    """Attempts to executes the pytorch 2.0 model compiling for further
    acceleration. Will fail gracefully."""

    if not config.compile:
        _log("Model compile==False")
        return model

    import torch
    from torch import _dynamo

    _dynamo.config.suppress_errors = True
    model = torch.compile(model)
    _log("Model compilation attempted... see logs above")
    return model

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from crescendo.data import ArrayRegressionDataModule


class TestArrayRegressionDataModule:
    def test_ArrayRegressionDataModule(
        self, X_array_int_cols, Y_array_int_cols
    ):
        # Save the dummy data to disk
        with TemporaryDirectory() as d:
            np.save(str(Path(d) / "X_train.npy"), X_array_int_cols)
            np.save(str(Path(d) / "Y_train.npy"), Y_array_int_cols)

            datamodule = ArrayRegressionDataModule(
                data_dir=d, normalize_inputs=False, feature_select="0:5"
            )
            assert datamodule.X_train.shape[1] == 5
            for ii, column in enumerate(datamodule.X_train.T):
                assert column[0] == ii

            datamodule = ArrayRegressionDataModule(
                data_dir=d, normalize_inputs=False, feature_select="0:3,6:9"
            )

            assert datamodule.X_train.shape[1] == 6
            vals = [0, 1, 2, 6, 7, 8]
            for ii, column in enumerate(datamodule.X_train.T):
                assert column[0] == vals[ii]
#!/usr/bin/env python3

import pytest

from crescendo.datasets.qm9 import QMXDataset
from crescendo.defaults import QM9_TEST_DATA_PATH, QM8_TEST_DATA_PATH


class TestQMXDataset:

    def test_load(self):
        ds = QMXDataset()
        ds.load(QM9_TEST_DATA_PATH, min_heavy_atoms=1)

    def test_loadspectra(self):
        qm8_test = QMXDataset()
        qm8_test.load_qm8_electronic_properties(QM8_TEST_DATA_PATH)
        S1 = qm8_test.qm8_electronic_properties[1]
        assert S1 == [0.43295186, 0.40993872, 0.1832, 0.1832]

    def test_ml_ready_qm8_EP(self):
        ds = QMXDataset()
        ds.load(QM9_TEST_DATA_PATH, min_heavy_atoms=1)
        ds.load_qm8_electronic_properties(QM8_TEST_DATA_PATH)
        ds.ml_ready(
            'qm8_EP',
            atom_feature_list=['type', 'hybridization'],
            bond_feature_list=['type']
        )
        ds.ml_data.sort(key=lambda x: x[2])  # sort by ID
        assert ds.ml_data[0][1] == [0.43295186, 0.40993872, 0.1832, 0.1832]

    def test_ml_ready_qm9_properties(self):
        ds = QMXDataset()
        ds.load(QM9_TEST_DATA_PATH, min_heavy_atoms=1)
        ds.ml_ready(
            'qm9_prop',
            atom_feature_list=['type', 'hybridization'],
            bond_feature_list=['type'],
            target_features=[0, 1, 2]
        )

    def test_with_scaling_targets(self):
        ds = QMXDataset()
        ds.load(QM9_TEST_DATA_PATH, min_heavy_atoms=1)
        ds.ml_ready(
            'qm9_prop',
            scale_targets=True,
            atom_feature_list=['type', 'hybridization'],
            bond_feature_list=['type'],
            target_features=[0, 1, 2]
        )

    def test_get_data_loaders(self):
        ds = QMXDataset()
        ds.load(QM9_TEST_DATA_PATH)
        ds.load_qm8_electronic_properties(QM8_TEST_DATA_PATH)
        ds.ml_ready(
            'qm8_EP',
            atom_feature_list=['type', 'hybridization'],
            bond_feature_list=['type']
        )
        loader_dict = ds.get_data_loaders(
            p_tvt=(0.1, 0.1, None), seed=1234, method='random',
            batch_sizes=(32, 32, 32)
        )

        # The testing set size is small enough that we only have one batch
        # each
        for b in loader_dict['test']:
            test_id = b[2].tolist()
        for b in loader_dict['valid']:
            valid_id = b[2].tolist()
        for b in loader_dict['train']:
            train_id = b[2].tolist()

        assert set(test_id).isdisjoint(valid_id)
        assert set(test_id).isdisjoint(train_id)
        assert set(train_id).isdisjoint(valid_id)


@pytest.fixture
def data():
    ds = QMXDataset()
    ds.load(QM9_TEST_DATA_PATH, min_heavy_atoms=1)
    return ds.raw


class TestQM9Datum:

    def test_000001(self, data):
        datum = data[1]
        assert datum.mw == 16.043
        assert datum.smiles == 'C'

    def test_100001(self, data):
        datum = data[100001]
        assert datum.mw == 128.21499999999997
        assert datum.smiles == 'CCC(C)(C)[C@@H](C)C=O'

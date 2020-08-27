#!/usr/bin/env python3

"""Module for loading in data from the QM9 database."""

import os as os
from typing import List

import glob2
from ntpath import basename
import numpy as np
import pickle as pickle
import pymatgen.core.structure as pmgstruc
from rdkit import Chem
from rdkit.Chem.Descriptors import MolWt
import torch

from crescendo.defaults import QM9_ENV_VAR, QM8_EP_ENV_VAR, \
    INDEPENDENT_QM9_PROPS
from crescendo.featurizers.graphs import mol_to_graph_via_DGL, \
    get_number_of_classes_per_feature
from crescendo.samplers.base import Sampler
from crescendo.utils.graphs import graph_to_vector_dummy_dataset
from crescendo.utils.logger import logger_default as dlog
from crescendo.utils.py_utils import intersection, \
    check_for_environment_variable
from crescendo.utils.timing import time_func


aromatic_pattern = Chem.MolFromSmarts('[a]')

double_bond_patterns = [
    Chem.MolFromSmarts('C=C'), Chem.MolFromSmarts('C=O'),
    Chem.MolFromSmarts('C=N'), Chem.MolFromSmarts('O=O'),
    Chem.MolFromSmarts('O=N'), Chem.MolFromSmarts('N=N')
]

triple_bond_patterns = [
    Chem.MolFromSmarts('C#C'), Chem.MolFromSmarts('C#N'),
    Chem.MolFromSmarts('N#N')
]

hetero_bond_patterns = [
    Chem.MolFromSmarts('C~O'), Chem.MolFromSmarts('C~N'),
    Chem.MolFromSmarts('N~O')
]


class QM9SmilesDatum:
    """Functions for determining structures from a smiles input using rdkit
    chem.

    Current Structure types
        .ring -contains any ring
        .ring5 -contains a 5 atom ring
        .ring4 -contains a 4 atom ring
        .aromatic -contains an aromatic structure
        .doublebond -contains a double bond with the combinations of carbon,
        oxygen, and nitrogen
        .triplebond -contains a triple bond with the combinations of carbon
        and nitrogen
        .singlebond -does not contain .doublebond .triplebond and .aromatic

    Example
    -------
    #Molecule Benzene aromatic structure
    >>> d = QM9SmilesDatum('C1=CC=CC=C1')
    >>> d.is_aromatic()
    True

    Attributes
    ----------
    TODO
    """

    def __init__(self, smiles, other_props, xyz, elements, zwitter, qm9_id):
        """
        Parameters
        ----------
        smiles : str
            smiles of target molecule as string
        """

        self.smiles = smiles
        self.mol = Chem.MolFromSmiles(smiles)
        self.mw = MolWt(self.mol)
        self.other_props = other_props
        self.xyz = xyz
        self.elements = elements
        self.zwitter = zwitter
        self.qm9_id = qm9_id

    def to_graph(
        self, atom_feature_list=['type', 'hybridization'],
        bond_feature_list=['type']
    ):
        """Initializes the graph attribute of the molecule object. See
        crescendo.featurizer.graphs.mol_to_graph_via_DGL for more details."""

        return mol_to_graph_via_DGL(
            self.mol, atom_feature_list, bond_feature_list
        )

    def has_n_membered_ring(self, n=None) -> bool:
        """Returns True if the mol attribute (the molecule object in rdkit
        representing the Smiles string) has an n-membered ring.

        Parameters
        ----------
        n : int, optional
            The size of the ring. If None, will simply check if the molecule
            has any ring (using the substructure matching string '[r]' instead
            of [f'r{n}']). Default is None.

        Returns
        -------
        bool
            True if a match is found. False otherwise.
        """

        n = '' if n is None else n
        return self.mol.HasSubstructMatch(Chem.MolFromSmarts(f'[r{n}]'))

    def is_aromatic(self) -> bool:
        """If the molecule has any aromatic pattern, returns True, else
        returns False. This is checked by trying to locate the substructure
        match for the string '[a]'.

        Returns
        -------
        bool
        """

        return self.mol.HasSubstructMatch(aromatic_pattern)

    def has_double_bond(self) -> bool:
        """Checks the molecule for double bonds. Note that by default this
        method assumes the data point is from QM9, and only checks the
        atoms capable of forming double bonds in QM9, so, it will only check
        C, N and O, and their 6 combinations.

        Returns
        -------
        bool
        """

        return any([
            self.mol.HasSubstructMatch(p)
            for p in double_bond_patterns
        ])

    def has_triple_bond(self) -> bool:
        """Checks the molecule for triple bonds. Note that by default this
        method assumes the data point is from QM9, and only checks the
        atoms capable of forming triple bonds in QM9, so, it will only check
        C#C, C#N and N#N.

        Returns
        -------
        bool
        """

        return any([
            self.mol.HasSubstructMatch(p)
            for p in triple_bond_patterns
        ])

    def has_hetero_bond(self) -> bool:
        """Checks the molecule for heter bonds. Note that by default this
        method assumes the data point is from QM9, and only checks the
        atoms capable of forming hetero bonds in QM9, so, it will only check
        C~O, C~N and N~O.

        Returns
        -------
        bool
        """

        return any([
            self.mol.HasSubstructMatch(p)
            for p in hetero_bond_patterns
        ])

    def to_pmg_molecule(self):
        """Convenience method which turns the current QM9 datum into a Pymatgen
        Molecule, which can be processed further in other useful ways due to
        that classes' built-in methods."""

        return pmgstruc.Molecule(species=self.elements, coords=self.xyz)

    def as_dict(self) -> dict:
        """Convenience method which turns the current QM9 datum into a
        dictionary formatted by the attributes of the object. Can be used for
        serialization or de-serialization in a JSON format."""

        return dict(vars(self))

    @staticmethod
    def from_dict(dictionary):
        return QM9SmilesDatum(**dictionary)


def parse_QM8_electronic_properties(
    props,
    selected_properties=[0, 13, 14, 15, 16]
):
    """Parses a list of strings into the correct floats that correspond to the
    electronic properties in the QM8 database.

    the properties are as follows (1-indexed):
        1  : index
        2  : RI-CC2/def2TZVP E1 in au
        3  : RI-CC2/def2TZVP E2 in au
        4  : RI-CC2/def2TZVP f1 in au in length representation
        5  : RI-CC2/def2TZVP f2 in au in length representation
        6  : LR-TDPBE0/def2SVP E1 in au
        7  : LR-TDPBE0/def2SVP E2 in au
        8  : LR-TDPBE0/def2SVP f1 in au in length representation
        9  : LR-TDPBE0/def2SVP f2 in au in length representation
        10 : LR-TDPBE0/def2TZVP E1 in au
        11 : LR-TDPBE0/def2TZVP E2 in au
        12 : LR-TDPBE0/def2TZVP f1 in au in length representation
        13 : LR-TDPBE0/def2TZVP f2 in au in length representation
        14 : LR-TDCAM-B3LYP/def2TZVP E1 in au
        15 : LR-TDCAM-B3LYP/def2TZVP E2 in au
        16 : LR-TDCAM-B3LYP/def2TZVP f1 in au in length representation
        17 : LR-TDCAM-B3LYP/def2TZVP f2 in au in length representation

    Note `au` = atomic units

    Parameters
    ----------
    props : list[str]
        Initial properties in string format.
    selected_properties : List[int]

    Returns
    -------
    int, list[float]
        The QM9 ID and list of properties (list[float]).
    """

    qm8_id = int(props[0])
    other = props[1:]
    other = [
        float(prop) for ii, prop in enumerate(other)
        if ii in selected_properties
    ]
    return (qm8_id, other)


def parse_QM9_scalar_properties(props, selected_properties=None):
    """Parses a list of strings into the correct floats that correspond to the
    molecular properties in the QM9 database.

    Only the following properties turn out to be statistically relevant in this
    dataset: selected_properties=[0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 14]. These
    properties are the statistically independent contributions as calculated
    via a linear correlation model, and together the capture >99% of the
    variance of the dataset.

    Note, according to the paper (Table 3)
    https://www.nature.com/articles/sdata201422.pdf
    the properties are as follows (1-indexed):
        1  : gdb9 index (we'll ignore)
        2  : identifier
        3  : "A" (GHz) rotational constant
        4  : "B" (GHz) rotational constant
        5  : "C" (GHz) rotational constant
        6  : "mu" (Debeye) dipole moment
        7  : "alpha" (a0^3) isotropic polarizability
        8  : "HOMO energy" (Ha)
        9  : "LUMO energy" (Ha)
        10 : "E gap" (Ha) 8-9 (might be uHa?)
        11 : "<R^2>" (a0^2) electronic spatial extent
        12 : "zpve" (Ha) zero-point vibrational energy
        13 : "U0" (Ha) internal energy at 0 K
        14 : "U" (Ha) internal energy at 198.15 K
        15 : "H" (Ha) enthalpy at 298.15 K
        16 : "G" (Ha) gibbs free energy at 298.15 K
        17 : "Cv" (cal/molK) heat capacity at 298.15 K

    The relevant ones (2 through 17 inclusive) will be returned in a new list
    with each element being of the correct type.

    Parameters
    ----------
    props : list[str]
        Initial properties in string format.
    selected_properties : List[int], optional
        Selected properties. If None, take all the properties.

    Returns
    -------
    int, list[float]
        The QM9 ID and list of properties (list[float]).
    """

    qm9_id = int(props[1])
    other = props[2:]

    if selected_properties is None:
        other = [float(prop) for ii, prop in enumerate(other)]
    else:
        other = [
            float(prop) for ii, prop in enumerate(other)
            if ii in selected_properties
        ]
    return (qm9_id, other)


def read_qm9_xyz(xyz_path, canonical=True):
    """Function for reading .xyz files like those present in QM9. Note this
    does not read in geometry information, just properties and SMILES codes.
    For a detailed description of the properties contained in the qm9 database,
    see this manuscript: https://www.nature.com/articles/sdata201422.pdf

    Parameters
    ----------
    xyz_path : str
        Absolute path to the xyz file.
    canonical : bool
        Whether or not to use the canonical SMILES codes instead of the
        standard ones.

    Returns
    -------
    Tuple containing relevant information and boolean flag for whether or not
    the molecule is a Zwitter-ionic compound or not.
    """

    with open(xyz_path, 'r') as file:
        n_atoms = int(file.readline())
        qm9_id, other_props = \
            parse_QM9_scalar_properties(file.readline().split())

        elements = []
        xyzs = []
        for ii in range(n_atoms):
            line = file.readline().replace('.*^', 'e').replace('*^', 'e')
            line = line.split()
            elements.append(str(line[0]))
            xyzs.append(np.array(line[1:4], dtype=float))

        xyzs = np.array(xyzs)

        # Skip extra vibrational information
        file.readline()

        # Now read the SMILES code
        smiles = file.readline().split()
        _smiles = smiles[int(canonical)]

        zwitter = '+' in smiles[0] or '-' in smiles[0]

    return (qm9_id, _smiles, other_props, xyzs, elements, zwitter)


def generate_qm9_pickle(
    qm9_directory: str = None, write_loc: str = './qm9_data.pickle',
    custom_range: List[int] = None
) -> List:
    """Given a path to the QM9 directory, creates and writes a .pickle file
    representing the entire QM9 database.

    Parameters
    ----------
    qm9_directory : str
        Location of QM9 database files. Can load from path.
    write_loc : str
        Where pickle file should be written.
    custom_range : list
        Subset of integers to selectively load in.

    Returns
    -------
    List of QM9 molecules formatted by the read_qm9_xyz function.
    """

    if qm9_directory is None:
        qm9_directory = check_for_environment_variable(QM9_ENV_VAR)

    entries = glob2.glob(qm9_directory + "/*.xyz")

    if custom_range is not None:
        prefix = 'dsgdb9nsd_'
        suffix = '.xyz'
        # Isolate the numbers of available QM9 values
        entry_numbers = {
            int(entry.split('_')[1].split('.')[0]) for entry in entries
        }
        use_numbers = entry_numbers.intersection(set(custom_range))
        to_use_entries = [
            prefix + str(entry).zfill(6) + suffix for entry in
            use_numbers
        ]

    else:
        to_use_entries = entries

    molecules = []
    for ent in to_use_entries:
        mol_path = os.path.join(qm9_directory, ent)
        molecules.append(read_qm9_xyz(mol_path))

    if write_loc:
        with open(write_loc, 'wb') as f:
            pickle.dump(molecules, f)

    return molecules


class QMXDataset(torch.utils.data.Dataset):
    """Container for the QMX data, where X is some integer. Although not the
    proper notation, we refer to X as in general, either 8 or 9 (usually),
    where X=max number of heavy atoms (C, N, O and F)/molecule.

    Attributes
    ----------
    raw : dict
        A dictionary or raw data as constructed by the load method. In the case
        of QM9, this is simply the QM9SmilesDatum object keyed by the QM9 ID.
    qm8_electronic_properties : dict
        Default as None, this is initialized by the
        load_qm8_electronic_properties, which loads in the qm8 electronic
        properties from a specified path and stores them, again by QM9 ID.
    ml_ready : list
        A result of the featurize method. This will always be a list of lists,
        where each list has 3 entries, [feature, target, metadata]. The format
        of these features, targets and metadata will of course depend on the
        type of featurizer the user specifies. The metadata will tend to be
        some combination of an identifier and perhaps other information.
    debug : int
        Default as -1, the debug flag simply indexes the max number of
        geometries to load. Will load all by default (indicated by -1).
    n_class_per_feature : list
        Metadata information (at the level of the dataset, not the individual
        data points) which will be passed to the MPNN initializer for graph
        based methods.
    """

    def __init__(self, *args, debug=-1, **kwargs):
        super().__init__(*args, **kwargs)
        self.raw = dict()
        self.qm8_electronic_properties = None
        self.ml_data = None
        self.debug = debug
        self.n_class_per_feature = None

    def __getitem__(self, ii):
        return self.ml_data[ii]

    def __len__(self):
        return len(self.ml_dat)

    @property
    def max_heavy_atoms(self):
        return self._max_heavy_atoms

    @max_heavy_atoms.setter
    def max_heavy_atoms(self, a):
        assert isinstance(a, int)
        assert a > 0
        dlog.info(f"Max heavy atoms set to {a}")
        self._max_heavy_atoms = a

    @property
    def min_heavy_atoms(self):
        return self._min_heavy_atoms

    @min_heavy_atoms.setter
    def min_heavy_atoms(self, a):
        assert isinstance(a, int)
        assert a > 0
        dlog.info(f"Min heavy atoms set to {a}")
        self._min_heavy_atoms = a

    @property
    def keep_zwitter(self):
        return self._keep_zwitter

    @keep_zwitter.setter
    def keep_zwitter(self, z):
        assert isinstance(z, bool)
        dlog.info(f"Keeping zwitterions set to {z}")
        self._keep_zwitter = z

    @property
    def canonical(self):
        return self._canonical

    @canonical.setter
    def canonical(self, c):
        assert isinstance(c, bool)
        dlog.info(f"Canonical SMILES set to {c}")
        self._canonical = c

    @time_func(dlog)
    def load(
        self,
        path=None,
        min_heavy_atoms=2,
        max_heavy_atoms=9,
        keep_zwitter=False,
        canonical=True,
        log_every=10000,
        dummy_data=None,
        dummy_default_max_size=10,
        dummy_default_max_n_class=7,
        dummy_default_max_e_class=5,
        dummy_default_target_size=4
    ):
        """Loads in the QM9 data as set via the path in the initializer, and
        also optionally other auxiliary data, such as spectra.

        Parameters
        ----------
        dummy_data : int, optional
            If not none, this will override all other kwargs in this method,
            and will load in a dummy dataset directly to ml_data so as to
            prepare immediately for a test of the ML pipeline. The integer
            passed represents the size of the dataset, with other parameters
            hard coded and defined by default.
        path : str, optional
            Path to the directory containing the qm9 .xyz files. For instance,
            if your xyz files are in directory /Users/me/data, then that should
            be the path. If path is None by default, it will check the
            os.environ dictionary for QM9_DATA_PATH, and if that does not
            exist, it will throw an error.
        min_heavy_atoms : int
            We exclude the trivial atomic cases in the QM9 dataset by default.
        max_heavy_atoms : int
            Maximum number of total heavy atoms (C, N, O, F) allowed in the
            dataset. By default, QM9 allows for... wait for it... 9 heavy
            atoms, but we can reduce this to, e.g., 8 to match other subsets
            used in the literature. Default is 9.
        keep_zwitter : bool
            If True, will keep zwitterionic compounds
            (https://en.wikipedia.org/wiki/Zwitterion) in the database. Default
            is False.
        canonical : bool
            If True, will use the canonical SMILES codes. Default is True.
        """

        if dummy_data is not None:
            dlog.warning(
                f"You are loading fake generated data of ds_size={dummy_data}"
            )
            kwargs = {
                'N': dummy_data,
                'graph_max_size': dummy_default_max_size,
                'graph_max_n_class': dummy_default_max_n_class,
                'graph_max_e_class': dummy_default_max_e_class,
                'target_size': dummy_default_target_size
            }
            self.ml_data = graph_to_vector_dummy_dataset(**kwargs)
            self.n_class_per_feature = [
                [dummy_default_max_n_class], [dummy_default_max_e_class]
            ]
            return

        if path is None:
            path = check_for_environment_variable(QM9_ENV_VAR)
        dlog.info(f"Loading QM9 from {path}")

        self.min_heavy_atoms = min_heavy_atoms
        self.max_heavy_atoms = max_heavy_atoms
        self.keep_zwitter = keep_zwitter
        self.canonical = canonical

        # Get a list of all of the paths of the xyz files
        all_xyz_paths = glob2.glob(path + "/*.xyz")
        total_xyz = len(all_xyz_paths)

        # Trim the total dataset if we're debugging and want to go fast
        if self.debug > 0:
            all_xyz_paths = all_xyz_paths[:self.debug]
        dlog.info(f"Loading from {total_xyz} geometry files")

        # Load in all of the data.
        for ii, current_path in enumerate(all_xyz_paths):

            if ii % log_every == 0 and ii != 0:
                pc = ii / total_xyz * 100.0
                dlog.info(
                    f"latest read from: {basename(current_path)} ({pc:.00f}%)"
                )

            (qm9_id, smiles, other_props, xyzs, elements, zwitter) = \
                read_qm9_xyz(current_path, canonical=self.canonical)

            # Exclude molecules outside of the allowed heavy atom range
            n_heavy = sum([e != 'H' for e in elements])
            if not self.min_heavy_atoms <= n_heavy <= self.max_heavy_atoms:
                continue

            if not self.keep_zwitter and zwitter:
                continue

            self.raw[qm9_id] = QM9SmilesDatum(
                smiles, other_props, xyzs, elements, zwitter, qm9_id
            )

        dlog.info(f"Total number of data points: {len(self.raw)}")

    def load_qm8_electronic_properties(self, path=None):
        """Function for loading Electronic properties for QM8 files.

        Parameters
        ----------
        path : str, optional
            Absolute path to the file containing the spectral information
            in the QM8 database. If None, checks for the QM8_EP_DATA_PATH
            environment variable.
        """

        if path is None:
            path = check_for_environment_variable(QM8_EP_ENV_VAR)

        self.qm8_electronic_properties = dict()

        dlog.info(f"Reading QM8 electronic properties from {path}")

        with open(path, 'r') as file:
            line = '#'
            while '#' in line:
                line = file.readline()
            while line != '':
                qm8_id, props = \
                    parse_QM8_electronic_properties(line.split())
                self.qm8_electronic_properties[qm8_id] = props
                line = file.readline()

        dlog.info(
            "Total number of data points "
            f"{len(self.qm8_electronic_properties)}"
        )

    def analyze(self, n=None):
        """Performs the analysis of the currently loaded QM9 Dataset.

        Parameters
        ----------
        n : int, optional
            The size of the rings it checking the data set for

        Returns
        -------
        A Dictionary containing the amount of molecules containing each
        structure contained in the loaded data
            Current Structures
                aramotic
                double bonds
                triple bonds
                hetero bonds
                n membered rings
        """

        analysis = {
            'num_aromatic': 0,
            'num_double_bond': 0,
            'num_triple_bond': 0,
            'num_hetero_bond': 0,
            'num_n_membered_ring': 0
            }

        for qmx_id in self.raw:
            if self.raw[qmx_id].is_aromatic():
                analysis['num_aromatic'] += 1
            if self.raw[qmx_id].has_double_bond():
                analysis['num_double_bond'] += 1
            if self.raw[qmx_id].has_triple_bond():
                analysis['num_triple_bond'] += 1
            if self.raw[qmx_id].has_hetero_bond():
                analysis['num_hetero_bond'] += 1
            if self.raw[qmx_id].has_n_membered_ring(n):
                analysis['num_n_membered_ring'] += 1

        return analysis

    @staticmethod
    def collating_function_graph_to_vector(batch):
        """Collates the graph-fixed length vector combination. Recall that
        in this case, each element of batch is a three vector containing the
        graph, the target and the ID."""

        _ids = torch.tensor([xx[2] for xx in batch]).long()

        # Each target is the same length, so we can use standard batching for
        # it.
        targets = torch.tensor([xx[1] for xx in batch]).float()

        # However, graphs are not of the same "length" (diagonally on the
        # adjacency matrix), so we need to be careful. Usually, dgl's batch
        # method would work just fine here, but for multi-gpu training, we
        # need to catch some subtleties, since the batch itself is split apart
        # equally onto many GPU's, but torch doesn't know how to properly split
        # a batch of graphs. So, we manually partition the graphs here, and
        # will batch the output of the collating function before training.
        # This is now just a list of graphs.
        graphs = [xx[0] for xx in batch]

        return (graphs, targets, _ids)

    @time_func(dlog)
    def _qm8_EP_featurizer(self, atom_f_list, bond_f_list):
        """constructs the ml_ready list by pairing QM9 structures with
        the corresponding electronic properties as read in by the
        load_qm8_electronic_properties method. Requires atom_feature_list and
        bond_feature_list as kwargs (in featurizer) and stores an attribute
        n_class_per_feature."""

        # First, check that the QM8 electronic properties were read.
        if self.qm8_electronic_properties is None:
            error = \
                "Read in qm8 electronic properties first - doing nothing"
            dlog.error(error)
            return

        # Get the overlap between the QM9 structural data and the
        # electronic properties.
        ids_to_featurize = intersection(
            list(self.raw.keys()),
            list(self.qm8_electronic_properties.keys())
        )
        dlog.info(
            "Determined intersection between QM9 structural data and "
            f"QM8 electronic properties of length {len(ids_to_featurize)}"
        )

        # Get the number of classes per feature, used in initializing
        # the MPNN later.
        dlog.info(f"Using atom feature list: {atom_f_list}")
        dlog.info(f"Using bond feature list: {bond_f_list}")

        self.n_class_per_feature = get_number_of_classes_per_feature(
            atom_f_list, bond_f_list
        )
        dlog.info(
            f"Storing self.n_class_per_feature: {self.n_class_per_feature}"
        )

        self.ml_data = [
            [
                self.raw[_id].to_graph(atom_f_list, bond_f_list),
                self.qm8_electronic_properties[_id], int(_id)
            ] for _id in ids_to_featurize
        ]

    @time_func(dlog)
    def _qm9_property_featurizer(
        self, atom_f_list, bond_f_list, target_features
    ):
        """Chooses the features of the model as the indexes specified in
        `features`, corresponding to the extra properties in the QM9 dataset.
        Note that not all of these features are statistically independent,
        and when those features are selected, warnings will be thrown."""

        if not set(target_features).issubset(set(INDEPENDENT_QM9_PROPS)):
            dlog.warning(
                f"Chosen features {target_features} is not a subset of the "
                f"pre-determined independent set {INDEPENDENT_QM9_PROPS}"
            )

        self.ml_data = [
            [
                self.raw[_id].to_graph(atom_f_list, bond_f_list),
                [self.raw[_id].other_props[ii] for ii in target_features],
                int(_id)
            ] for _id in self.raw.keys()
        ]

        self.n_class_per_feature = get_number_of_classes_per_feature(
            atom_f_list, bond_f_list
        )
        dlog.info(
            f"Storing self.n_class_per_feature: {self.n_class_per_feature}"
        )

    def _compute_and_log_mean_sd_targets(self, ml_data_target_index=1):
        """Calculates and returns the mean and standard deviation of the
        targets, and also logs the values. Assumes the targets are in the 1st
        index of the ml_ready data by default. Also assumes that the target
        data is in vector format that can be concatenated into a numpy array.
        """

        trgs = np.array([xx[ml_data_target_index] for xx in self.ml_data])
        mean = trgs.mean(axis=0)
        sd = trgs.std(axis=0)
        dlog.info(
            "Mean/sd of target data is "
            f"{mean.mean():.02e} +/- {sd.mean():.02e}"
        )
        return mean, sd

    def _scale_target_data(self, mu, sd, ml_data_target_index=1):
        """Scales the target data forward."""

        for ii in range(len(self.ml_data)):
            self.ml_data[ii][ml_data_target_index] = \
                list((np.array(
                    self.ml_data[ii][ml_data_target_index]
                ) - mu) / sd)

    def ml_ready(self, featurizer, scale_targets=False, **kwargs):
        """This method is the workhorse of the QMXLoader. It will featurize
        the raw data depending on the user settings. Also based on the
        featurizer, it will construct the appropriate data loader objects.

        Parameters
        ----------
        featurizer : {to_graph, qm8_EP}
            The featurizer options are described henceforth or in docstrings:
            * to_graph : temporary debugging, just returns graphs and metadata
            * qm8_EP : see _qm8_EP_featurizer
        seed : int, optional
            The most important seed step in the entire pipeline, as this
            determines the train/validation/test split. It is used to seed the
            sampler.

        Returns
        -------
        dict
            Metadata about the features and targets.
        """

        dlog.info(f"Attempting to run featurizer: {featurizer}")
        trg_meta = None

        if featurizer == 'to_graph':

            # Get the number of classes per feature, used in initializing
            # the MPNN later.
            atom_f_list = kwargs['atom_feature_list']
            bond_f_list = kwargs['bond_feature_list']
            n_class_per_feature = get_number_of_classes_per_feature(
                atom_f_list, bond_f_list
            )

            # Convert every object in self.raw -> graphs
            return {
                _id: datum.to_graph(atom_f_list, bond_f_list)
                for _id, datum in self.raw.items()
            }, n_class_per_feature

        elif featurizer == 'qm8_EP':
            atom_f_list = kwargs['atom_feature_list']
            bond_f_list = kwargs['bond_feature_list']
            self._qm8_EP_featurizer(atom_f_list, bond_f_list)
            if scale_targets:
                mu, sd = self._compute_and_log_mean_sd_targets()
                self._scale_target_data(mu, sd)
                self._compute_and_log_mean_sd_targets()
                trg_meta = (mu, sd)

        elif featurizer == 'qm9_prop':
            atom_f_list = kwargs['atom_feature_list']
            bond_f_list = kwargs['bond_feature_list']
            target_features = kwargs['target_features']
            self._qm9_property_featurizer(
                atom_f_list, bond_f_list, target_features
            )
            if scale_targets:
                mu, sd = self._compute_and_log_mean_sd_targets()
                self._scale_target_data(mu, sd)
                self._compute_and_log_mean_sd_targets()
                trg_meta = (mu, sd)

        else:
            critical = f"Unknown featurizer: {featurizer}"
            dlog.critical(critical)
            raise RuntimeError(critical)

        dlog.info(
            "Initialized `self.ml_data` of length "
            f"{len(self.ml_data)}"
        )

        return {
            'feature_metadata': None,
            'target_metadata': trg_meta
        }

    def _execute_random_points_sampling(self, p_test, p_valid, p_train, seed):
        """Initializes a sampler, performs random sampling and returns a
        dictionary of the split indexes."""

        s = Sampler(len(self.ml_data))
        s.shuffle_(seed)
        assert s.indexes_modified
        return s.split(p_test, p_valid, p_train=p_train)

    def get_data_loaders(
        self, p_tvt=(0.1, 0.1, None), seed=None, method='random',
        batch_sizes=(32, 32, 32), idx_override=None
    ):
        """Utilizes the DGL library or related code (samplers) to split the
        self.ml_ready attribute into test, validation and training splits.

        Parameters
        ----------
        p_tvt : tuple, optional
            A length 3 tuple containing the proportion of testing, validation
            and training data desired. If the sum of the elements in the tuple
            sums to less than one, then we downsample the training set
            accordingly.
        seed : int, optional
            Used to seed the sampler RNG. Ensures reproducibility.
        method : {'random'}
            The method of choice for sampling the splits.
        batch_sizes : tuple
            The batch sizes for the testing, validation and training loaders.
        idx_override : dict
            A dict of lists, each lists corresponding to the test, validation
            and training splits. This will override the sampler and select the
            splits directly from the user-supplied indexes.

        Returns
        -------
        dict, optional
            Dictionary of loaders of type torch.utils.data.DataLoader. Returns
            None in the event of an error.
        """

        if self.ml_data is None:
            error = "Run ml_ready before calling this method - doing nothing"
            dlog.error(error)
            return None

        if seed is None:
            dlog.warning(
                "Not seeding the RNG: this result will not be reproducible"
            )

        # Execute the sampling method of choice to produce the T/V/T splits
        # dictionary.
        if idx_override is None:
            if method == 'random':
                tvt_dict = self._execute_random_points_sampling(*p_tvt, seed)
            else:
                critical = f'Method {method} not implemented'
                dlog.critical(critical)
                raise RuntimeError(critical)
        else:
            dlog.info("Overriding sampler with user-loaded split indexes")
            if p_tvt is not None:
                dlog.warning("p_tvt is specified and will be ignored")

            test_idx = idx_override['test']
            valid_idx = idx_override['valid']
            train_idx = idx_override['train']
            assert set(test_idx).isdisjoint(valid_idx)
            assert set(test_idx).isdisjoint(train_idx)
            assert set(train_idx).isdisjoint(valid_idx)
            dlog.info("Assertions passed - all splits are unique")
            tvt_dict = idx_override

        # Initialize the subset objects
        testSubset = torch.utils.data.Subset(self, tvt_dict['test'])
        validSubset = torch.utils.data.Subset(self, tvt_dict['valid'])
        trainSubset = torch.utils.data.Subset(self, tvt_dict['train'])

        # Initialize the loader objects
        testLoader = torch.utils.data.DataLoader(
            testSubset, batch_size=batch_sizes[0], shuffle=False,
            collate_fn=QMXDataset.collating_function_graph_to_vector
        )
        validLoader = torch.utils.data.DataLoader(
            validSubset, batch_size=batch_sizes[1], shuffle=False,
            collate_fn=QMXDataset.collating_function_graph_to_vector
        )
        trainLoader = torch.utils.data.DataLoader(
            trainSubset, batch_size=batch_sizes[2], shuffle=True,
            collate_fn=QMXDataset.collating_function_graph_to_vector
        )

        return {
            'test': testLoader,
            'valid': validLoader,
            'train': trainLoader
        }

    def write_file(self, filename: str = 'QMdb', fmt: str = 'pickle'):
        """Write dataset into serialized form for later access."""

        if fmt in ['pickle', 'pckl', 'pkl', 'binary']:
            if len(filename.split('.')) == 1:
                filename = f'{filename}.pkl'
            pickle.dump(
                self, open(filename, "wb"), protocol=pickle.HIGHEST_PROTOCOL
            )

        else:
            critical = f"Your specified format {fmt} is not supported."
            dlog.critical(critical)
            raise ValueError(critical)

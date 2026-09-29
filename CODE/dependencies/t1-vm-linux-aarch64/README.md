# T1 Linux aarch64 environment definition

These files capture the observed T1 runtime and are inputs to isolated reconstruction.
They do not claim bit-for-bit wheel reconstruction: the PyPI lock pins versions but
does not yet contain wheel hashes. Release receipts hash the lock files themselves.

## Captured runtime

- OS: Linux aarch64; kernel `6.6.0-28.0.0.34.oe2403.aarch64`; glibc 2.39.
- Python: CPython 3.11.15, conda-forge `linux-aarch64`.
- Conda: 25.3.0. `conda-linux-aarch64.explicit.lock` records the explicit Conda package URLs.
- `requirements.lock` records all 88 installed PyPI distributions and exact versions from
  `conda list --json`; it is the full resolved Python distribution snapshot, not a claim
  that all 88 packages are direct project dependencies.
- Required core imports found in the source: NumPy 1.24.3, SimPy 4.0.1, PyYAML 6.0.2
  (`requirements-direct.in`). Tests additionally use pytest 9.1.1
  (`requirements-test.in`). Optional paths also import TensorFlow, Pillow, pandas, and
  Matplotlib; their present versions are pinned by the full runtime lock.
- NVIDIA A100 PCIe 40 GB and driver 570.133.20 were observed. CUDA toolkit/compiler
  availability was not established; do not treat the driver's CUDA compatibility field
  as proof of a local toolkit.

## Isolated reconstruction

On a Linux aarch64 host with conda and network access to the recorded conda-forge
artifacts and PyPI, create a new prefix (never reuse an active environment):

```sh
conda create --yes --prefix /path/to/new-prefix --file conda-linux-aarch64.explicit.lock
/path/to/new-prefix/bin/python -m pip install --no-deps --requirement requirements.lock
/path/to/new-prefix/bin/python -m pip check
/path/to/new-prefix/bin/python -c 'import numpy, simpy, yaml; print(numpy.__version__, simpy.__version__, yaml.__version__)'
/path/to/new-prefix/bin/python -m pytest CODE/scripts/remote/tests CODE/tests/test_maintenance_check.py -q
```

The explicit Conda package URLs are platform-specific and may become unavailable; a
future hardened lock should add verified artifact hashes and an approved artifact mirror.
The command above is a recipe, not proof of successful reconstruction. Record the isolated
prefix's package inventory and test output before marking reconstruction verified.

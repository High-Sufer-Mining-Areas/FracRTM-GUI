# FracRTM-GUI

## Description

FracRTM-GUI is a graphical modelling environment for flow and reactive transport in fractured porous media. Within a single project, it connects discrete fracture network (DFN) generation in dfnWorks, continuum upscaling with the upscaled discrete fracture matrix (UDFM) framework, and flow and reactive transport simulation in PFLOTRAN.

The desktop application uses Windows and WSL for model preparation and native execution. The interface is available in English and Chinese, with English as the default.

## Features

* **DFN generation:** Configure fracture families, explicitly defined fractures and connectivity filters, then generate fracture networks and meshes with dfnWorks.
* **Continuum upscaling:** Generate locally refined octree grids and obtain equivalent permeability and porosity fields using UDFM.
* **Spatial model configuration:** Organize meshes, datasets, materials and regions, and associate flow and transport conditions with the appropriate regions.
* **Reactive transport setup:** Configure aqueous species, mineral reactions and chemical constraints, check model consistency, and generate PFLOTRAN input files.
* **Simulation management:** Configure WSL and MPI execution, launch and stop jobs, monitor logs, and retain run records and input fingerprints.
* **Results visualization:** Display fracture networks, continuum meshes, material properties and time-dependent simulation results in the integrated viewer.

## Installation

Use Python 3.10–3.13 on Windows. The commands below install the desktop application and its Python dependencies, listed in [pyproject.toml](src/pyproject.toml). To run the full modelling workflow, install pydfnWorks, DFNGen, LaGriT, PFLOTRAN and a compatible MPI implementation separately in WSL/Ubuntu.

From the repository root, run the following commands in PowerShell:

```powershell
python -m venv .venv
& "./.venv/Scripts/python.exe" -m pip install ./src
& "./.venv/Scripts/python.exe" -m fracrtm
```

With the environment activated, either `python -m fracrtm` or `fracrtm-gui` starts the application. For an editable installation, use `python -m pip install -e ./src`.

In **Runtime Environment**, configure the actual WSL distribution, user, Python interpreter, DFN runtime HOME, PFLOTRAN executable, MPI launcher and a writable WSL workspace. Native scientific tools are installed separately; the MPI implementation must be compatible with the PFLOTRAN build.

## Documentation

The [user guide](docs/user-guide.md) explains how to create or reopen a project, generate a fracture network and UDFM grid, configure PFLOTRAN, run a simulation and view its results. An illustrated example follows acidic-water transport in the B1 fracture-network scenario.

**Configuration notes:** A specified boundary connection area is a uniform input to the converter; it does not reconstruct the individual geometric areas of boundary control volumes. Historical rectangle configurations with undefined angle conventions require an explicit geometry definition before import.

## Example projects

Two example projects include model inputs and precomputed results:

* [M-H2](example/M-H2/): Steady-state groundwater flow benchmark.
* [B1](example/B1/): Acidic-water reactive transport in calcite-bearing fractured media.

The B1 result file is stored with Git Large File Storage (Git LFS). Install Git LFS, then run `git lfs pull` from the repository root after cloning. In the GUI, choose **Project Overview → Switch Project** and select the example folder containing `project.json`.

## Major Libraries

|Library|Role|
|-|-|
|PyQt5|Desktop interface and application events|
|NumPy|Numerical arrays and scientific data operations|
|SciPy|Spatial indexing and coordinate matching|
|h5py|HDF5 dataset and simulation-result access|
|VTK|Three-dimensional data processing and rendering|
|PyVista|Mesh handling and scientific visualization|
|PyVistaQt|Integration of the visualization viewer with Qt|
|pydfnWorks|Fracture-generation and continuum-upscaling workflows|

The native workflow also uses DFNGen, LaGriT, PFLOTRAN and MPI.

## Acknowledgements

We thank the developers and contributors of [dfnWorks](https://lanl.github.io/dfnWorks/), [LaGriT](https://lanl.github.io/LaGriT/), [PFLOTRAN](https://www.pflotran.org/) and the libraries listed above for the software and documentation on which this project builds.

## License

FracRTM-GUI is distributed under the GNU General Public License v3.0.

See [LICENSE](LICENSE) for the complete license text. Third-party tools and dependencies retain their own licenses.

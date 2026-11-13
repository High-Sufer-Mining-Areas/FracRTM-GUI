# FracRTM-GUI

## Description

FracRTM-GUI is a graphical modelling environment for flow and reactive transport in fractured porous media. It connects dfnWorks fracture-network generation, UDFM continuum upscaling, and PFLOTRAN simulations within a single project workflow.

The desktop application uses Windows and WSL for model preparation and native execution. The interface is available in English and Chinese, with English as the default.

## Features

- **DFN generation:** Configure fracture families, explicitly defined fractures and connectivity filters, then generate fracture networks and meshes with dfnWorks.
- **Continuum upscaling:** Generate locally refined octree grids and obtain equivalent permeability and porosity fields using UDFM.
- **Spatial model configuration:** Organize meshes, datasets, materials and regions, and associate flow and transport conditions with the appropriate regions.
- **Reactive transport setup:** Configure aqueous species, mineral reactions and chemical constraints, check model consistency, and generate PFLOTRAN input files.
- **Simulation management:** Configure WSL and MPI execution, launch and stop jobs, monitor logs, and retain input snapshots and run records.
- **Results visualization:** Display fracture networks, continuum meshes, material properties and time-dependent simulation results in the integrated viewer.

## Installation

Use a compatible desktop Python environment, a configured WSL distribution, and existing installations of pydfnWorks, DFNGen, LaGriT, PFLOTRAN and a matching MPI implementation. Python dependency requirements are defined in `src/pyproject.toml`.

From the repository root, run the following commands in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ./src
.\.venv\Scripts\python.exe -m fracrtm
```

With the environment activated, either `python -m fracrtm` or `fracrtm-gui` starts the application. For an editable installation, use `python -m pip install -e ./src`.

In **Runtime Environment**, configure the actual WSL distribution, user, Python interpreter, DFN runtime HOME, PFLOTRAN executable, MPI launcher and a writable WSL workspace. Native scientific tools are installed separately; the MPI implementation must be compatible with the PFLOTRAN build.

Create or open a project, generate its DFN and UDFM resources in **Spatial Model**, configure the simulation in **PFLOTRAN Model**, then run it and inspect the results. Keep the project together with its referenced scientific resources.

**Configuration notes:** A specified boundary connection area is a uniform input to the converter; it does not reconstruct the individual geometric areas of boundary control volumes. Historical rectangle configurations with undefined angle conventions require an explicit geometry definition before import.

## Major Libraries

| Library | Role |
|---|---|
| PyQt5 | Desktop interface and application events |
| NumPy | Numerical arrays and scientific data operations |
| SciPy | Spatial indexing and coordinate matching |
| h5py | HDF5 dataset and simulation-result access |
| VTK | Three-dimensional data processing and rendering |
| PyVista | Mesh handling and scientific visualization |
| PyVistaQt | Integration of the visualization viewer with Qt |
| pydfnWorks | Fracture-generation and continuum-upscaling workflows |

The native workflow also uses DFNGen, LaGriT, PFLOTRAN and MPI.

## Acknowledgements

We thank the developers and contributors of [dfnWorks](https://lanl.github.io/dfnWorks/), [LaGriT](https://lanl.github.io/LaGriT/), [PFLOTRAN](https://www.pflotran.org/) and the libraries listed above for the software and documentation on which this project builds.

## License

License: GNU General Public License v3.0.

See [LICENSE](LICENSE) for the complete license text. Third-party tools and dependencies retain their own licenses.

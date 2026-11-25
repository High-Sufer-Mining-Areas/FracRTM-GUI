# FracRTM-GUI user guide

This guide covers project preparation, DFN generation, UDFM configuration, PFLOTRAN model construction, execution and result inspection. The worked example is **B1**, a fractured-medium flow and reactive-transport project. It includes a completed calculation, so you can first learn the viewer without running a solver.

The instructions and screenshots were checked against the maintained source and the B1 example in **November 2026**. Screenshots are original application captures; project paths and execution times are specific to the demonstration machine. English interface labels are used below. Chinese is also available under **Runtime Environment → Language**.

## Contents

1. [Installation and first launch](#1-installation-and-first-launch)
2. [B1 tutorial: inspect the supplied results](#2-b1-tutorial-inspect-the-supplied-results)
3. [Prepare or change a spatial model](#3-prepare-or-change-a-spatial-model)
4. [Configure a PFLOTRAN model](#4-configure-a-pflotran-model)
5. [B1 tutorial: run a working copy](#5-b1-tutorial-run-a-working-copy)
6. [Understand project and run status](#6-understand-project-and-run-status)
7. [Save, reopen and transfer a project](#7-save-reopen-and-transfer-a-project)
8. [Troubleshooting](#8-troubleshooting)

## 1\. Installation and first launch

### Viewing versus computing

|Activity|Required environment|
|-|-|
|Open configurations and inspect a complete precomputed B1 project|Desktop Python application and its declared dependencies, including Qt/VTK; the example's geometry, raw outputs and visualization files|
|Generate DFN or UDFM resources|Desktop application plus a working WSL environment with pydfnWorks, DFNGen and LaGriT|
|Run PFLOTRAN|Desktop application plus WSL, PFLOTRAN, a compatible MPI installation and all input resources|

Installing the GUI does not install the native scientific programs. Viewing an intact precomputed example does not require rerunning them. First loading or rebuilding visualization data may still take time and use substantial memory.

Follow the dependency declarations and [installation instructions](../README.md#installation). From the repository root, a typical PowerShell installation is:

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install ./src
./.venv/Scripts/python.exe -m fracrtm
```

With that environment activated, `python -m fracrtm` or `fracrtm-gui` starts the installed application. Use the interpreter into which you installed this source; an older installation can otherwise open a different version.

### Projects and navigation

In **Project Overview**, use **Switch Project** to select a project directory. Select the directory containing `project.json`, not its `pflotran`, `octree` or `runs` subdirectory. For a new project, select a new, empty, writable directory. Confirm the displayed project name and location before generating files.

|Page|Main purpose|
|-|-|
|Project Overview|Project location, workflow readiness and the most recent run|
|Spatial Model|DFN definitions and UDFM parameters/generation|
|PFLOTRAN Model|Model editor, validation and input generation|
|Simulation Run|MPI process count, task execution and logs|
|Results \& Visualization|Fracture structure, spatial properties and selected-run results|
|Runtime Environment|Language and machine-specific execution settings|

### Configure execution on your machine

Before generating or running, enter the actual **WSL distribution**, **WSL user**, **WSL workspace**, **WSL Python**, **dfnWorks HOME**, **PFLOTRAN executable** and **MPI launcher** under **Runtime Environment**. Choose suitable DFN and PFLOTRAN process counts, then use **Check Environment** and **Save Settings**. Environment detection is not proof that a scientific model will converge.

These fields refer to the execution environment: use Linux paths for WSL programs and workspace. Do not paste a Windows `D:\\...` path into a WSL executable field. Project resource references such as `octree/full\_mesh.uge` are relative to the project root, not the directory from which Python was started.

Changing language changes presentation, not scientific values or resource identities. Use a compatible PFLOTRAN/MPI pair. Keep the native tools and their versions consistent when reproducing a calculation.

## 2\. B1 tutorial: inspect the supplied results

### 2.1 Obtain and open the complete example

The [B1 example project](../example/B1/) contains the model inputs and precomputed reactive transport results. Its main PFLOTRAN output file is stored with Git LFS. Install Git LFS and run `git lfs pull` from the repository root after cloning to download the full result file. The [M-H2 example](../example/M-H2/) provides a separate steady-state flow benchmark.

1. Locate the `example/B1/` folder in your local copy of the repository. If you move it to another writable directory, copy the entire folder.
2. Start the GUI and select **Project Overview → Switch Project**.
3. Select the `B1` directory containing `project.json`.
4. Check **Project Location**. Do not click Generate or Start Simulation just to view supplied results.
5. Inspect **Most Recent Simulation** and, if needed, expand **Details**.

!\[B1 project overview with its actual completed run](images/b1-overview.jpg)

*Figure 1. Project Overview for the B1 demonstration run. Run duration is elapsed computing time; simulation time is the physical time reached.*

The screenshots show a separate run of the B1 example; its run ID and elapsed time differ from those in the distributed project. The distributed run contains 41 saved frames and reaches 630720000 s (20 years under the solver convention used here).

The example contains a 15 × 15 × 15 m domain, an initial generation target of 30 fractures and **11 retained fractures** after connectivity filtering. Its UGE mesh contains **32215 control volumes**. The generation target and retained count are different quantities.

### 2.2 Inspect the fracture network

1. Open **Results \& Visualization → Spatial Structure**.
2. Select **Fracture Network** in **Data**.
3. Use **Representation**, **Opacity** and **Reset View** to inspect the network. Rotate the view by dragging in the viewport and zoom with the mouse wheel.

!\[B1 fracture geometry in the actual viewer](images/b1-fractures.jpg)

*Figure 2. B1's retained fracture polygons. This is structural geometry, not a PFLOTRAN concentration or pressure result.*

### 2.3 Inspect the continuum resources

Open **Spatial Model** to review the UDFM settings without changing them. B1 uses a level-0 cell size of **1 m**, **2** refinement levels, a displayed minimum size of **0.25 m**, matrix permeability **1e-18 m²** and matrix porosity **0.01**. Boundary connection area uses **Use tool default** in this example.

!\[B1 spatial parameters and UDFM configuration controls](images/b1-spatial-model.jpg)

*Figure 3. The existing B1 settings. Loading or saving a configuration is separate from generating its products.*

Next open **Results \& Visualization → Mesh Properties**. Select the computational mesh or an available material, permeability or porosity dataset in **Data**. **Surface + Edges** reveals the display topology; use **Surface** when edges obscure a scalar field.

!\[B1 display mesh with visible edges](images/b1-display-mesh.jpg)

*Figure 4. The displayed topology is not the boundary geometry of PFLOTRAN control volumes. In particular, the visible triangles must not be interpreted as octree control-volume faces.*

For this example, `octree/full\_mesh.uge` defines computational control volumes and connections. `octree/octree\_dfn.inp` supplies display geometry whose points are associated with UGE control-volume locations through the application's mapping checks. The display has 32215 points and 182576 cells; the latter is **not** the computational control-volume count. Mapped scalar values are attached to display points. Surface colouring between points is a rendering representation, not an additional solver calculation.

### 2.4 Inspect the reactive-transport results

1. Select **Simulation Results**.
2. Select the completed B1 record in **Run record**. This chooses that run's verified display data and original outputs.
3. Set **Data** to **PFLOTRAN Time Series**.
4. Select **pH** under **Property**. The first available property may be an internal identifier such as `imt1`; explicitly select the physical variable you want.
5. Select **6.3072e+08 s** under **Time** for the final snapshot. The initial `0 s` frame is also a valid output, but it has not yet undergone transport.
6. Use **Surface**, leave **Use Log Scale** off for pH, and enable **Legend**.

!\[B1 final-time pH displayed from the completed run](images/b1-final-ph.jpg)

*Figure 5. The final pH field in the B1 demonstration run. Time, property and run selection jointly determine the displayed result.*

The supplied series includes pressure, aqueous totals, calcite quantities and velocity components. Names and units come from the available outputs; do not assume that selecting `ALL` in a solver input produces every possible secondary species. Inspect the property list and the run's output requirements.

**Regenerate selected visualization** rebuilds a display product from available source data; it does not rerun PFLOTRAN. Use it for a missing or invalid display cache, not as a way to repair a failed scientific calculation. Keep the raw output and recorded visualization files. Regeneration requires the current input and mesh to match the recorded input fingerprints. A blank viewport with an error message requires diagnosis; a successfully loaded view does not need a persistent grey status sentence.

## 3\. Prepare or change a spatial model

Work in a copy to preserve the supplied B1 example. Earlier run records and outputs are retained, but editing the current configuration can change which results are marked as current.

### DFN configuration

In **Spatial Model → Fracture Network → Configuration**, load or edit the network definition. Check domain dimensions, generation/stop controls, fracture-family probabilities, size distributions, orientation convention, random seed and connectivity settings. Explicitly defined fractures are configured separately from stochastic families. Save the configuration, then use **Generate** when a new network is required.

Connectivity filtering can reduce the number of retained fractures below the generation target. A lack of a spanning network is a scientific outcome to investigate, not a reason to change the seed silently. Historical rectangle files without a defined orientation convention need an explicit, supported geometry definition; do not guess a normal merely to import them.

### UDFM configuration

|Field|Meaning and use|
|-|-|
|Level-0 Cell Size|Base spatial scale in metres|
|Number of Octree Refinement Levels|Local refinement depth; increasing it can substantially increase memory and runtime|
|Minimum Cell Size|Derived display of base size divided by 2 to the refinement level|
|Boundary Connection Area (m²)|Converter parameter source: tool default or a specified uniform positive area|
|Matrix Permeability (m²)|Background matrix permeability used by upscaling|
|Matrix Porosity|Dimensionless background matrix porosity|

In **Use tool default**, no custom area is passed to the boundary converter. In **Specify uniform value**, enter a finite positive number; scientific notation is supported. That value is used uniformly for generated boundary connections. It is **not** an automatic calculation of each control volume's geometric boundary area. A default observed in one installation is not a universal value or a scientific recommendation.

Use **Load Configuration** to import UDFM settings and **Save Configuration** to export them. These buttons do not package meshes or run results. Editing a valid setting can invalidate dependent products; **Generate UDFM** performs the required generation work. An area change must regenerate boundary resources rather than merely relabel existing `.ex` files. Do not edit these files to make their contents appear consistent with a new setting.

After generation, inspect the mesh and properties. Numerical values are control-volume properties; they are not bulk effective properties inferred from boundary flow measurements.

## 4\. Configure a PFLOTRAN model

Open **PFLOTRAN Model → Model Configuration**. The editor provides the following sequence:

!\[B1 model configuration editor and its navigation](images/b1-model-editor.jpg)

*Figure 6. The model editor. Cancel leaves the project configuration unchanged; Save Configuration applies the edited model.*

|Editor section|What to check|
|-|-|
|Simulation|Required flow/transport processes and steady or transient flow mode|
|Mesh \& Data|Automatically discovered computational mesh, material identifiers and property datasets|
|Materials|Material IDs, hydraulic properties, referenced datasets and applicable constitutive settings|
|Chemistry|Thermodynamic database, aqueous species, minerals and supported kinetic/constraint definitions|
|Regions|Named spatial regions, their definition type and any referenced region file|
|Conditions|Initial/inflow flow and transport conditions, values and units|
|Assignments|References linking conditions and materials to regions|
|Time \& Solvers|Final time, timestep controls and supported solver settings|
|Output \& Review|Requested output quantities, file formats, schedules and model review|

### Spatial references and materials

In the current UDFM workflow, the mesh and material file fields are **read-only discovered paths**. Use **Rescan** after making the required resources available; this is not a general mesh-file chooser.

!\[B1 automatically discovered computational grid and material data](images/b1-mesh-data.jpg)

*Figure 7. B1 Mesh \& Data: 32215 UGE control volumes, 119550 connections and two material identifiers. The property table may require scrolling.*

Keep the roles distinct:

|Resource|Typical project reference|Role|
|-|-|-|
|Computational grid|`octree/full\_mesh.uge`|PFLOTRAN control-volume locations, volumes and connections|
|Display geometry|`octree/octree\_dfn.inp`|Geometry for spatial/result display, with checked mapping|
|Material identifiers|`octree/materials.h5`|Cell IDs and material assignments, used by STRATA|
|Permeability|`octree/mesh\_permeability.h5`|Referenced permeability dataset|
|Porosity|`octree/mesh\_porosity.h5`|Referenced porosity dataset|
|Boundary connections|UDFM-generated `.ex` files in `octree/`|External boundary regions referenced by the model|
|Thermodynamic database|`database/hanford.dat` in B1|Database used by the chemistry definition|

These are the supplied example's paths, not instructions to rename arbitrary resources. Dataset names inside HDF5 and the names referenced by MATERIAL\_PROPERTY must agree. A material identifier is not a control-volume identifier. Do not swap HDF5 IDs based solely on a material's display name.

UDFM boundary files describe generated exterior boundary connections. Other REGION definitions may describe coordinate ranges or control-volume selections. A named region alone does not prescribe an initial, boundary or source/sink condition: the corresponding assignment must also exist.

### Values, units and chemistry

Enter values with the units shown by their fields. Check the selected orientation convention, pressure/head reference, gravity and time units; do not silently substitute one physical quantity for another. Porosity is dimensionless, permeability is in m², and the UDFM boundary area is in m².

For reactive transport, keep the database file with the project and preserve exact species/mineral names, including charge notation. Database parsing and reference checks do not establish that a reaction network, initial solution or boundary condition is scientifically valid. Choose only the processes and constitutive relationships supported by the editor and target backend.

### Save, validate and generate

1. Use **Validate Model** in the editor to inspect issues. Resolve blocking errors at the named object/field; review warnings separately.
2. Use **Save Configuration** to apply changes, or **Cancel** to discard them. **Export Configuration** saves a configuration file, not a complete portable project.
3. On the main PFLOTRAN page, use **Validate Model**, then **Generate Input File**.
4. Confirm that the generated `model.in` is current before running.

!\[PFLOTRAN model summary and input-generation controls](images/b1-pflotran-model.jpg)

*Figure 8. The main model page separates configuration checking from input generation. A current input file does not prove that a new simulation has finished.*

Required fields, constrained values, object references and supported spatial-resource checks can block generation. A successful check is not validation of the underlying physical model. Do not use a hand-edited `model.in` as a substitute for updating the GUI configuration: subsequent generation writes from the configuration, and input identity checks may detect the difference.

**B1 time warning:** in this version, the configuration-only check can report that the target solver's calendar-unit convention is unconfirmed, even when a particular frozen run has separate confirmed time evidence. This was observed during the tutorial:

!\[Actual calendar-unit warning from configuration checking](images/b1-validation-warning.jpg)

Keep the warning and the selected run's completion assessment distinct. Do not clear it by editing state records or by assuming 365/365.25 days. On another installation, inspect that run's recorded environment and completion evidence before claiming it reached the requested calendar time.

## 5\. B1 tutorial: run a working copy

This section explains how to rerun B1. The screenshots illustrate the controls using the completed demonstration run.

### 5.1 Prepare the copy and environment

1. Close any active edit dialog and ensure no task is running in B1.
2. Copy the **entire B1 directory**, including its run records and hidden metadata, to a separate writable working directory such as `B1\_work`.
3. Open that directory through **Switch Project**. Verify **Project Location**; the display name may remain B1.
4. Configure and check your actual WSL environment. The demonstration used 4 processes for DFN/UDFM generation and **1 MPI process** for PFLOTRAN. These are recorded settings, not minimum hardware requirements.
5. Review workflow status. Reuse spatial resources that are valid. If resources are stale, missing or lack verifiable generation records, resolve that cause and regenerate the affected stages in dependency order. Copying a project does not justify forcing all states to valid.

### 5.2 Generate the input without changing the model

1. Open **PFLOTRAN Model → Model Configuration** and inspect the loaded B1 definitions. Cancel if you made no edits.
2. Run **Validate Model** and review all issues. Resolve blocking issues without altering the intended scientific case.
3. Use **Generate Input File**. If an overwrite confirmation appears, confirm only for the current input in this working copy.
4. Keep B1's processes, reaction definitions, solver settings and output schedule unchanged. In particular, do not shorten its 20-year target to make a reproduction finish sooner.

### 5.3 Start and monitor

1. Open **Simulation Run** and check **MPI Processes** (1 for the reference run).
2. Select **Start Simulation** once. Do not start a second task while the first is active.
3. Follow the task status and **View Log**. The application prepares frozen inputs/resources, invokes the backend and returns results through its normal task workflow.
4. If you need to stop, use the application's stop control and wait for the task's terminal state. A stopped task is not a completed calculation. Preserve its diagnostics.
5. If synchronization fails, investigate the reported path/environment problem and use **Retry synchronization** where appropriate. After an interruption, use **Check interrupted task** before launching a duplicate calculation.

!\[B1 run page showing its retained execution information](images/b1-simulation-run.jpg)

*Figure 9. Simulation Run for the B1 demonstration. Use Project Overview to inspect the selected run’s completion status, duration and final simulation time.*

### 5.4 Check the outcome

After the task ends, check **Project Overview → Most Recent Simulation**. For a successful unchanged run, expect **Completed · Up to date**, an actual duration and an actual reached simulation time. Open **Details**, **View Log** and **Open Results** to inspect the same run.

Then repeat section 2.4, explicitly selecting the **new** run. Check that the final time and requested fields exist. The supplied reference has 41 frames from 0 to 630720000 s at 15768000 s intervals. A new run ID and a different wall time are normal. Different numerical fields or missing frames require investigation, not cosmetic status changes.

Close and reopen the project, and confirm that the same run and outputs can still be selected. Process exit code 0, a final log message or an openable result file alone is not enough to establish complete scientific output. Completion assessment, synchronization and visualization are separate outcomes.

## 6\. Understand project and run status

|Display or condition|Interpretation and action|
|-|-|
|Ready / Valid|The relevant workflow product is available and passes its current identity/dependency checks; not a blanket claim of scientific correctness|
|Running|An active task; wait or stop through the application|
|Completed · Up to date|The run completed and remains associated with the current input/configuration|
|Input changed / Stale|Retained products or results do not represent the current dependencies; regenerate/recompute the affected stages|
|Failed|Execution or another recorded task stage failed; read the specific diagnostics|
|Unverified / completion unverified|Evidence is insufficient for a full completion claim; do not treat it as completed merely because files exist|
|Not configured / missing|Required settings or products are absent|
|—|Information was not recorded or cannot be reliably obtained; it is not zero|

DFN changes can invalidate the continuum and downstream input/results. UDFM grid or boundary changes affect downstream spatial products. Changing only a hydraulic or chemical condition generally retains unchanged spatial resources but invalidates PFLOTRAN input and its relationship to old results. Generating new input does not make an old result current.

Historical outputs may remain viewable through **Run record**, using their archived display geometry, arrays and times. Always report which run is being viewed. **Run duration** is elapsed task time; **Simulation time** is the physical time reached; a completion timestamp in **Details** is a calendar date/time. Unknown physical time should remain unknown rather than being inferred from snapshot number or file modification time.

## 7\. Save, reopen and transfer a project

The main project layout is:

```text
B1/
  project.json
  dfn/dfn\_config.json              # DFN configuration and generated resources
  octree/continuum\_config.json     # UDFM configuration, grid, attributes, boundaries
  pflotran/model\_config.json       # Current scientific simulation configuration
  pflotran/model.in                # Generated current solver input
  database/hanford.dat
  dfn/generation/<run\_id>/         # DFN record and stage logs
  octree/generation/octree/<id>/   # Octree record and stage logs
  octree/generation/upscale/<id>/  # Upscale record and stage logs
  results/run-001/result.json      # PFLOTRAN identity, input hashes, output manifest
  results/run-001/output/          # Original solver outputs and task logs
  results/run-001/visualization/   # Recorded display geometry, arrays and time
  .fracrtm/                       # Temporary active/failed tasks and recovery only
```

Exported JSON files contain settings and references, not the contents of all referenced resources. To preserve a viewable example, retain the complete project: configurations, generation records, database, required DFN/UDFM files, the selected run's metadata, raw output, logs and visualization files. Completed tasks retain input fingerprints, not permanent input/source copies. In this version, cached visualization validation also uses source identities; retaining only PVD/VTU files is insufficient.

Use the GUI's configuration save/load controls for settings. Allow tasks to finish before copying a project. Keep relative subdirectories intact and check any externally referenced absolute paths after transfer. On another machine, update **Runtime Environment** rather than replacing scientific parameters with machine defaults. Software 1.0 supports only project format 1.0 and this stage-specific layout. Older project formats are rejected, with no automatic migration. Missing generation/run records cannot be reconstructed from file existence.

The [example projects](../example/) illustrate this layout. To share a project with viewable results, keep its run records, solver outputs and visualization files together.

## 8\. Troubleshooting

|Problem|What to do|
|-|-|
|The project opens with an empty configuration or no mesh.|In **Project Overview → Switch Project**, select the folder containing `project.json`. If the mesh is still missing, confirm that `octree/full\_mesh.uge` is present, then select **Rescan** in **Mesh \& Data**.|
|The PFLOTRAN input file cannot be generated.|Select **Validate Model** on the **PFLOTRAN Model** page. Resolve the reported errors, such as missing resources or references to undefined regions or conditions, then select **Generate Input File**.|
|The simulation does not start or stops with an error.|If it does not start, check the WSL, PFLOTRAN and MPI settings in **Runtime Environment**. If it starts but stops with an error, select **View Log** and inspect the solver message.|
|No precomputed B1 results appear.|Run `git lfs pull` from the cloned repository root to retrieve the full result file. Open the B1 project, go to **Results \& Visualization → Simulation Results**, select the completed run and **PFLOTRAN Time Series**, then choose a variable (e.g. pH) and a saved time.|

[Screenshot details and checksums](images/README.md).


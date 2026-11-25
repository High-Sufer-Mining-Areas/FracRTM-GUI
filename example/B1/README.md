# B1

This example contains one complete generation workflow and one PFLOTRAN simulation.

* dfn/: all DFN scientific products and surviving intermediate files.
* dfn/generation/<run\_id>/: DFN generation record and logs.
* octree/: all mesh, boundary and equivalent-property products and intermediate files.
* octree/generation/octree/<run\_id>/: octree generation record and logs.
* octree/generation/upscale/<run\_id>/: equivalent-property generation record and logs.
* pflotran/: current JSON definition and generated model.in.
* results/run-001/: the single PFLOTRAN record, solver output and visualization.

Each generation record contains result.json and output/ logs. Open this project folder
in the updated FracRTM-GUI; all stage logs remain available through the task-log selector.


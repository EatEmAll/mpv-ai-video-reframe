# Third-party notices

Project source is GNU AGPL-3.0-only. Third-party packages retain their own
copyright and license terms. This source repository does not vendor weights,
engines, third-party binaries, mpv, or reference clones. `uv.lock` records the
full dependency set; consult the license files shipped with installed packages.

Ultralytics detection/tracking and the configured ByteTrack integration use its
AGPL-3.0 offering: https://github.com/ultralytics/ultralytics/blob/main/LICENSE
and https://www.ultralytics.com/license. Explicit model downloads during setup
retain their upstream terms. This project does not grant a different model license.

mpv is external; its license depends on the build:
https://github.com/mpv-player/mpv/blob/master/Copyright.
PyAV links FFmpeg; its build-specific LGPL/GPL notices also apply to those binaries.
Optional NVIDIA TensorRT is subject to NVIDIA's terms, not this project's AGPL:
https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/license.html.
No TensorRT binary is included here.

Installed direct runtime dependency metadata (not a substitute for upstream terms):

| Package | License metadata |
|---|---|
| torch 2.14.1+cu130 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |
| torchvision 0.29.1+cu130 | BSD |
| ultralytics 8.4.193 | AGPL-3.0 |
| av 19.0.2 | BSD-3-Clause |
| python-mpv-jsonipc 1.5.0 | Apache-2.0 |
| numpy 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| lap 0.5.13 | BSD-2-Clause |

Optional ONNX is Apache-2.0. Dev/test dependencies retain their respective licenses.
A future binary/model bundle requires an inventory of the actual redistributed
artifacts and their notice and source obligations; this private source import
does not certify such a bundle.

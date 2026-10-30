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

## Reference-project provenance

The development plan recorded the following reference revisions. They were
studied as prior art; their repositories are not installed or vendored by this
extension. This provenance is separate from the runtime dependency inventory.

| Project and studied revision | Material and influence |
|---|---|
| [idopinto/ai-video-reframing](https://github.com/idopinto/ai-video-reframing/tree/2c73d49d1930ffbd5e8a6cbb339921c31bda87dd) | `smartcrop/plan.py`: primary camera-planning reference for single-subject hold/follow behavior, easing to centre after exit, and Gaussian smoothing around one-third of a second. |
| [amys94fr/FaceFollow](https://github.com/amys94fr/FaceFollow/tree/71c4f0393bb485a570f4c38da0c92f69c8f314a1) | `main.py`: causal exponential-smoothing baseline and portrait-crop geometry. |
| [Ashyni/mpv-scripts](https://github.com/Ashyni/mpv-scripts/tree/d3f5685f5209ae548f8398b21d4dcbbea766d076) | `dynamic-crop.lua`: runtime crop-update patterns and Lua script structure. |

The reference license files identify MIT licensing and copyright holders
Ido Pinto (2026), amys94fr (2021), and Ashyni (2020), respectively. Their terms
remain with the upstream projects. These acknowledgements do not assert that
their implementations were imported wholesale. Any future inclusion of upstream
code must retain its applicable copyright and license notices.

## Runtime dependency metadata

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

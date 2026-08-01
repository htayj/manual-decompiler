# Reproducible conversion environment

The native conversion and validation toolchain runs in a locked Nix
development shell. GPU OCR inference runs through rootless Podman so the host
NVIDIA driver remains the only non-Nix runtime boundary.

## Native shell

```sh
scripts/dev-shell
uv sync --extra dev --frozen
scripts/ocr-env-doctor
```

`scripts/dev-shell` supplies the `nix-command flakes` feature flags because the
host does not enable experimental Nix features globally. After the repository
has an initial Git commit, the equivalent direct command is `nix develop`.

The shell supplies Python 3.12, `uv`, Tesseract, Poppler, MuPDF, qpdf, resvg,
HarfBuzz, FontTools, WOFF2, Potrace, Chromium, Node, jq, and Podman. Project
Python dependencies remain locked by `uv.lock`.

The shell places `tools/podman-shims/` first on `PATH`. Its `docker` executable
is a deliberately narrow compatibility shim for Surya, whose vLLM launcher
currently invokes the Docker CLI. The shim:

- invokes rootless Podman;
- removes Docker's `--runtime nvidia`;
- translates `--gpus device=N` to the NVIDIA CDI device
  `nvidia.com/gpu=N`;
- replaces Surya's mutable vLLM image tag with the platform digest in
  `containers/images.lock.json`.

It is not a general Docker emulation layer.

## Surya client

The Surya client has a separate Python 3.12 lock so its large ML dependency
graph cannot destabilize the converter:

```sh
uv sync --project tools/surya --frozen
SURYA_INFERENCE_BACKEND=vllm \
VLLM_GPU_TYPE=4090 \
DOCKER_HF_CACHE_PATH="$PWD/work/models/huggingface" \
tools/surya/run INPUT --output_dir OUTPUT
```

Surya will call the compatibility shim to start the digest-pinned vLLM
container. Model weights are downloaded into ignored `work/models/`; before an
engine may enter a benchmark report, the exact model files must be hashed and
their license disposition recorded.

Prepare the locked model and backend separately:

```sh
tools/surya/run python tools/surya/prefetch_model.py \
  > work/surya-model-evidence.json
tools/surya/pull_backend
```

The model fetch uses the exact Hugging Face commit and verifies the primary
weight's byte size and SHA-256 from `config/models/surya-ocr-2.lock.json`.
`pull_backend` refuses to begin unless 30 GiB is free, since an interrupted
multi-gigabyte image pull on a nearly full filesystem is unsafe.

The launcher scopes the Nix C++ runtime and host NVIDIA driver library path to
Surya. It does not export those impure paths to the rest of the development
shell.

## PaddleOCR

`containers/images.lock.json` records the official CUDA 12.6 Paddle base image
by registry digest. The derived runtime installs PaddleOCR 3.7.0 from the
hash-locked `containers/paddleocr/requirements.lock` without modifying the
official base image identity:

```sh
tools/paddleocr/build
tools/paddleocr/run -c \
  'import paddle; print(paddle.__version__, paddle.device.get_device())'
```

The selected English smoke pipeline is PP-OCRv5 server detection plus the
English PP-OCRv5 mobile recognizer. Downloaded model files must pass the exact
revision and file-digest lock before results are retained:

```sh
python tools/paddleocr/verify_models.py \
  config/models/paddleocr-ppocrv5-en.lock.json \
  work/models/paddleocr/official_models

tools/paddleocr/run /work/tools/paddleocr/ocr_page.py \
  /work/work/path/to/page.png \
  /work/work/paddleocr-output/page.json
```

The repository is mounted read-only in this container; only ignored `work/`
and the ignored model cache are writable. PaddleOCR remains a benchmark
candidate, not an endorsed engine. A successful runtime smoke and the models'
reported confidence are not substitutes for comparison against manually
transcribed ground truth.

On 2026-08-01 the pinned runtime was exercised on K Machine page 68. It emitted
38 text lines with polygons and word boxes. The retained canonical JSON has
SHA-256 `6ed358097c1b7796fc8c5a510731547c16507b797c383354b34d2e4bcfd37425`.
The model-verification evidence has SHA-256
`b09c89e4a5f18ce049026aea8b8453644ff2936e25b0ae96b17781d3700ff40a`.
Both files are deliberately in ignored work storage.

The official Paddle base image is unusually large: 43,135,598,771 unpacked
bytes, and the derived image is 43,649,613,816 bytes. Keep the disk guards and
do not make these images part of the basic environment check.

## Executed GPU smoke evidence

On 2026-08-01 the digest-pinned vLLM backend was pulled and Surya OCR 2 ran a
real one-page inference on the RTX 4090. Its retained result JSON is
`work/surya-smoke/p000068/results.json`, SHA-256
`660018f74ca52ef5175dec3745d43f3995f34d1fb19873d3f3a422dc018a8d1b`.
The vLLM image ID is
`aef4ebc906574fa2e6e52079937b7d3a8735e218f6353b58667b215e4257a34b`
and its unpacked size is 23,397,988,887 bytes.

The Paddle base and derived image also saw CUDA device `gpu:0`. The derived
image ID is
`8e4a92357c29996a95a4876f55a6fb08c8a902e9cc46f53afd4a37dd977efb52`.
These are execution and identity checks, not OCR quality claims.

## Evidence and updates

Run `scripts/ocr-env-doctor` inside `nix develop` to emit machine-readable
runtime evidence. Container tags are documentation only; execution uses image
digests.

Updating Nix or a container is an explicit operation:

1. update `flake.lock` or one entry in `containers/images.lock.json`;
2. record the upstream version and platform digest;
3. rerun the doctor, unit tests, and OCR benchmark;
4. do not reuse benchmark results produced under the old identity.

The NVIDIA kernel driver and CDI specification are host inputs. The doctor
fails if the `nvidia.com/gpu=all` CDI device or digest-pinned CUDA smoke image
is unavailable.

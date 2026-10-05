"""The Kubernetes manifests keep the rules in CLAUDE.md and the spec. Offline: reads the YAML files, no cluster, no kubectl."""

from pathlib import Path

import pytest
import yaml

K8S = Path(__file__).resolve().parent.parent / "deploy" / "k8s"
API_URL = "http://api.invest.svc.cluster.local:8750"


def load(name: str) -> dict:
    return yaml.safe_load((K8S / name).read_text())


def pod_spec(doc: dict) -> dict:
    spec = doc["spec"]
    return spec["template"]["spec"] if doc["kind"] == "Deployment" else spec["jobTemplate"]["spec"]["template"]["spec"]


WORKLOADS = ["web.yaml", "refresh.yaml"]
MEMORY_LIMITS = {"web.yaml": "192Mi", "refresh.yaml": "832Mi"}   # peak x 1.25 rounded up to 64 MiB (docs/acceptance-local.md)


def mebibytes(quantity: str) -> int:
    assert quantity.endswith("Mi"), quantity
    return int(quantity.removesuffix("Mi"))


def test_the_base_lists_exactly_the_files_that_exist():
    kustomization = load("kustomization.yaml")
    assert kustomization["namespace"] == "dgi"
    assert sorted(kustomization["resources"]) == sorted(p.name for p in K8S.glob("*.yaml") if p.name != "kustomization.yaml")
    assert kustomization["images"] == [{"name": "localhost/dgi", "newTag": "__IMAGE_TAG__"}]
    assert kustomization["configMapGenerator"][0]["name"] == "scoring"


def test_the_only_namespace_created_is_dgi_and_nothing_else_is_cluster_wide():
    assert load("namespace.yaml")["metadata"]["name"] == "dgi"
    for p in K8S.glob("*.yaml"):
        if p.name != "kustomization.yaml":
            assert yaml.safe_load(p.read_text())["kind"] in {"Namespace", "PersistentVolumeClaim", "Deployment", "Service", "CronJob"}


@pytest.mark.parametrize("name", WORKLOADS)
def test_images_are_local_never_pulled_and_run_as_a_locked_down_non_root_user(name):
    doc = load(name)
    pod = pod_spec(doc)
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["securityContext"]["runAsUser"] == 10001
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    for c in pod["containers"]:
        assert c["image"] == "localhost/dgi" and c["imagePullPolicy"] == "Never"
        assert c["securityContext"]["readOnlyRootFilesystem"] is True and c["securityContext"]["allowPrivilegeEscalation"] is False
        assert c["securityContext"]["capabilities"]["drop"] == ["ALL"]
        limit, request = c["resources"]["limits"]["memory"], c["resources"]["requests"]["memory"]
        assert limit == MEMORY_LIMITS[name], "memory limit comes from measurement (docs/acceptance-local.md)"
        assert 0 < mebibytes(request) < mebibytes(limit)
        env = {e["name"]: e["value"] for e in c["env"]}
        assert env["DGI_INVEST_API_URL"] == API_URL and env["DGI_DATA_DIR"] == "/data" and env["DGI_SCORING_CONFIG"] == "/config/scoring.yaml"


def test_the_web_deployment_is_one_recreated_replica_reading_the_cache_read_only():
    web = load("web.yaml")
    assert web["spec"]["replicas"] == 1 and web["spec"]["strategy"]["type"] == "Recreate"
    c = pod_spec(web)["containers"][0]
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    assert mounts["cache"]["readOnly"] is True and mounts["config"]["readOnly"] is True
    assert c["readinessProbe"]["httpGet"]["path"] == "/health" and c["livenessProbe"]["httpGet"]["path"] == "/health"
    assert c["ports"][0]["containerPort"] == 8760
    assert "hostPort" not in c["ports"][0], "the UI is reached by port-forward; no host port"


def test_the_refresh_cronjob_runs_daily_in_bucharest_time_and_never_overlaps():
    job = load("refresh.yaml")
    assert job["spec"]["schedule"] == "0 7 * * *" and job["spec"]["timeZone"] == "Europe/Bucharest"
    assert job["spec"]["concurrencyPolicy"] == "Forbid"
    template = job["spec"]["jobTemplate"]["spec"]
    assert template["backoffLimit"] == 0 and template["activeDeadlineSeconds"] > 0
    assert template["template"]["spec"]["restartPolicy"] == "Never"
    c = pod_spec(job)["containers"][0]
    assert c["args"] == ["refresh"]
    assert {m["name"]: m for m in c["volumeMounts"]}["cache"].get("readOnly") is not True   # the job writes the cache


def test_both_workloads_share_the_one_cache_volume_and_config_map():
    for name in WORKLOADS:
        volumes = {v["name"]: v for v in pod_spec(load(name))["volumes"]}
        assert volumes["cache"]["persistentVolumeClaim"]["claimName"] == "cache"
        assert volumes["config"]["configMap"]["name"] == "scoring"
        assert volumes["tmp"] == {"name": "tmp", "emptyDir": {}}


def test_the_service_is_cluster_internal_and_the_volume_is_disposable():
    service = load("service.yaml")
    assert service["spec"]["type"] == "ClusterIP" and service["spec"]["ports"][0]["port"] == 8760
    pvc = load("pvc.yaml")
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"] and "storageClassName" not in pvc["spec"]   # the cluster's default class

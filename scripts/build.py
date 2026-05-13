import argparse
import os
import re
import shutil
from collections import defaultdict
from typing import TypedDict, cast

import yaml


class App(TypedDict):
    appName: str
    dependencies: list[str]
    repoURL: str
    helmPath: str
    helmChart: str
    targetRevision: str | None
    namespace: str


def is_valid_app(app: dict) -> bool:
    required = {"repoURL", "helmPath", "helmChart", "namespace"}
    return required.issubset(app.keys())


def get_apps(input_file_path: str) -> list[App]:
    with open(input_file_path, "r") as f:
        data = yaml.safe_load(f)

    apps: list[App] = []
    for name, fields in data["apps"].items():
        if not is_valid_app(fields):
            raise ValueError(f"App '{name}' is missing required fields")
        apps.append(
            cast(
                App,
                {
                    "appName": name,
                    "dependencies": fields.get("dependencies", []),
                    "targetRevision": fields.get("targetRevision"),
                    "repoURL": fields.get("repoURL"),
                    "helmPath": fields.get("helmPath"),
                    "helmChart": fields.get("helmChart"),
                    "namespace": fields.get("namespace"),
                },
            )
        )

    return apps


def build_deployment_order(apps: list[App]) -> list[list[App]]:
    app_map = {app["appName"]: app for app in apps}
    dependents: dict[str, list[str]] = defaultdict(list)
    in_degree = {app["appName"]: 0 for app in apps}

    for app in apps:
        for dep in app["dependencies"]:
            if dep not in app_map:
                raise ValueError(
                    f"App '{app['appName']}' has unknown dependency '{dep}'"
                )
            dependents[dep].append(app["appName"])
            in_degree[app["appName"]] += 1

    current_wave = [app for app in apps if in_degree[app["appName"]] == 0]
    result: list[list[App]] = []

    while current_wave:
        result.append(current_wave)
        next_wave: set[str] = set()
        for app in current_wave:
            for dependent in dependents[app["appName"]]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_wave.add(dependent)
        current_wave = [app_map[name] for name in next_wave]

    if sum(len(wave) for wave in result) != len(apps):
        raise ValueError("Circular dependency detected among apps")

    return result


def get_version_files(env_dir: str) -> dict[str, str]:
    """
    Scan an environment directory for <app-name>-version.txt files.
    Returns a dict of {app_name: target_revision}.
    """
    pattern = re.compile(r"^(.+)-version\.txt$")
    versions: dict[str, str] = {}

    for fname in os.listdir(env_dir):
        m = pattern.match(fname)
        if not m:
            continue
        app_name = m.group(1)
        fpath = os.path.join(env_dir, fname)
        with open(fpath, "r") as f:
            revision = f.read().strip()
        if not revision:
            raise ValueError(f"Version file '{fpath}' is empty")
        versions[app_name] = revision

    return versions


def get_apps_per_environment(
    envs_dir: str, app_waves: list[list[App]]
) -> dict[str, list[list[App]]]:
    """
    For each environment, filter the deployment waves down to only apps
    that have a corresponding version.txt file. Preserves wave structure.
    """
    app_map = {app["appName"]: app for wave in app_waves for app in wave}
    result: dict[str, list[list[App]]] = {}

    for env in os.listdir(envs_dir):
        env_path = os.path.join(envs_dir, env)
        if not os.path.isdir(env_path):
            continue

        versions = get_version_files(env_path)

        unknown = set(versions.keys()) - set(app_map.keys())
        if unknown:
            raise ValueError(
                f"Environment '{env}' has version files for unknown apps: {unknown}"
            )

        # Check that all dependencies of deployed apps are also deployed
        for app_name in versions:
            app = app_map[app_name]
            missing_deps = [d for d in app["dependencies"] if d not in versions]
            if missing_deps:
                raise ValueError(
                    f"Environment '{env}': app '{app_name}' depends on "
                    f"{missing_deps} but they have no version file in this environment"
                )

        env_waves: list[list[App]] = []
        for wave in app_waves:
            matched: list[App] = []
            for app in wave:
                if app["appName"] in versions:
                    # Return a copy with targetRevision set from the version file
                    matched.append({**app, "targetRevision": versions[app["appName"]]})
            if matched:
                env_waves.append(matched)

        result[env] = env_waves

    return result


def render_application(
    template: dict, app: App, wave: int, values_path: str | None
) -> dict:
    """
    Deep-copy the template and substitute app-specific values.
    Assumes the template follows the ArgoCD Application CRD structure.
    """
    import copy

    manifest = copy.deepcopy(template)

    meta = manifest.setdefault("metadata", {})
    meta["name"] = f"{app['appName']}"
    annotations = meta.setdefault("annotations", {})
    annotations["argocd.argoproj.io/sync-wave"] = str(wave)

    spec = manifest.setdefault("spec", {})
    spec.setdefault("destination", {})["namespace"] = app["namespace"]

    sources: list[dict] = []

    # Primary source: Helm chart or git repo
    if app.get("helmChart"):
        primary: dict = {
            "repoURL": app["repoURL"],
            "chart": app["helmChart"],
            "targetRevision": app["targetRevision"],
        }
    else:
        primary = {
            "repoURL": app["repoURL"],
            "path": app["helmPath"],
            "targetRevision": app["targetRevision"],
        }

    if values_path:
        primary.setdefault("helm", {})["valueFiles"] = [f"$values/{values_path}"]

    sources.append(primary)

    # Second source: gitops repo for values (only needed if values file exists)
    if values_path:
        gitops_repo = template.get("_gitopsRepoURL", "")
        gitops_revision = template.get("_gitopsRevision", "master")
        sources.append(
            {
                "repoURL": gitops_repo,
                "targetRevision": gitops_revision,
                "ref": "values",
            }
        )

    spec["sources"] = sources

    # Strip internal template-only keys
    manifest.pop("_gitopsRepoURL", None)
    manifest.pop("_gitopsRevision", None)

    return manifest


def build_manifests(
    env_waves: dict[str, list[list[App]]],
    envs_dir: str,
    output_dir: str,
    template: dict,
) -> None:
    for env, waves in env_waves.items():
        env_out = os.path.join(output_dir, env)
        if os.path.exists(env_out):
            shutil.rmtree(env_out)
        os.makedirs(env_out, exist_ok=True)

        # Assign sync-wave numbers based on wave index
        wave_number = 0
        for wave in waves:
            for app in wave:
                values_rel = os.path.join("envs", env, f"{app['appName']}-values.yaml")
                values_abs = os.path.join(
                    envs_dir, env, f"{app['appName']}-values.yaml"
                )
                values_path = values_rel if os.path.exists(values_abs) else None

                manifest = render_application(template, app, wave_number, values_path)

                out_file = os.path.join(env_out, f"{app['appName']}.yaml")
                with open(out_file, "w") as f:
                    yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)
                print(f"  wrote {out_file}")

            wave_number += 1


def main(args: argparse.Namespace) -> None:
    try:
        apps = get_apps(args.apps_file)
    except Exception as e:
        print(f"Error getting list of all apps: {e}")
        return

    try:
        deployment_order = build_deployment_order(apps)
    except Exception as e:
        print(f"Error building deployment order: {e}")
        return

    try:
        with open(args.template, "r") as f:
            template = yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading Application template: {e}")
        return

    try:
        env_waves = get_apps_per_environment(args.input_dir, deployment_order)
    except Exception as e:
        print(f"Error resolving environments: {e}")
        return

    try:
        build_manifests(env_waves, args.input_dir, args.output_dir, template)
    except Exception as e:
        print(f"Error building manifests: {e}")
        return

    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build deployment order for apps")
    parser.add_argument(
        "-a",
        "--apps-file",
        default="apps.yaml",
        help="Path to the YAML file containing app definitions",
    )
    parser.add_argument(
        "-i",
        "--input-dir",
        default=".",
        help="Directory containing the environment subdirectories with version txt files (default: current directory)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="build",
        help="Directory to output the ArgoCD application manifests (default: build/)",
    )
    parser.add_argument(
        "-t",
        "--template",
        default="app-template.yaml",
        help="Path to the template file for ArgoCD application manifests (default: app-template.yaml)",
    )
    args = parser.parse_args()

    main(args)

# gitops

This is the single source of truth for all deployments across every environment. ArgoCD watches this repository and automatically syncs the cluster state to match what's defined here. No application is deployed to any environment unless it is explicitly defined in this repo.

---

## How it works

### Repository structure

```
gitops/
├── apps/                          # One file per application (source of truth for WHERE charts live)
│   ├── my-app.yaml
│   └── external-secrets-operator.yaml
│
├── envs/                          # Controls WHAT is deployed to each environment
│   ├── dev/
│   │   └── my-app-values.yaml
│   ├── staging/
│   │   └── my-app-values.yaml
│   └── prod/
│       ├── my-app-values.yaml
│       └── external-secrets-operator-values.yaml
│
└── bootstrap/                     # ArgoCD root ApplicationSets, one per environment
    ├── root-dev.yaml
    ├── root-staging.yaml
    └── root-prod.yaml
```

### The two-file model

Every deployed application is represented by exactly **two things**:

1. **`apps/<app-name>.yaml`** — defines the app once: where its Helm chart lives (either a git repo or a Helm registry), its target namespace, and which ArgoCD project it belongs to. This file never changes per environment.

2. **`envs/<env>/<app-name>-values.yaml`** — contains the Helm values for that app in that specific environment. **The existence of this file is what causes the app to be deployed.** If the file doesn't exist, the app is not deployed to that environment.

### How ArgoCD picks it up

Each environment has a root `ApplicationSet` in `bootstrap/`. When ArgoCD syncs, it:

1. Scans all files in `apps/` to discover known applications and their chart sources.
2. Scans all files in `envs/<env>/` to discover which apps have values configured for that environment.
3. For each app that appears in **both** places, generates an ArgoCD `Application` and deploys it.
4. Apps with no values file for a given environment are simply not deployed there — no error, no action.

This means the `envs/<env>/` directory is the deployment gate. Adding a values file deploys the app. Deleting it removes it (ArgoCD will prune it on the next sync).

### Helm values layering

ArgoCD and Helm always load values in this order:

```
1. helm/<app>/values.yaml          ← chart defaults (lives in the app repo)
2. envs/<env>/<app>-values.yaml    ← environment overrides (lives here)
```

Your environment values file only needs to contain overrides. You do not need to repeat every default value.

---

## Adding a new application

### Step 1 — Create the app definition

Create `apps/<app-name>.yaml`. Choose the correct `sourceType` based on whether this is your own application or a third-party chart.

**Your own application (hosted in GitHub):**

```yaml
appName: my-app
sourceType: git
repoURL: https://github.com/my-org/my-app.git
helmPath: helm/my-app
targetRevision: main
namespace: my-app
project: apps
```

**Third-party application (hosted in a Helm registry):**

```yaml
appName: external-secrets-operator
sourceType: helm
repoURL: https://charts.external-secrets.io
helmChart: external-secrets
targetRevision: "0.10.5"       # always pin an exact version
namespace: external-secrets-operator
project: infrastructure
```

> **Naming:** the `appName` field must exactly match the filename without `.yaml`. For example, `appName: my-app` must live in `apps/my-app.yaml`, and its values files must be named `my-app-values.yaml`.

### Step 2 — Deploy to an environment

Create a values file at `envs/<env>/<app-name>-values.yaml` for each environment you want to deploy to.

The file must exist for the app to be deployed. It can be as minimal as:

```yaml
# envs/dev/my-app-values.yaml
image:
  tag: "abc1234"
```

Or as detailed as needed:

```yaml
# envs/prod/my-app-values.yaml
image:
  tag: "abc1234"

replicaCount: 3

resources:
  requests:
    cpu: 200m
    memory: 256Mi
  limits:
    memory: 512Mi

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
```

### Step 3 — Commit and push

Once your changes are merged to `main`, ArgoCD will detect the change on its next poll cycle (default: every 3 minutes) and automatically deploy the application to the configured environments.

No manual ArgoCD commands are required.

---

## Deploying to a specific environment

| Goal | Action |
|---|---|
| Deploy app to dev | Create `envs/dev/<app-name>-values.yaml` |
| Deploy app to staging | Create `envs/staging/<app-name>-values.yaml` |
| Deploy app to prod | Create `envs/prod/<app-name>-values.yaml` |
| Remove app from an environment | Delete the values file for that environment |
| Remove app from all environments | Delete the values files and `apps/<app-name>.yaml` |
| Update configuration for one environment | Edit the values file for that environment only |
| Promote a config change from dev to staging | Copy/merge the relevant values into the staging values file |

---

## Updating an image tag

CI pipelines update the image tag automatically by modifying the `image.tag` field in the relevant environment values file and opening a pull request. For manual updates:

```yaml
# envs/prod/my-app-values.yaml
image:
  tag: "new-sha-here"   # ← update this
```

Merge the PR. ArgoCD deploys the new image automatically.

**Never use `latest` as an image tag.** Always use an immutable tag such as a git SHA.

---

## ArgoCD projects

Each app definition specifies a `project` field. Projects enforce boundaries around what an application is allowed to do in the cluster.

| Project | Used for | Can deploy to | Cluster-scoped resources |
|---|---|---|---|
| `apps` | Your own application workloads | `app-*` namespaces only | No |
| `infrastructure` | Operators and cluster-level tooling | Any namespace | Yes |

If you are adding a workload (a service your team owns), use `project: apps`.  
If you are adding a cluster operator or third-party controller, use `project: infrastructure`.

---

## Bootstrap (first-time cluster setup)

The `bootstrap/` directory contains the root `ApplicationSet` for each environment. These are applied **once** manually when setting up a new cluster. After that, ArgoCD manages everything.

```bash
# On a fresh cluster, after installing ArgoCD:
kubectl apply -f bootstrap/root-dev.yaml

# For staging/prod clusters, apply the corresponding file on that cluster:
kubectl apply -f bootstrap/root-staging.yaml
kubectl apply -f bootstrap/root-prod.yaml
```

You should never need to run `kubectl apply` again after this. All subsequent changes go through git.

---

## Conventions

- **One app, one file** in `apps/`. Never define multiple apps in the same file.
- **Pin versions.** For third-party Helm charts, always use an exact `targetRevision` version number, not a range or `latest`.
- **Filenames are load-bearing.** The `appName` in `apps/<name>.yaml` must match the prefix of `envs/<env>/<name>-values.yaml` exactly. A mismatch means the app will not be deployed.
- **Environment values files are overrides only.** Do not copy the entire chart `values.yaml` into your env file. Only include values you are intentionally changing.
- **Changes go through pull requests.** Direct pushes to `main` are discouraged. All environment changes, especially to staging and prod, should be reviewed before merge.
# gitops

This is the single source of truth for all deployments across every environment. ArgoCD watches this repository and automatically syncs the cluster state to match what's defined here. No application is deployed to any environment unless it is explicitly defined in this repo.

---

## How it works

### Repository structure

```
gitops/
├── apps/                          # One file per application workload
│   ├── my-app.yaml
│   └── infra/                     # One file per infrastructure operator/controller
│       └── external-secrets.yaml
│
├── envs/                          # Controls WHAT is deployed to each environment
│   ├── dev/
│   │   ├── my-app-values.yaml
│   │   └── infra/
│   │       └── external-secrets-values.yaml
│   ├── staging/
│   │   └── my-app-values.yaml
│   └── prod/
│       ├── my-app-values.yaml
│       └── infra/
│           └── external-secrets-values.yaml
│
└── bootstrap/                     # ArgoCD root ApplicationSets, one per environment tier
    ├── root-dev-apps.yaml         # Workload apps for dev
    ├── root-dev-infra.yaml        # Infrastructure operators for dev
    ├── root-staging.yaml
    └── root-prod.yaml
```

### App tiers

There are two tiers of applications, each managed by a separate ApplicationSet:

| Tier | Directory | ApplicationSet | Examples |
|---|---|---|---|
| **Workloads** | `apps/` + `envs/<env>/` | `root-<env>-apps.yaml` | Your services and APIs |
| **Infrastructure** | `apps/infra/` + `envs/<env>/infra/` | `root-<env>-infra.yaml` | ESO, Prometheus, cert-manager |

Infrastructure apps carry a `sync-wave: -1` annotation so that in any App-of-Apps setup they are guaranteed to sync before workloads. In practice this means CRDs (from ESO, Prometheus, etc.) are installed before workloads that depend on them.

### The two-file model

Every deployed application is represented by exactly **two things**:

1. **`apps[/infra]/<app-name>.yaml`** — defines the app once: where its Helm chart lives (either a git repo or a Helm registry), its target namespace, and which ArgoCD project it belongs to. This file never changes per environment.

2. **`envs/<env>[/infra]/<app-name>-values.yaml`** — contains the Helm values for that app in that specific environment. **The presence of the `appName` key in this file is what causes the app to be deployed.** If the key is absent, the app is not deployed. If the key is later removed, ArgoCD prunes the app on the next sync.

### How ArgoCD picks it up

Each environment has two root `ApplicationSet`s in `bootstrap/`. When ArgoCD syncs, it:

1. Scans all files in `apps/infra/` and `envs/<env>/infra/` — generates infra Applications (wave -1).
2. Scans all files in `apps/` (non-infra) and `envs/<env>/` (non-infra) — generates workload Applications (wave 0).
3. For each app that appears in **both** its app definition and its values file **with `appName` set**, generates an ArgoCD `Application` and deploys it.
4. Apps whose values file is missing or has no `appName` key are not deployed — no error, no action.

### Helm values layering

ArgoCD and Helm always load values in this order:

```
1. helm/<app>/values.yaml          ← chart defaults (lives in the app repo)
2. envs/<env>/<app>-values.yaml    ← environment overrides (lives here)
```

Your environment values file only needs to contain overrides. You do not need to repeat every default value.

---

## Adding a new workload application

### Step 1 — Create the app definition

Create `apps/<app-name>.yaml`.

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
appName: my-app
sourceType: helm
repoURL: https://charts.example.com
helmChart: my-app
targetRevision: "1.2.3"       # always pin an exact version
namespace: my-app
project: apps
```

### Step 2 — Deploy to an environment

Create `envs/<env>/<app-name>-values.yaml`. The file **must contain `appName`** for the app to be deployed:

```yaml
# envs/dev/my-app-values.yaml
appName: my-app

image:
  tag: "abc1234"
```

Add only the Helm values you want to override — everything else comes from the chart defaults.

### Step 3 — Commit and push

Once merged to `master`, ArgoCD detects the change on its next poll (default: every 3 minutes) and deploys automatically. No manual ArgoCD commands required.

---

## Adding a new infrastructure application

Infrastructure apps (operators, CRD providers, cluster-level controllers) follow the same model but live under `infra/` subdirectories.

### Step 1 — Create the app definition

Create `apps/infra/<app-name>.yaml`:

```yaml
appName: external-secrets
sourceType: helm
repoURL: https://charts.external-secrets.io
helmChart: external-secrets
targetRevision: "0.19.0"
namespace: external-secrets-operator
project: infrastructure
```

### Step 2 — Deploy to an environment

Create `envs/<env>/infra/<app-name>-values.yaml` with `appName` set:

```yaml
# envs/dev/infra/external-secrets-values.yaml
appName: external-secrets

installCRDs: true
```

### Step 3 — Commit and push

Same as workloads — merge to `master` and ArgoCD handles the rest. Because infra apps carry `sync-wave: -1`, they will always sync before workloads that depend on their CRDs.

---

## Deploying to a specific environment

| Goal | Action |
|---|---|
| Deploy workload app to dev | Create `envs/dev/<app-name>-values.yaml` with `appName` set |
| Deploy infra app to dev | Create `envs/dev/infra/<app-name>-values.yaml` with `appName` set |
| Remove app from an environment | Delete the values file, or remove the `appName` key from it |
| Remove app from all environments | Delete all values files and `apps[/infra]/<app-name>.yaml` |
| Update configuration for one environment | Edit the values file for that environment only |
| Promote a config change from dev to staging | Copy/merge the relevant values into the staging values file |

---

## Updating an image tag

CI pipelines update the image tag automatically by modifying the `image.tag` field in the relevant environment values file and opening a pull request. For manual updates:

```yaml
# envs/prod/my-app-values.yaml
appName: my-app

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
If you are adding a cluster operator or third-party controller, use `project: infrastructure` and place it under `apps/infra/`.

---

## Bootstrap (first-time cluster setup)

The `bootstrap/` directory contains the root `ApplicationSet`s for each environment. These are applied **once** manually when setting up a new cluster. Apply infra first so CRDs are available before workloads sync.

```bash
# On a fresh cluster, after installing ArgoCD:
kubectl apply -f bootstrap/root-dev-infra.yaml   # infra first — installs CRDs
kubectl apply -f bootstrap/root-dev-apps.yaml     # workloads second

# For staging/prod clusters, apply the corresponding files on that cluster:
kubectl apply -f bootstrap/root-staging-infra.yaml
kubectl apply -f bootstrap/root-staging-apps.yaml
```

You should never need to run `kubectl apply` again after this. All subsequent changes go through git.

---

## Conventions

- **One app, one file** in `apps/` or `apps/infra/`. Never define multiple apps in the same file.
- **Infra vs workloads.** Anything that installs CRDs or runs cluster-wide belongs in `apps/infra/`. Everything else goes in `apps/`.
- **`appName` is the deployment gate.** The `appName` key in the values file must be present for the app to be deployed. Removing it prunes the app. The key must match the filename prefix and the `appName` in the app definition exactly.
- **Pin versions.** For third-party Helm charts, always use an exact `targetRevision` version number, not a range or `latest`.
- **Environment values files are overrides only.** Do not copy the entire chart `values.yaml` into your env file. Only include values you are intentionally changing.
- **Changes go through pull requests.** Direct pushes to `master` are discouraged. All environment changes, especially to staging and prod, should be reviewed before merge.

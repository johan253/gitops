helm repo add argo https://argoproj.github.io/argo-helm || true
helm repo update
helm install argocd argo/argo-cd --namespace argocd --create-namespace -f bootstrap/argocd/values.yaml
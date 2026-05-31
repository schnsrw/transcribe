# Kubernetes manifests

Plain manifests (no Helm / Kustomize) for deploying Casual-SST to a
cluster with NVIDIA GPUs. Designed as a starting point — drop into
`kubectl apply -k .` or copy into your own Helm chart.

## Layout

```
deploy/k8s/
├── README.md           you are here
├── kustomization.yaml  groups the manifests below
├── namespace.yaml      casual-sst namespace
├── secret.yaml         template — DO NOT commit your real values
├── deployment.yaml     the GPU-backed deployment
├── service.yaml        ClusterIP service for the API + metrics
├── ingress.yaml        optional nginx-ingress example with WS support
└── pvc.yaml            persistent claim for the HF model cache
```

## Prerequisites on the cluster

- An NVIDIA GPU node pool. Verify the device-plugin is installed and
  GPUs are reported by `kubectl describe node <gpu-node> | grep nvidia.com/gpu`.
- A `StorageClass` available for ReadWriteOnce volumes (any SSD class
  works; the HF cache is ~2 GB).
- (Optional) nginx-ingress-controller if you want the included ingress.

## Quick deploy

```bash
# 1. Edit secret.yaml — fill in ADMIN_TOKEN, JWT_*, etc.
cp secret.yaml my-secret.yaml
# (real edit; do NOT commit my-secret.yaml)

# 2. Apply:
kubectl apply -k deploy/k8s/
```

## Verify

```bash
kubectl -n casual-sst get pods
kubectl -n casual-sst logs deploy/casual-sst -f
kubectl -n casual-sst port-forward svc/casual-sst 8000:8000

# In another terminal:
curl http://localhost:8000/health
curl http://localhost:8000/metrics
```

## Sizing

The default `deployment.yaml` requests 1 GPU + 4 GB RAM + 2 CPU. The
HF cache PVC defaults to 5 GB. Adjust both based on your model
choice (`large-v3-turbo` ~ 1.5 GB on disk, ~3.5 GB VRAM at runtime).

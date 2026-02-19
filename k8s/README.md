# Mirror Maestro - Kubernetes Deployment

Self-contained Kubernetes manifests for deploying Mirror Maestro. No changes to the existing project files are required.

## Architecture

```
                     ┌──────────────┐
  Internet ─────────►│   Ingress    │  TLS termination
                     └──────┬───────┘
                            │ :80
                     ┌──────▼───────┐
                     │  App Service │
                     └──────┬───────┘
                            │ :8000
                     ┌──────▼───────┐     ┌──────────────┐
                     │   FastAPI    │────►│  PostgreSQL   │
                     │  Deployment  │     │  Deployment   │
                     └──────────────┘     └───────┬───────┘
                            │                     │
                       ┌────▼────┐          ┌─────▼─────┐
                       │app-data │          │postgres-  │
                       │  PVC    │          │data PVC   │
                       └─────────┘          └───────────┘
```

### Components

| Resource | Description |
|----------|-------------|
| **mirror-maestro** Deployment | FastAPI application serving the web UI and API on port 8000. Includes a `wait-for-postgres` init container that blocks startup until the database is reachable. Persists encryption keys to the `app-data` PVC. |
| **postgres** Deployment | PostgreSQL 16 database storing all configuration, mirrors, issue mappings, and user accounts. Data is persisted to the `postgres-data` PVC. Uses `Recreate` strategy to avoid two pods writing to the same volume. |
| **Ingress** | Routes external traffic to the app Service with optional TLS termination. Replaces the nginx reverse proxy used in the Docker Compose setup. |

## Prerequisites

- Kubernetes cluster (1.25+)
- `kubectl` configured for your cluster
- An ingress controller installed (e.g., nginx-ingress, Traefik)
- A StorageClass available for PersistentVolumeClaims

## Quick Start

### 1. Configure secrets

Edit `secret.yaml` and replace **all** default placeholder values:

```bash
vi k8s/secret.yaml
```

The `DATABASE_URL` value must use the same credentials as `POSTGRES_USER` and `POSTGRES_PASSWORD`.

### 2. Configure ingress

Edit `ingress.yaml` and replace `mirror-maestro.example.com` with your actual hostname:

```bash
vi k8s/ingress.yaml
```

Uncomment the annotations that match your ingress controller and TLS setup.

### 3. Deploy

```bash
kubectl apply -k k8s/
```

### 4. Verify

```bash
# Check all resources
kubectl -n mirror-maestro get all

# Watch pods come up
kubectl -n mirror-maestro get pods -w

# Check app logs
kubectl -n mirror-maestro logs -l app.kubernetes.io/component=app -f
```

## Configuration

### Non-sensitive settings

Edit `configmap.yaml`. All environment variables from the project's `.env.example` that are non-sensitive can be placed here. See the main project README for the full list.

### Sensitive settings

Edit `secret.yaml`. For production, consider using an external secret manager:

- [External Secrets Operator](https://external-secrets.io/) for HashiCorp Vault, AWS Secrets Manager, etc.
- [Sealed Secrets](https://sealed-secrets.netlify.app/) for encrypted secrets in Git

### Image version

Pin to a specific release by editing `kustomization.yaml`:

```yaml
images:
  - name: ghcr.io/mrzoller/mirror-maestro
    newTag: "1.2.3"
```

Or via command line:

```bash
kubectl apply -k k8s/ --set-image ghcr.io/mrzoller/mirror-maestro=ghcr.io/mrzoller/mirror-maestro:1.2.3
```

### Storage

Both PVCs default to `10Gi` (PostgreSQL) and `1Gi` (app data). Adjust in `postgres/pvc.yaml` and `app/pvc.yaml`. To use a specific StorageClass, uncomment the `storageClassName` field.

### TLS

Options for TLS certificates:

1. **cert-manager** (recommended): Uncomment the `cert-manager.io/cluster-issuer` annotation in `ingress.yaml`
2. **Manual**: Create a TLS secret and reference it in `ingress.yaml`:
   ```bash
   kubectl -n mirror-maestro create secret tls mirror-maestro-tls \
     --cert=path/to/cert.pem \
     --key=path/to/key.pem
   ```

## Customization with Kustomize Overlays

For managing multiple environments (dev, staging, production), create overlays:

```
k8s/
├── base/              # Move current files here
│   └── kustomization.yaml
└── overlays/
    ├── dev/
    │   └── kustomization.yaml
    ├── staging/
    │   └── kustomization.yaml
    └── production/
        └── kustomization.yaml
```

Example production overlay:

```yaml
# k8s/overlays/production/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base
patchesStrategicMerge:
  - increase-resources.yaml
images:
  - name: ghcr.io/mrzoller/mirror-maestro
    newTag: "1.2.3"
```

## Air-Gapped / Private Registry Deployment

In environments without internet access, all container images must be available in a local registry. Both images are listed in `kustomization.yaml` so you can retarget them with `newName`:

```yaml
# kustomization.yaml
images:
  - name: ghcr.io/mrzoller/mirror-maestro
    newName: harbor.internal/mirror-maestro/app
    newTag: "1.2.3"
  - name: postgres
    newName: harbor.internal/proxy/postgres
    newTag: 16-alpine
```

This rewrites all image references in the manifests (app container, init container, and postgres deployment) without editing individual YAML files.

You also need to enable local frontend vendor assets so the app doesn't try to load Chart.js and D3.js from the CDN at runtime. Set this in `configmap.yaml`:

```yaml
  USE_LOCAL_VENDOR_ASSETS: "true"
```

The Docker image must have been built with the vendor assets baked in (see `scripts/download-vendor-assets.sh` and the main project's [Enterprise Deployment guide](../docs/ENTERPRISE_DEPLOYMENT.md)).

## Troubleshooting

```bash
# Pod status and events
kubectl -n mirror-maestro describe pod <pod-name>

# App logs
kubectl -n mirror-maestro logs deploy/mirror-maestro

# PostgreSQL logs
kubectl -n mirror-maestro logs deploy/postgres

# Database shell
kubectl -n mirror-maestro exec -it deploy/postgres -- psql -U postgres -d mirror_maestro

# Restart the app (e.g., after config change)
kubectl -n mirror-maestro rollout restart deploy/mirror-maestro
```

## Cleanup

```bash
kubectl delete -k k8s/
```

Note: PersistentVolumeClaims are retained by default. To delete data permanently:

```bash
kubectl -n mirror-maestro delete pvc --all
```

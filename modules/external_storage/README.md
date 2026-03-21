# External Storage Module

Route file storage to external backends instead of the local filesystem.

Supported backends:
- Local Filesystem (default)
- AWS S3
- Google Cloud Storage (GCS)
- Google Drive

Configure in Settings → Modules → File Storage.

---

## Google Cloud Storage (GCS)

### Prerequisites

- Google Cloud project with billing enabled
- `google-cloud-storage` Python package (included in requirements.txt)

### Option A: Application Default Credentials (local development)

1. Install Google Cloud CLI: https://cloud.google.com/sdk/docs/install

2. Authenticate:
   ```bash
   gcloud auth application-default login
   ```

3. Set quota project (avoids 403 errors):
   ```bash
   gcloud auth application-default set-quota-project YOUR_PROJECT_ID
   ```

4. In Settings → Modules → File Storage:
   - Select "Google Cloud Storage"
   - Enter your GCS bucket name
   - Auth method: "Application Default Credentials"
   - Click "Test Connection"

### Option B: Service Account JSON Key (production)

1. Go to Google Cloud Console → IAM & Admin → Service Accounts

2. Create a Service Account (or use existing)

3. Grant role: `Storage Object Admin` on your bucket

4. Create a JSON key:
   - Click the Service Account → Keys → Add Key → Create new key → JSON
   - Save the file to a secure location on the server (e.g. `/etc/secrets/gcs-sa.json`)

5. In Settings → Modules → File Storage:
   - Select "Google Cloud Storage"
   - Enter bucket name
   - Auth method: "Service Account JSON Key File"
   - Path: `/etc/secrets/gcs-sa.json`
   - Click "Test Connection"

### Creating a GCS Bucket

```bash
# Create bucket (replace with your names)
gcloud storage buckets create gs://my-autonomos-files --location=europe-west1

# Verify
gcloud storage ls
```

---

## Google Drive

### Prerequisites

- Google Cloud project with billing enabled
- Google Drive API enabled
- `google-api-python-client` and `google-auth` packages (included in requirements.txt)

### Step 1: Enable Google Drive API

```bash
gcloud services enable drive.googleapis.com --project=YOUR_PROJECT_ID
```

Or via Console: APIs & Services → Enable APIs → search "Google Drive API" → Enable.

### Step 2: Create a target folder on Google Drive

1. Open https://drive.google.com
2. Create a folder (e.g. "ContaAutonomo")
3. Copy the folder ID from the URL:
   ```
   https://drive.google.com/drive/folders/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs
                                           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                           This is the folder ID
   ```

The module automatically creates subfolders inside this folder
(invoices_pdf/, expenses_files/, documents_files/, etc.).

### Option A: Application Default Credentials (local development)

1. Authenticate with Drive scope:
   ```bash
   gcloud auth application-default login \
     --scopes="https://www.googleapis.com/auth/drive.file,https://www.googleapis.com/auth/cloud-platform"
   ```

2. Set quota project:
   ```bash
   gcloud auth application-default set-quota-project YOUR_PROJECT_ID
   ```

3. In Settings → Modules → File Storage:
   - Select "Google Drive"
   - Enter the folder ID from step 2
   - Auth method: "Application Default Credentials"
   - Click "Test Connection"

### Option B: Service Account JSON Key (production)

1. Go to Google Cloud Console → IAM & Admin → Service Accounts

2. Create a Service Account (e.g. `autonomos-drive@your-project.iam.gserviceaccount.com`)

3. Create a JSON key:
   - Click the Service Account → Keys → Add Key → Create new key → JSON
   - Save to a secure location (e.g. `/etc/secrets/gdrive-sa.json`)

4. Share the Google Drive folder with the Service Account:
   - Open the folder on Google Drive
   - Click Share → paste the SA email (e.g. `autonomos-drive@your-project.iam.gserviceaccount.com`)
   - Role: Editor
   - Uncheck "Notify people"

5. In Settings → Modules → File Storage:
   - Select "Google Drive"
   - Enter the folder ID
   - Auth method: "Service Account JSON Key File"
   - Path: `/etc/secrets/gdrive-sa.json`
   - Click "Test Connection"

### Docker / Production

For Docker deployments, mount the JSON key file as a volume:

```yaml
services:
  app:
    volumes:
      - ./secrets/gdrive-sa.json:/etc/secrets/gdrive-sa.json:ro
    environment:
      # Or use ADC via env var instead of the settings UI:
      - GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/gdrive-sa.json
```

---

## AWS S3

### Option A: SDK Default (env vars, instance role)

Set environment variables:
```bash
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=eu-west-1
```

Or use an EC2 instance role / ECS task role (no env vars needed).

### Option B: Access Keys (via Settings UI)

Enter Access Key ID and Secret Access Key directly in Settings → Modules.

### Option C: AWS Profile

Use a named profile from `~/.aws/credentials`:
```ini
[my-profile]
aws_access_key_id = AKIA...
aws_secret_access_key = ...
region = eu-west-1
```

---

## Troubleshooting

### 403 Insufficient Permission (Google Drive)

ADC credentials don't have Drive scope. Re-authenticate:
```bash
gcloud auth application-default login \
  --scopes="https://www.googleapis.com/auth/drive.file,https://www.googleapis.com/auth/cloud-platform"
```

### 403 quota project not set (Google APIs)

```bash
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

### Google Drive API not enabled

```bash
gcloud services enable drive.googleapis.com --project=YOUR_PROJECT_ID
```

### Files appear but no subfolders (Google Drive)

Update the module to the latest version (v0.2.0+). Older versions saved all files
in the root folder without creating subfolders.

### Switching backends

When switching from one backend to another, existing files are NOT migrated.
Files uploaded before the switch remain in the old storage. New files go to the
new backend. The app handles both via the `pdf_storage_key` column (falls back
to local path convention for old invoices).

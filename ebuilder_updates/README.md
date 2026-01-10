# eBuilder Updates for Provisioner Support

These files need to be added to eBuilder to support automated provisioning.

## Files to Add

### 1. Management Command

Copy `create_admin_from_env.py` to:
```
ebuilder/core/management/commands/create_admin_from_env.py
```

Make sure the management directory structure exists:
```bash
mkdir -p core/management/commands
touch core/management/__init__.py
touch core/management/commands/__init__.py
```

### 2. Entrypoint Script

Replace the existing `entrypoint.sh` with the new version that includes:
- First-boot detection using `/app/db/.initialized` marker
- Automatic admin user creation from environment variables
- Migration running on every boot (for updates)

## Environment Variables

The provisioner will set these environment variables when creating containers:

| Variable | Description | Example |
|----------|-------------|---------|
| `SITE_NAME` | Store name | "Jane's Digital Shop" |
| `ADMIN_EMAIL` | Admin login email | "jane@example.com" |
| `ADMIN_PASSWORD` | Initial admin password | "TempPass123" |
| `SECRET_KEY` | Django secret key | Auto-generated |
| `ALLOWED_HOSTS` | Allowed hostnames | "janes-shop.ebuilder.host" |
| `DEBUG` | Debug mode | "False" |

## How It Works

1. **First Boot** (no `.initialized` marker):
   - Runs `python manage.py migrate`
   - Runs `python manage.py create_admin_from_env`
   - Runs `python manage.py collectstatic`
   - Creates `/app/db/.initialized` marker

2. **Subsequent Boots** (marker exists):
   - Runs `python manage.py migrate` (for any updates)
   - Starts gunicorn

## Testing Locally

```bash
# Build the image
docker build -t ebuilder:test .

# Run with environment variables
docker run -d \
  --name ebuilder_test \
  -e SITE_NAME="Test Shop" \
  -e ADMIN_EMAIL="test@example.com" \
  -e ADMIN_PASSWORD="testpass123" \
  -e SECRET_KEY="test-secret-key" \
  -e ALLOWED_HOSTS="localhost" \
  -v $(pwd)/test_db:/app/db \
  -v $(pwd)/test_media:/app/media \
  -p 8000:8000 \
  ebuilder:test

# Check logs
docker logs ebuilder_test

# Should see:
# 🚀 First boot detected - initializing...
# ✅ First boot initialization complete!
```

## Health Check

Make sure eBuilder has a `/health/` endpoint. If not, add this view:

```python
# core/views.py
from django.http import HttpResponse

def health_check(request):
    return HttpResponse("OK", status=200)

# core/urls.py
path('health/', health_check, name='health-check'),
```

The provisioner uses this endpoint to verify instances are running correctly.

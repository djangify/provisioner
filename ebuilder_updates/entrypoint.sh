#!/bin/bash
set -e

# =============================================================================
# eBuilder Docker Entrypoint
# =============================================================================
# This script runs when the container starts.
# 
# First boot:
#   - Runs migrations
#   - Creates admin user from env vars
#   - Collects static files
#   - Creates .initialized marker
#
# Subsequent boots:
#   - Runs any pending migrations
#   - Starts the server
# =============================================================================

echo "=========================================="
echo "eBuilder Docker Entrypoint"
echo "=========================================="

# Marker file to detect first boot
INIT_MARKER="/app/db/.initialized"

# First boot initialization
if [ ! -f "$INIT_MARKER" ]; then
    echo ""
    echo "🚀 First boot detected - initializing..."
    echo ""
    
    # Run migrations
    echo "📦 Running database migrations..."
    python manage.py migrate --no-input
    
    # Create admin user from environment variables
    if [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_PASSWORD" ]; then
        echo "👤 Creating admin user..."
        python manage.py create_admin_from_env
    else
        echo "⚠️  ADMIN_EMAIL or ADMIN_PASSWORD not set, skipping admin creation"
    fi
    
    # Collect static files
    echo "📁 Collecting static files..."
    python manage.py collectstatic --no-input
    
    # Create initialization marker
    touch "$INIT_MARKER"
    echo "$(date -Iseconds)" > "$INIT_MARKER"
    
    echo ""
    echo "✅ First boot initialization complete!"
    echo ""
else
    echo ""
    echo "📌 Existing installation detected"
    echo "   Initialized: $(cat $INIT_MARKER)"
    echo ""
    
    # Run any pending migrations (for updates)
    echo "📦 Checking for pending migrations..."
    python manage.py migrate --no-input
fi

# Start the application
echo ""
echo "🌐 Starting eBuilder..."
echo "   Site: ${SITE_NAME:-eBuilder}"
echo "   Port: 8000"
echo ""

# Use gunicorn for production
exec gunicorn ebuilder.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --threads 4 \
    --worker-class gthread \
    --worker-tmp-dir /dev/shm \
    --access-logfile /app/logs/access.log \
    --error-logfile /app/logs/error.log \
    --capture-output \
    --enable-stdio-inheritance

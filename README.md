# eBuilder Provisioner

**Automated hosting provisioning for eBuilder managed hosting.**

When a customer pays for managed hosting, this service automatically:
1. Receives the Stripe webhook
2. Creates a new Docker container running eBuilder
3. Configures nginx reverse proxy
4. Sends the customer their login details

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Stripe         │────▶│  Provisioner    │────▶│  Docker         │
│  (payments)     │     │  (this app)     │     │  (containers)   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │  Nginx          │
                        │  (reverse proxy)│
                        └─────────────────┘
```

## Customer Journey

1. Customer visits ebuilder.host and chooses a plan
2. Enters their store name and subdomain (e.g., "janes-shop")
3. Completes Stripe checkout (£12/month)
4. **Instantly**:
   - Docker container is created with their eBuilder instance
   - Nginx is configured for janes-shop.ebuilder.host
   - Welcome email sent with login details
5. Customer logs in and their store is ready

## Quick Start

### Prerequisites

- Python 3.11+
- Docker installed and running
- Nginx installed
- Wildcard SSL certificate for your domain

### Installation

```bash
# Clone the repo
git clone https://github.com/djangify/provisioner.git
cd provisioner

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
nano .env  # Fill in your values

# Create database directory
mkdir -p db

# Run migrations
python manage.py migrate

# Create admin user
python manage.py createsuperuser

# Run the server
python manage.py runserver
```

### Stripe Setup

1. Create a product in Stripe Dashboard for "eBuilder Managed Hosting"
2. Create a price (£12/month recurring)
3. Copy the Price ID to your `.env` as `STRIPE_PRICE_ID`
4. Set up webhook endpoint:
   - URL: `https://provisioner.ebuilder.host/api/webhook/stripe/`
   - Events to listen for:
     - `checkout.session.completed`
     - `customer.subscription.updated`
     - `customer.subscription.deleted`
     - `invoice.payment_failed`
     - `invoice.paid`
5. Copy webhook secret to `.env` as `STRIPE_WEBHOOK_SECRET`

### Infrastructure Setup

#### Docker Network

```bash
docker network create ebuilder-network
```

#### Customer Data Directory

```bash
sudo mkdir -p /srv/customers
sudo chown $USER:$USER /srv/customers
```

#### Nginx Wildcard SSL

```bash
# Using certbot with DNS challenge
sudo certbot certonly \
  --manual \
  --preferred-challenges dns \
  -d "*.ebuilder.host" \
  -d "ebuilder.host"
```

#### DNS

Add a wildcard A record:
```
*.ebuilder.host  →  YOUR_SERVER_IP
```

## API Endpoints

### Public (No Auth Required)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/check-subdomain/` | POST | Check subdomain availability |
| `/api/create-checkout/` | POST | Create Stripe checkout session |
| `/api/webhook/stripe/` | POST | Stripe webhook receiver |

### Admin Only

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/instances/` | GET | List all instances |
| `/api/instances/{id}/` | GET | Instance details |
| `/api/instances/{id}/start/` | POST | Start instance |
| `/api/instances/{id}/stop/` | POST | Stop instance |
| `/api/instances/{id}/restart/` | POST | Restart instance |
| `/api/instances/{id}/health/` | GET | Health check |
| `/api/instances/{id}/stats/` | GET | Resource usage |
| `/api/customers/` | GET | List all customers |
| `/api/stats/` | GET | Dashboard overview |

## Admin Interface

Access at `/admin/` to:

- View all customers and their subscription status
- See all running instances
- Start/stop/restart containers
- View provisioning logs

## Management Commands

```bash
# Check health of all instances
python manage.py provisioner health

# Sync database with actual container status
python manage.py provisioner sync

# Regenerate all nginx configs
python manage.py provisioner nginx

# Show overview stats
python manage.py provisioner stats

# Clean up deleted containers
python manage.py provisioner cleanup
```

## eBuilder Image Requirements

The eBuilder Docker image (`djangify/ebuilder:latest`) must:

1. Accept these environment variables:
   - `SITE_NAME` - Store name
   - `ADMIN_EMAIL` - Admin email
   - `ADMIN_PASSWORD` - Initial admin password
   - `SECRET_KEY` - Django secret key
   - `ALLOWED_HOSTS` - Comma-separated hosts

2. Auto-create admin user on first boot if not exists

3. Have a `/health/` endpoint that returns 200 when ready

## File Structure

```
provisioner/
├── manage.py
├── requirements.txt
├── .env.example
├── provisioner/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
└── core/
    ├── models.py           # Customer, Subscription, Instance
    ├── admin.py            # Admin interface with actions
    ├── views.py            # DRF API views
    ├── serializers.py      # DRF serializers
    ├── urls.py             # API routing
    ├── docker_manager.py   # Docker container lifecycle
    ├── nginx_manager.py    # Nginx config generation
    ├── stripe_webhooks.py  # Stripe event handlers
    ├── email_service.py    # Transactional emails
    └── management/
        └── commands/
            └── provisioner.py  # Maintenance commands
```

## Production Deployment

### Systemd Service

```ini
# /etc/systemd/system/provisioner.service
[Unit]
Description=eBuilder Provisioner
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/provisioner
ExecStart=/opt/provisioner/venv/bin/gunicorn provisioner.wsgi:application -b 127.0.0.1:8080
Restart=always

[Install]
WantedBy=multi-user.target
```

### Nginx Config (for provisioner itself)

```nginx
server {
    listen 443 ssl http2;
    server_name provisioner.ebuilder.host;
    
    ssl_certificate /etc/letsencrypt/live/ebuilder.host/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ebuilder.host/privkey.pem;
    
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Cron Jobs

```bash
# Health checks every 5 minutes
*/5 * * * * cd /opt/provisioner && venv/bin/python manage.py provisioner health

# Sync status hourly
0 * * * * cd /opt/provisioner && venv/bin/python manage.py provisioner sync

# Cleanup daily
0 2 * * * cd /opt/provisioner && venv/bin/python manage.py provisioner cleanup
```

## Troubleshooting

### Container won't start

```bash
# Check logs
docker logs ebuilder_subdomain

# Check if port is in use
netstat -tlnp | grep 8100
```

### Nginx config errors

```bash
# Test config
nginx -t

# View specific config
cat /etc/nginx/sites-enabled/ebuilder-subdomain.conf
```

### Stripe webhook not receiving

1. Check webhook is enabled in Stripe dashboard
2. Verify endpoint URL is correct
3. Check webhook secret matches `.env`
4. View logs in admin > Provisioning Logs

## License

MIT License - See LICENSE file

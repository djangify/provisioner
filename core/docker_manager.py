"""
Docker Manager - Handles container lifecycle for eBuilder instances

This module provides functions to:
- Create new eBuilder containers
- Start/stop/restart containers
- Health check containers
- Clean up deleted containers
"""

import os
import docker
import requests
from django.conf import settings
from django.utils import timezone
from .models import Instance, ProvisioningLog


class DockerManager:
    """
    Manages Docker containers for eBuilder instances.
    """
    
    def __init__(self):
        self.client = docker.from_env()
        self.image = settings.EBUILDER_IMAGE
        self.data_root = settings.CUSTOMER_DATA_ROOT
        self.network = settings.CONTAINER_NETWORK
    
    def log(self, instance, action, message, details=None):
        """Create a log entry"""
        ProvisioningLog.objects.create(
            instance=instance,
            action=action,
            message=message,
            details=details or {}
        )
    
    def ensure_network_exists(self):
        """Create the Docker network if it doesn't exist"""
        try:
            self.client.networks.get(self.network)
        except docker.errors.NotFound:
            self.client.networks.create(self.network, driver='bridge')
    
    def create_data_directories(self, instance):
        """Create the data directories for an instance"""
        data_dir = instance.data_directory
        os.makedirs(f"{data_dir}/db", exist_ok=True)
        os.makedirs(f"{data_dir}/media", exist_ok=True)
        os.makedirs(f"{data_dir}/logs", exist_ok=True)
        # Set permissions (Docker user is typically 1000)
        os.chmod(data_dir, 0o755)
    
    def provision_instance(self, instance):
        """
        Create and start a new eBuilder container.
        This is called when a new customer signs up.
        """
        try:
            instance.status = 'creating'
            instance.save(update_fields=['status'])
            self.log(instance, 'create', f'Starting provisioning for {instance.subdomain}')
            
            # Allocate a port
            instance.allocate_port()
            
            # Ensure network exists
            self.ensure_network_exists()
            
            # Create data directories
            self.create_data_directories(instance)
            
            # Build container name
            container_name = f"ebuilder_{instance.subdomain}"
            instance.container_name = container_name
            
            # Environment variables for the container
            environment = {
                'SITE_NAME': instance.site_name,
                'ADMIN_EMAIL': instance.admin_email,
                'ADMIN_PASSWORD': instance.admin_password,
                'SECRET_KEY': instance.secret_key,
                'DEBUG': 'False',
                'ALLOWED_HOSTS': f'{instance.subdomain}.{settings.BASE_DOMAIN},localhost',
                'DATABASE_URL': 'sqlite:///db/db.sqlite3',
            }
            
            # Add custom domain if set
            if instance.custom_domain:
                environment['ALLOWED_HOSTS'] += f',{instance.custom_domain}'
            
            # Volume mounts
            volumes = {
                f"{instance.data_directory}/db": {'bind': '/app/db', 'mode': 'rw'},
                f"{instance.data_directory}/media": {'bind': '/app/media', 'mode': 'rw'},
                f"{instance.data_directory}/logs": {'bind': '/app/logs', 'mode': 'rw'},
            }
            
            # Create and start the container
            container = self.client.containers.run(
                self.image,
                name=container_name,
                detach=True,
                restart_policy={'Name': 'unless-stopped'},
                environment=environment,
                volumes=volumes,
                ports={8000: instance.port},
                network=self.network,
                healthcheck={
                    'test': ['CMD', 'curl', '-f', 'http://localhost:8000/health/'],
                    'interval': 30000000000,  # 30 seconds in nanoseconds
                    'timeout': 10000000000,   # 10 seconds
                    'retries': 3
                }
            )
            
            instance.container_id = container.id
            instance.status = 'running'
            instance.status_message = ''
            instance.save(update_fields=['container_id', 'container_name', 'status', 'status_message'])
            
            self.log(instance, 'create', f'Successfully provisioned {instance.subdomain}', {
                'container_id': container.id,
                'port': instance.port
            })
            
            return True
            
        except Exception as e:
            instance.status = 'error'
            instance.status_message = str(e)
            instance.save(update_fields=['status', 'status_message'])
            self.log(instance, 'error', f'Failed to provision: {e}')
            raise
    
    def start_instance(self, instance):
        """Start a stopped container"""
        try:
            container = self.client.containers.get(instance.container_id)
            container.start()
            instance.status = 'running'
            instance.status_message = ''
            instance.save(update_fields=['status', 'status_message'])
            self.log(instance, 'start', f'Started {instance.subdomain}')
            return True
        except docker.errors.NotFound:
            # Container doesn't exist, try to recreate
            self.log(instance, 'start', f'Container not found, reprovisioning {instance.subdomain}')
            return self.provision_instance(instance)
        except Exception as e:
            instance.status = 'error'
            instance.status_message = str(e)
            instance.save(update_fields=['status', 'status_message'])
            self.log(instance, 'error', f'Failed to start: {e}')
            raise
    
    def stop_instance(self, instance):
        """Stop a running container"""
        try:
            container = self.client.containers.get(instance.container_id)
            container.stop(timeout=30)
            instance.status = 'stopped'
            instance.save(update_fields=['status'])
            self.log(instance, 'stop', f'Stopped {instance.subdomain}')
            return True
        except docker.errors.NotFound:
            instance.status = 'stopped'
            instance.save(update_fields=['status'])
            self.log(instance, 'stop', f'Container already removed for {instance.subdomain}')
            return True
        except Exception as e:
            self.log(instance, 'error', f'Failed to stop: {e}')
            raise
    
    def restart_instance(self, instance):
        """Restart a container"""
        try:
            container = self.client.containers.get(instance.container_id)
            container.restart(timeout=30)
            instance.status = 'running'
            instance.save(update_fields=['status'])
            self.log(instance, 'restart', f'Restarted {instance.subdomain}')
            return True
        except Exception as e:
            self.log(instance, 'error', f'Failed to restart: {e}')
            raise
    
    def delete_instance(self, instance, remove_data=False):
        """Remove a container and optionally its data"""
        try:
            try:
                container = self.client.containers.get(instance.container_id)
                container.stop(timeout=10)
                container.remove()
            except docker.errors.NotFound:
                pass  # Container already gone
            
            if remove_data:
                import shutil
                shutil.rmtree(instance.data_directory, ignore_errors=True)
            
            instance.status = 'deleted'
            instance.save(update_fields=['status'])
            self.log(instance, 'delete', f'Deleted {instance.subdomain}', {
                'data_removed': remove_data
            })
            return True
        except Exception as e:
            self.log(instance, 'error', f'Failed to delete: {e}')
            raise
    
    def health_check(self, instance):
        """Check if an instance is responding"""
        try:
            # First check if container is running
            try:
                container = self.client.containers.get(instance.container_id)
                if container.status != 'running':
                    return False
            except docker.errors.NotFound:
                return False
            
            # Then check HTTP health endpoint
            url = f"http://localhost:{instance.port}/health/"
            response = requests.get(url, timeout=5)
            is_healthy = response.status_code == 200
            
            instance.last_health_check = timezone.now()
            instance.save(update_fields=['last_health_check'])
            
            self.log(instance, 'health_check', f'Health check: {"OK" if is_healthy else "FAILED"}')
            
            return is_healthy
            
        except Exception as e:
            self.log(instance, 'health_check', f'Health check error: {e}')
            return False
    
    def get_container_stats(self, instance):
        """Get CPU/memory stats for an instance"""
        try:
            container = self.client.containers.get(instance.container_id)
            stats = container.stats(stream=False)
            
            # Calculate CPU percentage
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                       stats['precpu_stats']['cpu_usage']['total_usage']
            system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                          stats['precpu_stats']['system_cpu_usage']
            cpu_percent = (cpu_delta / system_delta) * 100 if system_delta > 0 else 0
            
            # Memory usage
            memory_usage = stats['memory_stats'].get('usage', 0)
            memory_limit = stats['memory_stats'].get('limit', 1)
            memory_percent = (memory_usage / memory_limit) * 100
            
            return {
                'cpu_percent': round(cpu_percent, 2),
                'memory_usage_mb': round(memory_usage / 1024 / 1024, 2),
                'memory_percent': round(memory_percent, 2)
            }
        except Exception as e:
            return None
    
    def pull_latest_image(self):
        """Pull the latest eBuilder image"""
        self.client.images.pull(self.image)
    
    def update_instance(self, instance):
        """
        Update an instance to the latest eBuilder image.
        Stops the container, pulls new image, recreates with same config.
        """
        try:
            self.log(instance, 'restart', f'Starting update for {instance.subdomain}')
            
            # Stop and remove old container
            try:
                container = self.client.containers.get(instance.container_id)
                container.stop(timeout=30)
                container.remove()
            except docker.errors.NotFound:
                pass
            
            # Pull latest image
            self.pull_latest_image()
            
            # Recreate container (provision will use existing data directories)
            return self.provision_instance(instance)
            
        except Exception as e:
            self.log(instance, 'error', f'Failed to update: {e}')
            raise

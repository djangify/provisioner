"""
Management command for provisioner maintenance tasks.

Usage:
    python manage.py provisioner health     # Check all instance health
    python manage.py provisioner cleanup    # Remove deleted containers
    python manage.py provisioner sync       # Sync container status with DB
    python manage.py provisioner nginx      # Regenerate all nginx configs
"""

from django.core.management.base import BaseCommand
from core.models import Instance
from core.docker_manager import DockerManager
from core.nginx_manager import NginxManager, generate_all_configs


class Command(BaseCommand):
    help = 'Provisioner maintenance commands'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=['health', 'cleanup', 'sync', 'nginx', 'stats'],
            help='Action to perform'
        )
    
    def handle(self, *args, **options):
        action = options['action']
        
        if action == 'health':
            self.health_check()
        elif action == 'cleanup':
            self.cleanup()
        elif action == 'sync':
            self.sync_status()
        elif action == 'nginx':
            self.regenerate_nginx()
        elif action == 'stats':
            self.show_stats()
    
    def health_check(self):
        """Check health of all running instances"""
        manager = DockerManager()
        instances = Instance.objects.filter(status='running')
        
        self.stdout.write(f"Checking {instances.count()} running instances...")
        
        healthy = 0
        unhealthy = 0
        
        for instance in instances:
            is_healthy = manager.health_check(instance)
            status = self.style.SUCCESS('✓') if is_healthy else self.style.ERROR('✗')
            self.stdout.write(f"  {status} {instance.subdomain}")
            
            if is_healthy:
                healthy += 1
            else:
                unhealthy += 1
        
        self.stdout.write(f"\nResults: {healthy} healthy, {unhealthy} unhealthy")
    
    def cleanup(self):
        """Remove containers for deleted instances"""
        manager = DockerManager()
        deleted = Instance.objects.filter(status='deleted')
        
        self.stdout.write(f"Cleaning up {deleted.count()} deleted instances...")
        
        for instance in deleted:
            if instance.container_id:
                try:
                    manager.delete_instance(instance, remove_data=False)
                    self.stdout.write(f"  Cleaned up {instance.subdomain}")
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"  Failed to clean {instance.subdomain}: {e}")
                    )
    
    def sync_status(self):
        """Sync database status with actual container status"""
        import docker
        client = docker.from_env()
        
        instances = Instance.objects.exclude(status__in=['deleted', 'pending'])
        
        self.stdout.write(f"Syncing {instances.count()} instances...")
        
        for instance in instances:
            if not instance.container_id:
                continue
            
            try:
                container = client.containers.get(instance.container_id)
                actual_status = container.status
                
                # Map Docker status to our status
                if actual_status == 'running' and instance.status != 'running':
                    instance.status = 'running'
                    instance.save(update_fields=['status'])
                    self.stdout.write(f"  Updated {instance.subdomain} to running")
                elif actual_status in ['exited', 'stopped'] and instance.status == 'running':
                    instance.status = 'stopped'
                    instance.save(update_fields=['status'])
                    self.stdout.write(f"  Updated {instance.subdomain} to stopped")
                    
            except docker.errors.NotFound:
                if instance.status == 'running':
                    instance.status = 'error'
                    instance.status_message = 'Container not found'
                    instance.save(update_fields=['status', 'status_message'])
                    self.stdout.write(
                        self.style.WARNING(f"  {instance.subdomain} container not found")
                    )
    
    def regenerate_nginx(self):
        """Regenerate all nginx configurations"""
        self.stdout.write("Regenerating nginx configurations...")
        generate_all_configs()
        self.stdout.write(self.style.SUCCESS("Done"))
    
    def show_stats(self):
        """Show overview statistics"""
        from core.models import Customer, Subscription
        
        self.stdout.write("\n=== eBuilder Provisioner Stats ===\n")
        
        self.stdout.write(f"Customers: {Customer.objects.count()}")
        self.stdout.write(f"Active subscriptions: {Subscription.objects.filter(status='active').count()}")
        
        self.stdout.write("\nInstances:")
        for status, label in Instance.STATUS_CHOICES:
            count = Instance.objects.filter(status=status).count()
            if count > 0:
                self.stdout.write(f"  {label}: {count}")
        
        self.stdout.write("")

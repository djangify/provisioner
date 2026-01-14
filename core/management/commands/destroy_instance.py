from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
import shutil
import docker

from core.models import Instance, ProvisioningLog


class Command(BaseCommand):
    help = "Completely destroy an eBuilder instance (Docker + data + DB state)"

    def add_arguments(self, parser):
        parser.add_argument(
            "subdomain",
            type=str,
            help="Subdomain of the instance to destroy (e.g. evastore)",
        )

        parser.add_argument(
            "--hard",
            action="store_true",
            help="Hard delete the instance row instead of marking it deleted",
        )

    def handle(self, *args, **options):
        subdomain = options["subdomain"]
        hard_delete = options["hard"]

        try:
            instance = Instance.objects.get(subdomain=subdomain)
        except Instance.DoesNotExist:
            raise CommandError(f"No instance found with subdomain '{subdomain}'")

        self.stdout.write(f"Destroying instance: {subdomain}")

        client = docker.from_env()

        # 1. Stop + remove Docker container
        if instance.container_id:
            try:
                container = client.containers.get(instance.container_id)
                self.stdout.write("Stopping container...")
                container.stop(timeout=15)
                self.stdout.write("Removing container...")
                container.remove()
            except docker.errors.NotFound:
                self.stdout.write("Container already removed.")
            except Exception as e:
                raise CommandError(f"Failed to remove container: {e}")

        # 2. Remove data directory
        data_dir = instance.data_directory
        if data_dir and data_dir.startswith(settings.CUSTOMER_DATA_ROOT):
            self.stdout.write(f"Removing data directory: {data_dir}")
            shutil.rmtree(data_dir, ignore_errors=True)

        # 3. Update DB
        if hard_delete:
            instance.delete()
            self.stdout.write("Instance row hard-deleted from database.")
        else:
            instance.status = "deleted"
            instance.container_id = ""
            instance.container_name = ""
            instance.save(update_fields=["status", "container_id", "container_name"])
            self.stdout.write("Instance marked as deleted in database.")

        # 4. Log
        ProvisioningLog.objects.create(
            instance=None,
            action="delete",
            message=f"Instance '{subdomain}' destroyed via management command",
            details={
                "hard_delete": hard_delete,
                "data_directory": data_dir,
            },
        )

        self.stdout.write(self.style.SUCCESS("Instance destroyed successfully."))

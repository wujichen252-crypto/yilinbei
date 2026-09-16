import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.api.views import api


class Command(BaseCommand):
    help = "Write the generated Django Ninja OpenAPI document to openapi.json."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="openapi.json")

    def handle(self, *args, **options):
        target = Path(options["output"])
        target.write_text(json.dumps(api.get_openapi_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"OpenAPI written to {target}"))

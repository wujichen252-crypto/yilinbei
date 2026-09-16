import csv

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import User


class Command(BaseCommand):
    help = "Import users from a CSV exported from Laravel; passwords may be Laravel bcrypt hashes."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")

    def handle(self, *args, **options):
        path = options["csv_path"]
        try:
            source = open(path, "r", encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise CommandError(str(exc))
        with source:
            for row in csv.DictReader(source):
                user, _ = User.all_objects.get_or_create(username=row["username"], defaults={"nickname": row.get("nickname", "")})
                for key in ("nickname", "description", "tel", "leader", "type", "parent_id"):
                    if row.get(key): setattr(user, "parent" if key == "parent_id" else key, row[key])
                if row.get("password"): user.password = row["password"]
                user.save()
        self.stdout.write(self.style.SUCCESS("Laravel users imported"))

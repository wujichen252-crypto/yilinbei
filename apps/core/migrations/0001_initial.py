import apps.core.models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                ("last_login", models.DateTimeField(blank=True, null=True, verbose_name="last login")),
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("username", models.CharField(max_length=30, unique=True)),
                ("password", models.CharField(max_length=128, verbose_name="password")),
                ("nickname", models.CharField(max_length=255)),
                ("description", models.CharField(blank=True, default="", max_length=1000, null=True)),
                ("tel", models.CharField(blank=True, default="", max_length=50, null=True)),
                ("leader", models.CharField(blank=True, default="", max_length=50, null=True)),
                ("type", models.IntegerField(default=0)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("parent", models.ForeignKey(blank=True, db_column="parent_id", db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name="children", to="core.user")),
            ],
            options={"db_table": "users", "ordering": ["id"], "swappable": "AUTH_USER_MODEL"},
        ),
        migrations.CreateModel(
            name="PersonalAccessToken",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("tokenable_type", models.CharField(default="App\\Models\\User", max_length=255)),
                ("tokenable_id", models.BigIntegerField()),
                ("name", models.CharField(max_length=255)),
                ("token", models.CharField(max_length=64, unique=True)),
                ("abilities", models.TextField(blank=True, null=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "personal_access_tokens", "indexes": [models.Index(fields=["tokenable_type", "tokenable_id"], name="personal_acc_tokenab_2e3e7f_idx")]},
        ),
        migrations.CreateModel(
            name="Report",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)), ("user_id", models.IntegerField(db_index=True)),
                ("choir_name", models.CharField(max_length=255)), ("name", models.CharField(max_length=255)),
                ("name1", models.CharField(blank=True, max_length=255, null=True)), ("school_name", models.CharField(blank=True, max_length=255, null=True)),
                ("desc", models.CharField(blank=True, max_length=1000, null=True)), ("group", models.CharField(max_length=255)),
                ("establishment", models.CharField(max_length=255)), ("establishment_name", models.CharField(blank=True, max_length=255, null=True)),
                ("contact_name", models.CharField(max_length=255)), ("contact_phone", models.CharField(max_length=255)),
                ("contact_way", models.CharField(blank=True, max_length=255, null=True)), ("time_length", models.IntegerField(default=0)),
                ("spectrum", models.IntegerField(blank=True, null=True)), ("file", models.IntegerField(blank=True, null=True)),
                ("dinner_reservation", apps.core.models.LegacyJSONField(blank=True, default=list, null=True)), ("status", models.IntegerField(blank=True, default=0, null=True)),
                ("remark", models.CharField(blank=True, max_length=255, null=True)), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ], options={"db_table": "report", "indexes": [models.Index(fields=["user_id"], name="report_user_id_59a3ab_idx")]},
        ),
        migrations.CreateModel(
            name="Person",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)), ("name", models.CharField(max_length=255)), ("user_id", models.IntegerField()),
                ("card", models.CharField(max_length=255, unique=True)), ("age", models.IntegerField(blank=True, null=True)), ("school", models.CharField(blank=True, max_length=255, null=True)),
                ("phone", models.CharField(blank=True, max_length=255, null=True)), ("gender", models.CharField(blank=True, max_length=255, null=True)), ("major", models.CharField(blank=True, max_length=255, null=True)),
                ("head", models.CharField(blank=True, max_length=255, null=True)), ("instrument", models.CharField(blank=True, max_length=255, null=True)), ("other", models.CharField(blank=True, max_length=255, null=True)),
                ("remark", models.CharField(blank=True, default="", max_length=255, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ], options={"db_table": "person"},
        ),
        migrations.CreateModel(
            name="ReportPerson",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)), ("report_id", models.IntegerField()), ("person_id", models.IntegerField(blank=True, db_index=True, null=True)),
                ("position", models.IntegerField()), ("type", models.IntegerField()), ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ], options={"db_table": "report_person"},
        ),
        migrations.CreateModel(
            name="Logs",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("user_id", models.BigIntegerField()), ("type", models.IntegerField()), ("content", models.CharField(max_length=5000)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))],
            options={"db_table": "logs", "indexes": [models.Index(fields=["content"], name="logs_content_532f33_idx")]},
        ),
        migrations.CreateModel(
            name="Files",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("user_id", models.IntegerField()), ("filename", models.CharField(max_length=255)), ("type", models.CharField(max_length=255)), ("size", models.FloatField()), ("url", models.CharField(max_length=255)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "files"},
        ),
        migrations.CreateModel(
            name="ScanFiles",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("user_id", models.IntegerField()), ("type", models.IntegerField()), ("files", apps.core.models.LegacyJSONField(blank=True, null=True)), ("status", models.IntegerField(blank=True, default=0, null=True)), ("remark", models.CharField(blank=True, default="", max_length=255, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "scan_files"},
        ),
        migrations.CreateModel(
            name="Recommend",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("user_id", models.IntegerField()), ("file", apps.core.models.LegacyJSONField(blank=True, null=True)), ("status", models.IntegerField(blank=True, default=0, null=True)), ("remark", models.CharField(blank=True, default="", max_length=255, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "recommend"},
        ),
        migrations.CreateModel(
            name="LiveReport",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("user_id", models.IntegerField()), ("report_id", models.BigIntegerField()), ("name", models.CharField(max_length=255)), ("choir_name", models.CharField(max_length=255)), ("district_or_school_name", models.CharField(max_length=255)), ("group", models.CharField(max_length=255)), ("contact_name", models.CharField(max_length=255)), ("contact_phone", models.CharField(max_length=255)), ("files", apps.core.models.LegacyJSONField(blank=True, null=True)), ("status", models.IntegerField(default=0)), ("remark", models.CharField(blank=True, max_length=255, null=True)), ("is_other_show", models.CharField(blank=True, max_length=255, null=True)), ("other_show_message", models.TextField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "live_report"},
        ),
        migrations.CreateModel(
            name="Crew",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("live_report_id", models.IntegerField()), ("name", models.CharField(max_length=255)), ("card", models.CharField(max_length=255)), ("age", models.IntegerField(blank=True, null=True)), ("school", models.CharField(blank=True, max_length=255, null=True)), ("phone", models.CharField(blank=True, max_length=255, null=True)), ("gender", models.IntegerField(blank=True, null=True)), ("major", models.CharField(blank=True, max_length=255, null=True)), ("other", models.CharField(blank=True, max_length=255, null=True)), ("musical_instruments", models.CharField(blank=True, max_length=255, null=True)), ("arrival_time", models.DateField(blank=True, null=True)), ("departure_time", models.DateField(blank=True, null=True)), ("position", models.IntegerField(default=0)), ("type", models.IntegerField(default=0)), ("head", models.CharField(blank=True, max_length=255, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "crew"},
        ),
        migrations.CreateModel(
            name="Leader",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("live_report_id", models.IntegerField()), ("name", models.CharField(max_length=255)), ("gender", models.IntegerField(default=0)), ("linkman", models.IntegerField(default=0)), ("age", models.IntegerField(default=0)), ("unit", models.CharField(blank=True, max_length=255, null=True)), ("card", models.CharField(max_length=255)), ("phone", models.CharField(max_length=255)), ("arrival_time", models.DateField(blank=True, null=True)), ("departure_time", models.DateField(blank=True, null=True)), ("head", models.CharField(blank=True, max_length=255, null=True)), ("remark", models.CharField(blank=True, max_length=255, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "leader"},
        ),
        migrations.CreateModel(name="Draw", fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("name", models.CharField(max_length=255)), ("type", models.IntegerField()), ("index", models.IntegerField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "draw"}),
        migrations.CreateModel(name="Ticket", fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("type_name", models.CharField(max_length=255)), ("ticket_session", models.CharField(blank=True, max_length=255, null=True)), ("time", models.CharField(blank=True, max_length=255, null=True)), ("week", models.CharField(blank=True, max_length=255, null=True)), ("concrete", models.CharField(blank=True, max_length=255, null=True)), ("site", models.CharField(blank=True, max_length=255, null=True)), ("local", models.CharField(blank=True, max_length=255, null=True)), ("number", models.IntegerField(default=0)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "ticket"}),
        migrations.CreateModel(name="TicketSubscribe", fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("ticket_id", models.IntegerField()), ("name", models.CharField(max_length=255)), ("card", models.CharField(max_length=255)), ("phone", models.CharField(max_length=255)), ("ip", models.CharField(max_length=255)), ("code", models.CharField(max_length=255)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "ticket_subscribe"}),
        migrations.CreateModel(name="Student", fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("name", models.CharField(max_length=255)), ("user_id", models.IntegerField()), ("gender", models.IntegerField(blank=True, null=True)), ("age", models.IntegerField(blank=True, null=True)), ("nation", models.CharField(blank=True, max_length=255, null=True)), ("grade", models.CharField(blank=True, max_length=255, null=True)), ("major", models.CharField(blank=True, max_length=255, null=True)), ("phone", models.CharField(blank=True, max_length=255, null=True)), ("card", models.CharField(blank=True, max_length=255, null=True)), ("batch", models.CharField(blank=True, max_length=255, null=True)), ("head", models.CharField(blank=True, max_length=255, null=True)), ("status", models.IntegerField(blank=True, null=True)), ("remark", models.CharField(blank=True, max_length=255, null=True)), ("arrival_time", models.DateField(blank=True, null=True)), ("departure_time", models.DateField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "students"}),
        migrations.CreateModel(name="Statistics", fields=[("id", models.BigAutoField(primary_key=True, serialize=False)), ("user_id", models.IntegerField(blank=True, null=True)), ("file_id", models.IntegerField(blank=True, null=True)), ("deleted_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True))], options={"db_table": "statistics"}),
        migrations.CreateModel(name="PasswordReset", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("email", models.CharField(db_index=True, max_length=255)), ("token", models.CharField(max_length=255)), ("created_at", models.DateTimeField(blank=True, null=True))], options={"db_table": "password_resets"}),
    ]

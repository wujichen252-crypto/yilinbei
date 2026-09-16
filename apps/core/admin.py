from django.contrib import admin

from .models import (Crew, Draw, Files, Leader, LiveReport, Logs, Person,
                     PersonalAccessToken, Recommend, Report, ReportPerson,
                     ScanFiles, Student, Ticket, TicketSubscribe, User)

for model in (User, PersonalAccessToken, Report, Person, ReportPerson, Logs,
              Files, ScanFiles, Recommend, LiveReport, Crew, Leader, Draw,
              Ticket, TicketSubscribe, Student):
    admin.site.register(model)

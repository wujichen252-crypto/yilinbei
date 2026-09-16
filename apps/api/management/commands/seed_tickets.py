from django.core.management.base import BaseCommand

from apps.core.models import Ticket


class Command(BaseCommand):
    help = "Seed the ticket sessions from the Laravel migration (idempotent)."

    def handle(self, *args, **options):
        rows = [
            ("11月15日", "活动开幕式、中学组管乐团及中学组铜管乐团展演", "下午", "13:30-18:00", "绵阳师范学院行知音乐厅", 50),
            ("11月16日", "小学组管乐团1-16号展演", "上午", "09:00-12:00", "绵阳师范学院行知音乐厅", 50),
            ("11月16日", "小学组管乐团17-47号、小学组铜管乐团展演", "下午", "13:30-20:00", "绵阳师范学院行知音乐厅", 50),
            ("11月17日", "大学组管乐团展演", "上午", "8:30-11:00", "绵阳师范学院行知音乐厅", 50),
            ("11月17日", "活动闭幕式", "下午", "15:00-16:00", "绵阳师范学院行知音乐厅", 100),
        ]
        for type_name, session, time, concrete, site, number in rows:
            Ticket.objects.get_or_create(type_name=type_name, ticket_session=session,
                                         defaults={"time": time, "concrete": concrete, "site": site, "number": number})
        self.stdout.write(self.style.SUCCESS("ticket sessions seeded"))

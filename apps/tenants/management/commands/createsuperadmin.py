"""Management command to idempotently create or update a Platform Super Admin user."""
import os
import secrets
import string
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Idempotently creates or updates a Platform Super Admin user."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            default=os.environ.get("ADMIN_USERNAME") or os.environ.get("DJANGO_SUPERUSER_USERNAME") or "admin",
            help="Username for the super admin (defaults to ADMIN_USERNAME or 'admin')",
        )
        parser.add_argument(
            "--email",
            type=str,
            default=os.environ.get("ADMIN_EMAIL") or os.environ.get("DJANGO_SUPERUSER_EMAIL") or "admin@auctionbot.shop",
            help="Email for the super admin (defaults to ADMIN_EMAIL or 'admin@auctionbot.shop')",
        )
        parser.add_argument(
            "--password",
            type=str,
            default=os.environ.get("ADMIN_PASSWORD") or os.environ.get("DJANGO_SUPERUSER_PASSWORD") or "",
            help="Password for the super admin (defaults to ADMIN_PASSWORD; generates secure random password if empty)",
        )
        parser.add_argument(
            "--preserve",
            action="store_true",
            help="If user already exists, preserve current password instead of updating it",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"].strip()
        email = options["email"].strip()
        password = options["password"].strip()
        preserve = options["preserve"]

        if not password and not preserve:
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
            password = "".join(secrets.choice(alphabet) for _ in range(16))
            generated = True
        else:
            generated = False

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_superuser": True,
                "is_staff": True,
                "is_active": True,
            },
        )

        user.is_superuser = True
        user.is_staff = True
        user.is_active = True
        if email:
            user.email = email

        if created or (password and not preserve):
            user.set_password(password)
            user.save()
            action_text = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"[OK] {action_text} super admin user '{username}' successfully."))
            if generated:
                self.stdout.write(self.style.WARNING(f"[!] Generated temporary password: {password}"))
                self.stdout.write(self.style.NOTICE("    Please store this password safely or change it upon logging in."))
        else:
            user.save()
            self.stdout.write(self.style.SUCCESS(f"[OK] Super admin user '{username}' verified (password preserved)."))


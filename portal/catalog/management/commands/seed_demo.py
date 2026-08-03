from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import UserProfile
from accounts.roles import ROLE_IT, ensure_role_groups
from catalog.management.commands.import_stockfeed import (
    EXCEL_DEFAULT,
    parse_products,
)
from catalog.models import Branch, Customer, Product

CUSTOMER_NAME = "VAST AFRICA"

# Fixed key for local sync-service wiring (see README). Not used in production.
LOCAL_CHIVHU_API_KEY = "ssms-local-chivhu-dev-key"

BRANCHES = [
    "Chivhu",
    "Murambinda",
    "Chivi",
    "Masvingo",
    "Jerera",
]


class Command(BaseCommand):
    help = (
        "Seed IT admin user, VAST AFRICA customer/branches, and products "
        "from Product Codes Stockfeed.xlsx."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="admin123",
            help="Password for admin user (default: admin123)",
        )
        parser.add_argument(
            "--file",
            type=str,
            default=str(EXCEL_DEFAULT),
            help="Path to Product Codes Stockfeed.xlsx",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        excel_path = Path(options["file"])
        if not excel_path.exists():
            raise CommandError(
                f"Excel file not found: {excel_path}\n"
                "Place Product Codes Stockfeed.xlsx in portal/data/ "
                "or pass --file."
            )

        ensure_role_groups()

        user, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@edurate.local",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created:
            user.set_password(options["password"])
            user.save()
            self.stdout.write(self.style.SUCCESS("Created admin / " + options["password"]))
        else:
            self.stdout.write("Admin user already exists.")

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = ROLE_IT
        profile.save()
        profile.branches.clear()
        self.stdout.write(self.style.SUCCESS("Admin profile role: IT (all branches)."))

        customer, created = Customer.objects.get_or_create(
            name=CUSTOMER_NAME,
            defaults={
                "phone": "",
                "email": "",
                "address": "Zimbabwe",
                "notes": "VastAfrica stockfeed customer",
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created customer {CUSTOMER_NAME}."))
        else:
            self.stdout.write(f"Customer {CUSTOMER_NAME} already exists.")

        for branch_name in BRANCHES:
            branch, branch_created = Branch.objects.get_or_create(
                customer=customer,
                name=branch_name,
                defaults={"manager": f"{branch_name} Manager"},
            )
            if branch_created:
                self.stdout.write(f"  Created branch: {branch_name}")

        rows = parse_products(excel_path)
        if not rows:
            raise CommandError("No products parsed from spreadsheet.")

        created_n = updated_n = 0
        for row in rows:
            _, was_created = Product.objects.update_or_create(
                code=row["code"],
                defaults={
                    "name": row["name"],
                    "category": row["category"],
                    "barcode": row["code"],
                    "unit": "bag",
                    "selling_price": row["selling_price"],
                    "cost_price": row["selling_price"],
                    "low_stock_threshold": 10,
                    "status": Product.Status.ACTIVE,
                },
            )
            if was_created:
                created_n += 1
            else:
                updated_n += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Products from stockfeed: {len(rows)} "
                f"({created_n} created, {updated_n} updated)."
            )
        )
        cats = (
            Product.objects.order_by("category")
            .values_list("category", flat=True)
            .distinct()
        )
        for cat in cats:
            n = Product.objects.filter(category=cat).count()
            self.stdout.write(f"  {cat}: {n}")

        chivhu, _ = Branch.objects.update_or_create(
            customer=customer,
            name="Chivhu",
            defaults={"manager": "Chivhu Manager", "api_key": LOCAL_CHIVHU_API_KEY},
        )
        if chivhu.api_key != LOCAL_CHIVHU_API_KEY:
            chivhu.api_key = LOCAL_CHIVHU_API_KEY
            chivhu.save(update_fields=["api_key", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(f"Chivhu branch API key: {chivhu.api_key}")
        )
        self.stdout.write(self.style.SUCCESS("Seed complete."))

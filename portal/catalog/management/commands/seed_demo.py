from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.roles import ADMIN_GROUP, SUPPLIER_GROUP, ensure_role_groups
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

# From data/edurate.pdf — VAST AFRICA CHIVHU price list (supplier: edurate /sunrise)
PRODUCTS = [
    (
        "8880029570102",
        "8880029570102",
        "Finisher Pellets Phase 3 25kg",
        "Feed",
        "bag",
        16.50,
        16.50,
    ),
    (
        "8880029560103",
        "8880029560103",
        "Grower Pellets Phase 3 25kg",
        "Feed",
        "bag",
        17.00,
        16.50,
    ),
    (
        "8880029550104",
        "8880029550104",
        "Starter Crumb Phase 3 25kg",
        "Feed",
        "bag",
        17.00,
        17.00,
    ),
    (
        "8880029390106",
        "8880029390106",
        "Sunrise Grower Finisher 50kg 2 Phase",
        "Feed",
        "bag",
        30.00,
        30.00,
    ),
    (
        "8880029380107",
        "8880029380107",
        "Sunrise Growfin 25kg",
        "Feed",
        "bag",
        16.00,
        15.50,
    ),
    (
        "8880029370108",
        "8880029370108",
        "Sunrise Stagrow 25kg",
        "Feed",
        "bag",
        16.00,
        16.00,
    ),
]


class Command(BaseCommand):
    help = "Seed admin user and VAST AFRICA catalog (edurate /sunrise)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="admin123",
            help="Password for admin user (default: admin123)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        ensure_role_groups()
        admin_group = Group.objects.get(name=ADMIN_GROUP)
        supplier_group = Group.objects.get(name=SUPPLIER_GROUP)

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
        user.groups.add(admin_group, supplier_group)

        customer, created = Customer.objects.get_or_create(
            name=CUSTOMER_NAME,
            defaults={
                "phone": "",
                "email": "",
                "address": "Zimbabwe",
                "notes": "edurate /sunrise customer",
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

        for code, barcode, name, category, unit, sell, cost in PRODUCTS:
            product, product_created = Product.objects.update_or_create(
                code=code,
                defaults={
                    "barcode": barcode,
                    "name": name,
                    "category": category,
                    "unit": unit,
                    "selling_price": sell,
                    "cost_price": cost,
                    "low_stock_threshold": 10,
                },
            )
            action = "Created" if product_created else "Updated"
            self.stdout.write(f"  {action} product: {product.name} (${sell})")

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

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.roles import ADMIN_GROUP, SUPPLIER_GROUP, ensure_role_groups
from catalog.models import Branch, Customer, Product
from inventory.models import Dispatch, DispatchItem


class Command(BaseCommand):
    help = "Seed demo supplier data (Zimhope scenario) and admin user."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="admin123",
            help="Password for demo admin user (default: admin123)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        ensure_role_groups()
        admin_group = Group.objects.get(name=ADMIN_GROUP)
        supplier_group = Group.objects.get(name=SUPPLIER_GROUP)

        user, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@zimhope.local",
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

        customers_data = {
            "Ngoni": ["Harare CBD", "Borrowdale", "Chitungwiza"],
            "Kiran": [
                "Avondale",
                "Bulawayo",
                "Gweru",
                "Mutare",
                "Victoria Falls",
            ],
            "Kyle": [
                "Msasa",
                "Southerton",
                "Ruwa",
                "Kadoma",
                "Gweru",
                "Bindura",
                "Marondera",
                "Chinhoyi",
            ],
        }

        for name, branches in customers_data.items():
            customer, _ = Customer.objects.get_or_create(
                name=name,
                defaults={
                    "phone": "+263700000000",
                    "email": f"{name.lower()}@example.com",
                    "address": "Zimbabwe",
                    "notes": "Demo customer",
                },
            )
            for branch_name in branches:
                Branch.objects.get_or_create(
                    customer=customer,
                    name=branch_name,
                    defaults={"manager": f"{branch_name} Manager"},
                )

        products = [
            ("MILK-1L", "6001001", "Milk 1L", "Dairy", "bottle", 2.50, 1.80),
            ("BREAD-LOAF", "6001002", "Bread Loaf", "Bakery", "loaf", 1.20, 0.70),
            ("SUGAR-2KG", "6001003", "Sugar 2kg", "Grocery", "bag", 3.40, 2.50),
            ("COOKING-OIL", "6001004", "Cooking Oil 2L", "Grocery", "bottle", 5.00, 3.80),
            ("RICE-2KG", "6001005", "Rice 2kg", "Grocery", "bag", 4.20, 3.10),
        ]
        product_objs = {}
        for code, barcode, pname, category, unit, sell, cost in products:
            obj, _ = Product.objects.get_or_create(
                code=code,
                defaults={
                    "barcode": barcode,
                    "name": pname,
                    "category": category,
                    "unit": unit,
                    "selling_price": sell,
                    "cost_price": cost,
                    "low_stock_threshold": 20,
                },
            )
            product_objs[code] = obj

        borrowdale = Branch.objects.get(customer__name="Ngoni", name="Borrowdale")
        if not Dispatch.objects.filter(branch=borrowdale, reference="DSP-DEMO").exists():
            dispatch = Dispatch(
                customer=borrowdale.customer,
                branch=borrowdale,
                notes="Demo seed dispatch",
                created_by=user,
                reference="DSP-DEMO",
            )
            dispatch.save()
            DispatchItem.objects.create(
                dispatch=dispatch, product=product_objs["MILK-1L"], quantity=100
            )
            DispatchItem.objects.create(
                dispatch=dispatch, product=product_objs["BREAD-LOAF"], quantity=50
            )
            DispatchItem.objects.create(
                dispatch=dispatch, product=product_objs["SUGAR-2KG"], quantity=40
            )
            dispatch.approve(user=user)
            self.stdout.write(self.style.SUCCESS("Approved demo dispatch to Borrowdale."))
        else:
            self.stdout.write("Demo dispatch already present.")

        sample_branch = Branch.objects.get(customer__name="Ngoni", name="Borrowdale")
        self.stdout.write(
            self.style.SUCCESS(
                f"Sample branch API key (Borrowdale): {sample_branch.api_key}"
            )
        )
        self.stdout.write(self.style.SUCCESS("Seed complete."))

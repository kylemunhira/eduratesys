from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import BranchAPIKeyAuthentication
from .models import Sale, SaleItem, SyncLog, ingest_sale


class SaleItemSerializer(serializers.Serializer):
    product_code = serializers.CharField(required=False, allow_blank=True, default="")
    barcode = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False
    )


class SaleIngestSerializer(serializers.Serializer):
    external_sale_id = serializers.CharField(max_length=100)
    sold_at = serializers.DateTimeField(required=False)
    total_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False
    )
    items = SaleItemSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required.")
        for item in value:
            if not (item.get("product_code") or item.get("barcode")):
                raise serializers.ValidationError(
                    "Each item needs product_code or barcode."
                )
        return value


class SaleItemOutSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.code")

    class Meta:
        model = SaleItem
        fields = ("product_code", "quantity", "unit_price")


class SaleOutSerializer(serializers.ModelSerializer):
    items = SaleItemOutSerializer(many=True, read_only=True)
    branch = serializers.CharField(source="branch.name")

    class Meta:
        model = Sale
        fields = (
            "id",
            "branch",
            "external_sale_id",
            "sold_at",
            "total_amount",
            "received_at",
            "items",
        )


class SaleIngestAPIView(APIView):
    authentication_classes = [BranchAPIKeyAuthentication]
    permission_classes = []

    def post(self, request):
        if not getattr(request, "branch", None) and not request.auth:
            return Response(
                {"detail": "Provide X-API-Key header."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        branch = getattr(request, "branch", None) or request.auth
        serializer = SaleIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale, sync_log, created = ingest_sale(
            branch=branch, data=serializer.validated_data
        )
        if sync_log.status == SyncLog.Status.FAILED:
            return Response(
                {
                    "detail": sync_log.message,
                    "status": sync_log.status,
                    "external_sale_id": sync_log.external_sale_id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sync_log.status == SyncLog.Status.DUPLICATE:
            return Response(
                {
                    "detail": "Duplicate sale ignored.",
                    "status": sync_log.status,
                    "sale": SaleOutSerializer(sale).data if sale else None,
                },
                status=status.HTTP_200_OK,
            )
        return Response(
            {
                "detail": "Sale accepted.",
                "status": sync_log.status,
                "created": created,
                "sale": SaleOutSerializer(sale).data,
            },
            status=status.HTTP_201_CREATED,
        )

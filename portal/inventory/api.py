from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from sales.authentication import BranchAPIKeyAuthentication

from .models import ingest_stock_snapshot


class StockItemSerializer(serializers.Serializer):
    barcode = serializers.CharField()
    quantity = serializers.IntegerField(min_value=0)


class StockSnapshotSerializer(serializers.Serializer):
    items = StockItemSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required.")
        return value


class StockSnapshotAPIView(APIView):
    """Set branch stock from POS remaining quantities (absolute)."""

    authentication_classes = [BranchAPIKeyAuthentication]
    permission_classes = []

    def post(self, request):
        if not getattr(request, "branch", None) and not request.auth:
            return Response(
                {"detail": "Provide X-API-Key header."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        branch = getattr(request, "branch", None) or request.auth
        serializer = StockSnapshotSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = ingest_stock_snapshot(
            branch=branch, items=serializer.validated_data["items"]
        )
        return Response(
            {
                "detail": "Stock snapshot applied.",
                "updated_count": len(result["updated"]),
                "skipped_count": len(result["skipped"]),
                "updated": result["updated"],
                "skipped": result["skipped"],
            },
            status=status.HTTP_200_OK,
        )

"""No personal bearer session is used as the transport identity."""
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from .terrain_transport import (StrictInput, SubmissionInput, issue_transport_challenge,
    receive, status_receipts)


class DeviceTransportThrottle(AnonRateThrottle):
    rate = '120/min'


class ChallengeInput(StrictInput):
    device_id = serializers.IntegerField(min_value=1)
    installation_uuid = serializers.UUIDField()
    purpose = serializers.ChoiceField(choices=['RECEIVE', 'STATUS'])


class BatchInput(StrictInput):
    operations = SubmissionInput(many=True, allow_empty=False, max_length=50)


class StatusInput(StrictInput):
    operation_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=50)


def bounded(request, limit):
    if len(request.body) > limit:
        raise serializers.ValidationError('TRANSPORT_BODY_TOO_LARGE')


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([DeviceTransportThrottle])
def transport_challenge(request):
    bounded(request,4096)
    data = ChallengeInput(data=request.data);data.is_valid(raise_exception=True)
    return Response(issue_transport_challenge(**data.validated_data),status=201)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([DeviceTransportThrottle])
def submissions(request):
    bounded(request,262144)
    data = BatchInput(data=request.data);data.is_valid(raise_exception=True)
    return Response({'receipts':receive(request,data.validated_data['operations'])})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([DeviceTransportThrottle])
def submission_status(request):
    bounded(request,8192)
    data = StatusInput(data=request.data);data.is_valid(raise_exception=True)
    return Response({'receipts':status_receipts(request,data.validated_data['operation_ids'])})

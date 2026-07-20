"""O2ims_InfrastructurePerformance data-model skeleton.

Not wired to pti-o2. Defines the request/record shapes from the TS so the
r-App and a future pti-o2 extension share one vocabulary. Pure stdlib.
"""
import time
import uuid


def _id(prefix):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:12])


def new_measurement_job(collection_interval_s, resource_scope, selection,
                        extensions=None, pre_installed=False):
    """MeasurementJobRequest (TS clause 3.7). extensions permitted only when
    preInstalledJob is False (O-Cloud IM WriteableExtensions constraint)."""
    if extensions and pre_installed:
        raise ValueError("extensions require preInstalledJob=False")
    return {
        "measurementJobId": _id("mj"),
        "collectionInterval": collection_interval_s,
        "resourceScopeCriteria": resource_scope,
        "measurementSelectionCriteria": selection,
        "preInstalledJob": pre_installed,
        "extensions": extensions or {},
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def new_measurement_record(job_id, resource_id, definition_id, value,
                           is_suspect=False):
    """PerformanceMeasurementRecord (O-Cloud IM 4.2.1.4.15.2). timeStamp is
    RFC 3339 / UTC -- valid here because CLOCK_REALTIME is UTC (finding F7)."""
    return {
        "measurementJobId": job_id,
        "resourceId": resource_id,
        "measurementDefinitionId": definition_id,
        "timeStamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "measurementValue": value,          # typed "Any" in the IM
        "isSuspect": is_suspect,            # partial window / stale counter
    }


def new_subscription(callback, delivery="FILE"):
    """PerformanceSubscription (TS clause 3.7.6). FILE mirrors the VES
    fileReady pattern already exercised in the 2025.10 RANPM pipeline."""
    if delivery not in ("FILE", "NOTIFICATION", "STREAM"):
        raise ValueError("bad delivery mechanism")
    return {
        "subscriptionId": _id("sub"),
        "callback": callback,
        "reportDeliveryMechanism": delivery,
        "consumerSubscriptionId": _id("csub"),
    }

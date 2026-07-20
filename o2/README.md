# O2 `InfrastructurePerformance` — skeleton

Reference implementation target for exposing this agent's node metrics over
`O2ims_InfrastructurePerformance` (O-RAN.WG6.TS.O2IMS-INTERFACE-R005-v12.00
clause 3.7, API v2.2.0).

**Status: skeleton.** StarlingX `oran-o2 24.09-25` ships Inventory +
Monitoring(alarms) only; the Performance service is specified but not
implemented. This directory sketches the shape; it is NOT wired to pti-o2 yet.

Before implementing, VERIFY on the target (do not assume):

1. `system application-show oran-o2` — confirm the running version.
2. Locate the pti-o2 source in the applied FluxCD app; confirm the API is Flask
   and structured as O2API / Database / Watcher (as documented) before adding a
   namespace.
3. Confirm whether `O2ims_InfrastructurePerformance` types already exist as
   stubs in that version — if so, extend rather than add.
4. Read O2 GA&P clause 3.9.5 / 3.9.8 for the PM Dictionary + semantic
   transformation rules that govern the report format (the TS leaves the
   payload format unspecified — see docs/findings.md S3).

Do not build the pti-o2 extension until 1–4 are checked. This is exactly the
"verify before build" discipline the rest of the project follows.

## MVP resource set (all M in the TS; a partial impl is non-conformant — declare it in docs/ics.md)

    POST /o2ims-infrastructurePerformance/v1/measurementJobs
    GET  /o2ims-infrastructurePerformance/v1/measurementJobs
    GET  /o2ims-infrastructurePerformance/v1/measurementJobs/{id}
    DELETE ...                                    /measurementJobs/{id}
    POST /o2ims-infrastructurePerformance/v1/performanceSubscriptions   (FILE delivery)

## Mapping

Agent metric  ->  PM Dictionary measurementName  ->  O2 record
See pm-dictionary/ocloud-du-pm-dictionary.json. Energy from RAPL/Redfish,
CPU from /proc, delivered freq + membw from the uncore collector.

# Implementation Conformance Statement (skeleton)

Per ISO/IEC 9646-7, referenced as [26] by O-RAN.WG6.TS.O2IMS-INTERFACE-R005-v12.00.

A conformance testing platform that publishes a rigorous ICS for its own
implementation is demonstrating the method, not confessing a shortfall.

## Target

`O2ims_InfrastructurePerformance`, apiName `O2ims_infrastructurePerformance`,
API version 2.2.0, O-RAN.WG6.TS.O2IMS-INTERFACE-R005-v12.00 clause 3.7.

Implemented as an extension to `pti-o2` (StarlingX `oran-o2 24.09-25`), which
ships `O2ims_InfrastructureInventory` and `O2ims_InfrastructureMonitoring` only.

## Resource support

| Resource | URI | Method | Spec | Status |
|---|---|---|---|---|
| Measurement Job List | `/measurementJobs` | POST | M | PLANNED |
| Measurement Job List | `/measurementJobs` | GET | M | PLANNED |
| Measurement Job | `/measurementJobs/{id}` | GET | M | PLANNED |
| Measurement Job | `/measurementJobs/{id}` | PATCH | M | NOT IMPLEMENTED |
| Measurement Job | `/measurementJobs/{id}` | DELETE | M | PLANNED |
| Suspend | `/measurementJobs/{id}/suspend` | POST | M | NOT IMPLEMENTED |
| Resume | `/measurementJobs/{id}/resume` | POST | M | NOT IMPLEMENTED |
| Service Configuration | `/performanceServiceConfiguration` | GET | M | NOT IMPLEMENTED |
| Service Configuration | `/performanceServiceConfiguration` | PUT/PATCH | M | NOT IMPLEMENTED |
| Subscription List | `/performanceSubscriptions` | POST | M | PLANNED (FILE only) |
| Subscription List | `/performanceSubscriptions` | GET | M | PLANNED |
| Subscription | `/performanceSubscriptions/{id}` | GET | M | PLANNED |
| Subscription | `/performanceSubscriptions/{id}` | DELETE | M | PLANNED |

All resources are marked **M** in the TS. A partial implementation is
non-conformant. This document is the declaration.

## Declared deviations

| # | Deviation | Rationale |
|---|---|---|
| D1 | `reportDeliveryMechanism`: FILE only; NOTIFICATION and STREAM not implemented. | §3.7.5 states the API specifies no notifications, while NOTIFICATION is a mandatory enum. The notification structure is undefined. |
| D2 | Report format is a local profile. | §3.7.6.2.5: notification/file/stream formats are not specified in the present document version. |
| D3 | PM Dictionary is locally authored. | ES TR §6.2.2.1: the dictionary is provided by the O-Cloud Resource type vendor. No dictionary exists for this resource type. |
| D4 | `collectionInterval` used at 1 s for research capture. | Spec default 300 s; ES TR says intervals are typically minutes. Conformant export uses semantic transformation (O2 GA&P §3.9.10) to avg/min/max at a conformant interval; raw 1 Hz goes to file. |
| D5 | EC_node,core and EC_node,uncore not reported. | No PP0/PP1 RAPL domain on the platform (finding F3/S2). |
| D6 | EC_node,platform sourced off-node. | The node cannot route to its own BMC (finding F9). |

## Extensions declared

`PollModeWorkload` measurement group, carried via `MeasurementJobRequest.extensions`.
Populated by the `uncore` collector (IPC, MPKI, delivered frequency, hardware
C-state residency, per-socket memory bandwidth) — all validated readable on joule.

Permitted: O-Cloud IM constrains `WriteableExtensions` to jobs where
`preInstalledJob` is False. Jobs created by the r-App satisfy this.

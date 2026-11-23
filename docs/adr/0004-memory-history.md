# Server-side memory history

## Status

Accepted.

## Context

Dashboard memory observations previously existed only in the browser polling response. Memory history must continue to accrue while basal-ui is running without an open tab, survive an app restart, and share the Dashboard's dated ranges with recorded Decision traffic.

## Decision

A daemon sampler runs every 60 seconds for the lifetime of the app, independent of browser polling. It stores only measured host used RAM, the compatible host remainder, and per-runtime process-byte totals. Missing values remain unknown and are not counted as zero. Each minute, hour, or day bucket retains a byte sum, maximum, and sample count; API averages are derived from sum/count. Buckets are minute-granularity for 24 hours, hourly through 90 days, and daily through five years. Roll-up preserves the maximum and weighted average; data older than five years is removed. Memory history has its own `collection_started` coverage marker.

The history shares the stats JSON file, settings lock, and atomic write path used by traffic history. The 60-second collection interval is independent of the Dashboard display-refresh setting. Missing buckets represent gaps, not zero-valued observations.

Measurements retain their existing accounting meaning. Host used RAM is the host's measured used-memory value. Runtime process bytes are process RSS and include runtime overhead; on Apple unified-memory hosts, RSS excludes GPU allocations. The host remainder (`other`) is stored only when host and runtime measurements share the `resident` accounting basis. It is not calculated from RSS on unified-memory hosts. Runtime-provided model, context, and cache measurements are intentionally excluded from dated history.

## Consequences

The history is available when the browser reopens and after basal-ui restarts, but a gap remains wherever the app did not record a sample. A bucket reports average and peak of measured values plus the number of those measurements; it does not interpolate missing intervals. Runtime totals are keyed by runtime, not provider order. Individual model identifiers are omitted to keep persistent history compact and avoid retaining model-level activity; the live view remains the place for per-model details.

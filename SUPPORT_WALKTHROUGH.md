# Support walkthrough: investigating an unexpected tank reading

This example shows how I would investigate a report that a water-tank reading is missing, unexpected, or did not produce an expected alert. It follows the relevant path in the private ManageMyFarm backend and illustrates the evidence I would collect before escalating an issue.

The code example is for farm water monitoring. It is not a fuel-management implementation and does not claim to be Everlink Clarity code. The support habits shown here—checking data, routing, configuration, logs, and alert handling—apply to connected SaaS systems more broadly.

## 1. Clarify the report

Capture the affected device and gateway identifiers, the farm's local time and time zone, when the issue began, the expected and observed values, and whether the issue affects one device or several. Ask whether the sensor or gateway has recently been moved, reconfigured, or restarted.

Avoid putting customer names, credentials, or other identifying production data into a public issue or portfolio example.

## 2. Confirm the uplink reached the right tenant

The AWS IoT provider normalizes incoming uplinks. The handler looks up the gateway EUI in the platform database, then selects that tenant's database. If the gateway is unregistered, the handler logs the condition and drops the packet; it does not route the packet to a default tenant. The handler also ignores readings from devices that are not registered in that tenant.

For a suspected routing or ingestion issue, correlate the event timestamp and gateway/device identifiers with the AWS IoT and application logs. Check for gateway lookup failures, an unregistered gateway, an unregistered device, or a payload that did not decode into `water_level`.

## 3. Check the tenant-scoped readings

The `sensor_readings` table stores the device ID, timestamp, and water metrics. Each tenant has its own database in this system, so run the check against the database selected for the affected tenant. This illustrative parameterized SQL uses the model's field and table names; bind-variable syntax may need adapting to the SQL client:

```sql
SELECT timestamp, water_level, water_flow_rate, water_pressure
FROM sensor_readings
WHERE device_id = :device_id
  AND timestamp >= :from_utc
ORDER BY timestamp DESC
LIMIT 50;
```

Confirm that readings exist in the expected time range, that timestamps are current, and that values are non-null and plausible for the device. Compare the timestamp and metrics with the incoming uplink where available. If there are no rows, continue investigating gateway routing, device registration, payload decoding, and persistence before treating it as an alert-rule issue.

## 4. Trace the rapid-drop rule

For the tank-drop detector, the code:

1. Reads up to 20 non-null water-level readings from the last 10 minutes for that device.
2. Requires at least two readings and positive values.
3. Compares the latest reading with the reading closest to five minutes earlier.
4. Checks the percentage drop against the configured policy threshold (10% in the current source policy).
5. Checks for an irrigation activity that is in progress or was completed within the 15-minute suppression window.

This helps distinguish “no alert because the detector lacked enough recent data,” “no alert because the drop was below policy,” and “no alert because recorded irrigation suppressed it.” The detector returns a signal; the uplink handler passes the resulting alert payload to alert persistence, then schedules broadcast and delivery. Water-detector exceptions are logged, while individual detector failures are isolated so the other water checks can continue.

## 5. Record a useful escalation

If the issue needs the Software team, provide a concise, reproducible record:

- Tenant and device identifiers through the approved support channel
- Gateway identifier, event time, and time zone
- Expected behavior and observed behavior
- Relevant reading timestamps and values, or confirmation that readings are absent
- Whether the gateway and device are registered to the tenant
- Relevant sanitized log messages and the checks already completed
- The detector outcome: insufficient samples, below threshold, irrigation suppression, alert persisted, or an exception

Keep customer updates factual: explain what has been confirmed, what is still being checked, who owns the next step, and when the customer will hear back. Do not describe a recommendation to close a valve as an automated action; the excerpt's default behavior is alert-oriented.

## Scope

This is a source-based troubleshooting example, not a report of a real customer incident. It contains no production customer data and does not require the private product's AWS environment to read.

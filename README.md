Options to Reduce Cold Starts
1. Lambda SnapStart ⭐ Best Option for This Function
Your function runs Python 3.12, which is fully supported by SnapStart. This is the easiest win — it takes a snapshot of the initialized execution environment when you publish a new version, so subsequent cold starts resume from the snapshot instead of initializing from scratch.

How to enable: Go to your function → Configuration → General configuration → Edit → set SnapStart to PublishedVersions, then publish a new version.
Expected improvement: Sub-second startup, typically 10x faster cold starts.
Cost: Small additional charge for snapshot caching (~3 hour minimum per snapshot restore).
Caveat: SnapStart requires you to invoke a published version (not $LATEST). If your Firehose delivery stream is pointing to $LATEST, you'd need to update it to point to the published version ARN.
Direct link: Enable SnapStart on this function 

2. Provisioned Concurrency
Pre-warms a fixed number of execution environments so they're always ready. Zero cold starts for those pre-warmed instances.

How to enable: Go to your function → Configuration → Concurrency → Add provisioned concurrency. Setting it to 2–3 would cover most of the scaling events you're seeing.
Trade-off: You pay for provisioned concurrency even when the function isn't being invoked. For a Firehose processor that runs continuously, this cost is usually justified.
Best used with: A published version or alias (not $LATEST).
Direct link: Configure Provisioned Concurrency 

3. Increase Memory Allocation
Your function currently uses 256 MB with 111 MB max used. Increasing memory also increases CPU allocation, which speeds up the init phase. Going from 256 MB → 512 MB can noticeably reduce cold start duration with minimal cost impact since the function runs fast (~300–500ms).

How to enable: Configuration → General configuration → Memory → increase to 512 MB.
Cost impact: Minimal — billed duration is short, and the per-GB-second rate means doubling memory roughly doubles cost per invocation, but the faster execution partially offsets it.
4. Optimize the Deployment Package
Smaller packages load faster during init. If the function has unused dependencies, trimming them reduces the time Lambda spends loading code during a cold start.

Recommendation for Your Case
Given that this is a Firehose processor (invoked continuously, not sporadically), the 19 cold starts in 24 hours are relatively low. The best bang-for-buck approach is:

Enable SnapStart — free to try, minimal code change, biggest cold start reduction.
If cold starts still matter after SnapStart, add Provisioned Concurrency of 2 to cover burst scaling events.
The cold starts you're seeing are mostly happening during scale-out bursts (e.g., the 02:00 UTC and 16:00–17:00 UTC high-traffic windows), so Provisioned Concurrency of 2–3 would absorb those without over-provisioning.


Root Cause: Lambda.InvokeLimitExceeded → Files Land in S3
The Lambda itself never failed (ProcessingFailed = 0) — but Firehose couldn't even invoke the Lambda on 22 occasions because the Lambda concurrent execution limit was hit. When Firehose can't invoke the processor Lambda after retries, it treats those batches as failed delivery and writes them to S3 as backup. That's exactly what you're seeing in the bucket.

What Happened
Metric	Value
Lambda.InvokeLimitExceeded errors	22 times
InternalError	1 time
Affected window	02:00 – 07:00 UTC today
S3 Backup Mode	FailedEventsOnly → writes to s3://cdicares-cloudwatch-to-splunk-logs-dev01-us-west-2/failed-events/
Retry duration on Firehose	30 seconds — after that, batch goes to S3
The errors were concentrated in the 02:00–07:00 UTC window, which is exactly the same window where you saw the highest reingestion activity and the slowest invocation durations (max 3,701ms at 02:00). The function was scaling hard, hitting the concurrency ceiling, and Firehose couldn't get invocations through.

Why It's Happening
Your function has no reserved concurrency set, so it competes with every other Lambda in the account against the 1,000 account-wide limit. During burst periods, other functions in the account may be consuming concurrency, leaving none available for this Firehose processor.

How to Fix It
Option 1: Set Reserved Concurrency for This Function (Recommended)
Reserve a dedicated concurrency slice so Firehose always has capacity to invoke this function, regardless of what other Lambdas are doing.

Based on your traffic (peak ~4,700 records/hour, ~60s buffer interval), you're processing batches roughly every minute. A reservation of 10–20 concurrent executions would be more than sufficient.
Go to: Configure Concurrency  → Edit → Reserved concurrency → set to 20
Option 2: Increase the Account Concurrency Limit
If other functions are legitimately consuming the 1,000 limit, request a quota increase via Service Quotas  for Concurrent executions in us-west-2.

Option 3: Increase Firehose Retry Duration
Currently set to 30 seconds — very short. Increasing it to 300–600 seconds gives Firehose more time to retry when Lambda is briefly throttled, reducing the chance of fallback to S3. This is a complementary fix, not a standalone one.


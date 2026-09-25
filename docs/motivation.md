# Why gate locally

## The mechanism

A provider's rate-limit headers describe capacity that already exists on their side. Reading them and gating locally means a request that would be rejected is never sent — no round trip, no held connection, no server-side work spent on a call that was always going to fail.

The alternative, reactive pattern — send, receive a 429, back off, retry — pays that cost every time before learning anything. It also depends on a static, hardcoded limit going stale the moment a provider raises or lowers it. Reading the limit from the response instead of a constant means the client adjusts on the next call, automatically, with no redeploy.

An analogy, if it helps: a bouncer who checks capacity before waving someone in saves that person the wasted trip inside — the walk in, the wait, the walk back out — that they'd pay if let in and then ejected once the venue turns out to be over capacity. The cost of getting turned away falls on the person trying to get in, not on the venue. Gating on read-back headers is the former; reactive backoff is the latter — and in both cases, it's the caller who pays for a rejected request, not the provider.

## Why it matters beyond your own resource usage

If your application depends on a third-party API, that provider's rate limit is part of your reliability surface, whether or not you've accounted for it. When traffic grows into the limit, or the provider tightens it, the failure does not stay at the API boundary — it surfaces as errors, stalls, and retries in front of your own users, who have no visibility into the third party involved.

Gating locally absorbs that at the point of origin: a change in the provider's enforcement is handled as routine state, not as an incident.

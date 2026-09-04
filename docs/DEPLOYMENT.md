# Deployment provider options and free-tier operating plan

Last reviewed: 2026-09-04

This document records the delivery-provider evaluation for issue
[#53](https://github.com/Patric-1613/Virtusa_CaPStone/issues/53). Pricing and free-tier limits
change independently of this repository, so the delivery owner must recheck the linked provider
pages before creating a resource or approving a cost.

This is an operational recommendation, not authorization to create an account, paid resource,
production credential, or real subscriber campaign. Secrets belong in the deployment provider's
secret environment, never in Git, issue comments, build output, or application logs.

## MVP recommendation

Use the following combination for the seven-day MVP:

| Capability | Provider and plan | MVP use |
|---|---|---|
| React/Vite frontend | Render Static Site, Free | Build and serve the static frontend. |
| FastAPI service | Render Web Service, Free | Serve health/readiness and later `/v1` routes. |
| PostgreSQL | Render Postgres, Free | Temporary MVP/demo data only. |
| Scheduled pipeline | Authorised operator's local CLI | Run the documented pipeline command from an authorised team member's local checkout; do not create a Render Cron Job or public HTTP trigger while the project must remain at zero cost. |
| Transactional email | Resend, Free | Send confirmation, unsubscribe, and digest test emails over the HTTPS API. |

This selection keeps deployment understandable for a three-person capstone and matches the
existing FastAPI, PostgreSQL, and provider-adapter architecture. It is not a production-readiness
claim.

## Render free-tier constraints

Render documents the current free allowances and limitations in
[Deploy for Free](https://render.com/docs/free):

- Static Sites are free, subject to the workspace's included bandwidth and build-pipeline usage.
- A workspace receives 750 free web-service instance hours per calendar month.
- A free web service spins down after 15 minutes without inbound traffic. Its next request can be
  delayed while the service starts again.
- Free web services have an ephemeral filesystem and cannot attach a persistent disk.
- Free web services cannot make outbound SMTP connections on ports 25, 465, or 587. Email delivery
  must therefore use Resend's HTTPS API, not SMTP.
- Only one free Render Postgres database can be active per workspace. It has 1 GB of storage, no
  backups, no managed connection pooling, and expires 30 days after creation. After the documented
  14-day upgrade grace period, Render deletes it.
- Render may restart or maintain free services and databases at any time. The free plans are for
  preview and hobby use, not production availability.

Render [Cron Jobs](https://render.com/docs/cronjobs) are billed by runtime and have a minimum
monthly charge of USD 1 per cron service. A manually invoked worker command is therefore the MVP
choice while the project is required to cost zero. If the team later accepts that cost, one daily
cron service can be added after the worker command is stable and its idempotency tests pass.

### Render operating rules for the MVP

1. Select `free` explicitly for the web service and PostgreSQL; do not rely on dashboard defaults.
2. Do not create a paid service, cron service, disk, or larger database without a recorded team
   decision.
3. Run the daily collection/digest command manually from an authorised team member's local
   checkout for the demonstration. Supply required values through that operator's untracked local
   environment; never expose the command through an unauthenticated public endpoint.
4. Warm the API with a health request shortly before a live demonstration, while still treating
   cold-start behavior as expected rather than masking readiness failures.
5. Export through an approved migration process or move to durable PostgreSQL before the 30-day
   expiry. The free database is not the long-term system of record promised by ADR 0002.
6. Keep the application stateless outside PostgreSQL; never rely on Render's local filesystem.

### Manual pipeline execution boundary

For the zero-cost MVP, an authorised team member runs the documented collection/digest CLI command
from a local repository checkout. The operator supplies deployment configuration through an
untracked local environment and monitors the command directly. This is an operational action, not
an API route: the command must not be wrapped in or exposed through an unauthenticated public HTTP
endpoint. Any later remote or scheduled trigger requires a separate security decision covering
authentication, authorisation, replay protection, rate limits, audit logging, and secret access.

## Resend free-tier constraints

Resend's current [pricing](https://resend.com/pricing) and
[quota documentation](https://resend.com/docs/knowledge-base/account-quotas-and-limits) list:

- 3,000 transactional emails per month;
- 100 transactional emails per day;
- three verified domains on the Free plan;
- 30 days of activity-data retention; and
- no free-plan overage. Sending pauses when the free allowance is exhausted.

Each `To`, `CC`, or `BCC` recipient counts separately. The MVP must therefore test only with the
three approved team recipients or Resend's documented
[test addresses](https://resend.com/docs/dashboard/emails/send-test-emails), and it must not send to
a real subscriber list.

Resend requires an API key for the HTTPS API. Create a sending-only, domain-restricted key where
possible and store it only in the deployment secret environment. Resend displays a newly created
key once, so the key owner must record and rotate it through the agreed secret-management process.
See [Resend API keys](https://resend.com/docs/dashboard/api-keys/introduction).

Sending to recipients beyond the account's restricted test case requires a domain the team owns.
The DNS owner must publish and verify the SPF and DKIM records described in
[Managing Domains](https://resend.com/docs/dashboard/domains/introduction). Domain registration is
separate from Resend and may introduce a cost if the team does not already own one.

### Resend operating rules for the MVP

1. Use the HTTPS email API, never SMTP from the Render service.
2. Use the provider adapter required by the architecture; domain logic must not import a Resend SDK.
3. Keep an application-side daily safety limit below the provider's 100-message cap.
4. Preserve delivery idempotency so retries cannot consume the quota or send duplicate messages.
5. Log provider message identifiers and safe status metadata, never raw tokens or subscriber email
   addresses.
6. Use team/test recipients until the domain, consent, confirmation, unsubscribe, and suppression
   paths have passed their tests.

## Alternatives considered

The existing boundaries keep these providers replaceable. An alternative should still expose
PostgreSQL through `DATABASE_URL`, run the FastAPI ASGI service, use environment-backed secrets,
and sit behind the delivery email adapter.

### Application and database hosting

| Option | Current free entry point | Relevant trade-offs | Fit for this MVP |
|---|---|---|---|
| Render | Free Static Site, Web Service, and temporary PostgreSQL | One dashboard and simple Git deployment, but cold starts and a database that expires after 30 days. Cron costs at least USD 1/month. | **Recommended for the seven-day demo.** |
| Railway | Free plan with USD 1 of monthly usage credit and one small replica | The credit can be exhausted by an API/database combination; stronger workspace collaboration is associated with paid plans. See [Railway plans](https://docs.railway.com/pricing/plans). | Convenient fallback, but less predictable as a zero-cost shared stack. |
| Koyeb | One free 512 MB Web Service and one free database limited to 5 compute hours per month | The free database sleeps after 5 minutes of inactivity and currently supports PostgreSQL 14–16, not the PostgreSQL 17 baseline required by ADR 0002. The free service scales to zero after one hour; one free application instance cannot also provide a separate worker; Starter organizations are limited to one member and require a valid payment method. See [instances](https://www.koyeb.com/docs/reference/instances), [databases](https://www.koyeb.com/docs/databases), and [organizations](https://www.koyeb.com/docs/reference/organizations). | **Not a direct fallback.** Requires PostgreSQL compatibility validation and explicit architecture approval before use, in addition to accepting weaker team ergonomics. |
| Render plus Supabase PostgreSQL | Render hosts the UI/API; Supabase Free supplies a 500 MB PostgreSQL database | Avoids Render's 30-day database expiry, but introduces a second provider. Free Supabase projects pause after one week of inactivity and have no automatic backups. See [Supabase pricing](https://supabase.com/pricing). | Best zero-cost fallback if the database must survive beyond 30 days. |

Do not switch database providers after migrations begin without checking PostgreSQL version,
extension, connection, migration, backup, and data-export compatibility. In particular, Koyeb's
current PostgreSQL 14–16 range does not satisfy ADR 0002's PostgreSQL 17 baseline without an
approved compatibility decision. ADR 0002 remains authoritative for the storage model regardless
of host.

### Transactional email

| Option | Current free entry point | Relevant trade-offs | Fit for this MVP |
|---|---|---|---|
| Resend | 3,000 emails/month and 100/day | Clear HTTPS API, sufficient MVP quota, domain verification required for general recipients. | **Recommended.** |
| MailerSend | 500 emails/month and 100 API requests/day | One domain, one user seat plus accountant role, one-day activity retention, and the Free plan requires account approval and payment-card details. See [MailerSend pricing](https://www.mailersend.com/pricing). | Capable fallback, but lower quota and more account friction. |
| Postmark | Non-expiring Developer plan with 100 emails/month | Excellent for a small technical demonstration but too restrictive for even modest subscriber growth. See [Postmark pricing](https://postmarkapp.com/pricing/). | Testing fallback only. |
| Amazon SES | Usage-priced service; new AWS accounts can apply general introductory credits | More infrastructure, identity, billing, and operational setup. The former SES-specific free tier is no longer available to new customers. See [Amazon SES pricing](https://aws.amazon.com/ses/pricing/). | Not selected for the seven-day MVP. |

Changing email providers must not weaken ADR 0012. Confirmation and unsubscribe tokens, consent
generation, privacy-preserving responses, idempotency, and secret-handling requirements remain the
same.

## Upgrade triggers

The team must revisit the provider decision before any of these events:

- the API must remain responsive without free-tier cold starts;
- PostgreSQL data must remain available beyond the 30-day MVP window or require backups;
- automated daily scheduling is required instead of a manual demonstration command;
- expected transactional email exceeds 100 recipients per day or 3,000 per month;
- a real subscriber list replaces controlled team/test recipients;
- availability, support, audit, data-retention, or recovery requirements become production-grade;
- a provider asks for a payment method or enables usage-based overages.

At that point, record the expected monthly cost, owner, spending cap, rollback/export path, and
non-author approval before enabling the paid resource.

## Day 1 access checklist

- [ ] Confirm whether a shared Render workspace already exists; otherwise nominate its owner.
- [ ] Confirm whether a shared Resend team already exists; otherwise nominate its owner.
- [ ] Confirm who can connect the GitHub repository to Render.
- [ ] Confirm who owns the sending domain and can edit its DNS records.
- [ ] Confirm who creates and rotates the Resend sending-only API key.
- [ ] Confirm who owns `DATABASE_URL`, application signing keys, API-provider keys, and allowed
  frontend-origin configuration.
- [ ] Confirm that no paid resource or automatic overage is enabled for the MVP.

# Compliance checklist (for the owner)

**This is a checklist, not legal, tax or accounting advice.** It lists things that usually need attention before an app takes
money or personal data. Check each one with a qualified attorney or CPA for your situation; laws change and differ by place.
Every policy page the platform generates is a DRAFT until you set `legal.reviewed: true` for that app after professional review.

Tick a box only when it is really done. "Why" says what goes wrong if you skip it.

## Before the first real customer

- [ ] **Business entity and EIN.** Decide how you operate (sole proprietor, LLC, other) and get an EIN if needed.
  *Why:* it decides who is legally responsible, whose name goes on the policy pages (`legal.legal_name`), and what the bank and Stripe ask for.
- [ ] **Business bank account.** Separate from personal money.
  *Why:* keeps records clean for tax, and Stripe pays out to it.
- [ ] **Postal address for the policy pages and emails.** A PO box or virtual address, never your home address (`legal.postal_address`).
  *Why:* email law requires a real postal address in marketing mail, and policy pages name who is behind the service.
- [ ] **Attorney review of each policy.** Terms, Privacy, Cookie Notice, Refunds, Shipping, Accessibility, for every app.
  *Why:* these are templates. Only a lawyer can say they fit your app and your state. Set `legal.reviewed: true` only after this.
- [ ] **Texas sales tax permit and Stripe Tax.** Ask the Texas Comptroller whether your product is taxable; if so register for a
  permit, and turn on Stripe Tax to calculate and report it.
  *Why:* collecting or failing to collect sales tax wrongly creates a bill you owe personally.
- [ ] **PCI DSS SAQ-A by using hosted Stripe Checkout only.** Never let card numbers touch your pages, servers or logs; send buyers to Stripe's hosted page.
  *Why:* this keeps the card-security paperwork to the shortest self-assessment (SAQ-A). Handling cards yourself is a much heavier burden.
- [ ] **CAN-SPAM for any email you send.** Unsubscribe link that works, your postal address, honest subject lines; keep transactional mail
  (receipts, password resets) separate from marketing.
  *Why:* fines apply per email. The Phase 2 templates are transactional only.
- [ ] **COPPA screening per app.** For each app ask: is it aimed at, or likely to attract, children under 13? If yes, stop and get advice before collecting any data.
  *Why:* collecting data from children under 13 needs verifiable parental consent.
- [ ] **Record retention.** Decide how long to keep invoices, tax records, contact messages and backups, and write it into `legal.retention`.
  *Why:* the privacy policy must say how long data is kept, and tax records usually must be kept for years while personal data should not be kept forever.

## Your hosting setup

- [ ] **Internet provider terms.** Read your home ISP's terms about running servers and business use, or ask them.
  *Why:* some residential plans forbid hosting; they can cut service with little notice.
- [ ] **Cloudflare terms for the tunnel and proxy.** Confirm that serving your sites through Cloudflare Tunnel is allowed for your use, including any limits on media or large downloads.
  *Why:* a terms breach can switch your sites off.
- [ ] **Move prod off the home PC at the trigger.** The trigger is your **first real payment or first stored customer record**, whichever comes first.
  *Why:* a home PC, home internet and a single power supply are fine for learning, not for other people's money or data; moving needs a plan (Phase 9 AWS lab is the rehearsal).

## Per-app checks (repeat for each new app)

- [ ] `legal:` block filled in `avk.yaml`; `python -m avk legal render <app> --check` passes with no `[TO COMPLETE]` left.
- [ ] Cookie banner matches the real tools used (`legal.analytics`); nothing optional loads before consent.
- [ ] Contact mailbox answered within the time the pages promise (`support@`, `privacy@`, `security@`).
- [ ] Accessibility gate passes in the release run (serious and critical axe violations stop the release).
- [ ] Account deletion works end to end and erases the app's data (the deletion hook).

*Not legal advice. Review with a qualified attorney or CPA.*

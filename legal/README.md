# Legal pack (a draft, not legal advice)

Shared templates that turn a small `legal:` block in an app's `avk.yaml` into policy pages, a cookie notice, a contact page and
`security.txt`. **None of it is legal advice.** Every page starts with a visible banner "DRAFT – needs review by a qualified
attorney/CPA" until the owner sets `legal.reviewed: true` after professional review.

```powershell
python -m avk legal render <app>            # write the pages into the app repo (safe to repeat)
python -m avk legal render <app> --check    # exit 1 if stale, or if [TO COMPLETE] remains for a live app
python -m avk legal smtp <app> --env stg    # contact-form mail settings into the app's secrets (stg: Mailpit, prod: Resend)
```

## What you get
Pages (`legal/templates/*.html.j2` rendered into `legal.output_dir`): Terms of Service, Privacy Policy, Cookie Notice,
Refunds/Returns/Cancellation (only if `sells_goods` or `subscriptions`), Shipping (only if `physical_goods`), Accessibility Statement,
Contact. Plus `legal.css`, `consent.js`, `/.well-known/security.txt`, the contact acknowledgement mail (rendered from the Phase 2
`support_ack` template with `@@TICKET_ID@@` and `@@FIRST_NAME@@` placeholders) and a footer written between the markers
`<!-- avk-legal-footer:start -->` and `<!-- avk-legal-footer:end -->` of each file in `legal.footer_into`.
The app side (contact form handler, redirects, Sunset headers) is in `app-template/legal-starter/`.

## The `legal:` block
| Key | Meaning |
|---|---|
| `reviewed` | `true` only after attorney/CPA review; removes the DRAFT banner. The owner sets it, nobody else. |
| `app_title`, `legal_name`, `postal_address`, `effective_date` | Names and date shown on every page. Address is a PO box or virtual address, never a home address. |
| `accounts` | The app has sign-in (cookie table and deletion text follow). Default true. |
| `regions` | `tx`, `ca`, `eu`: adds the matching privacy sections. |
| `analytics` | Tools needing consent. Empty = a one-time notice about essential cookies only; otherwise Accept/Reject. |
| `processors` | `cloudflare`, `resend`, `stripe`, `turnstile` are known; any other name renders `[TO COMPLETE]`. |
| `data_collected`, `data_location`, `retention` | What, where, how long. |
| `sells_goods`, `subscriptions`, `physical_goods` | Which extra pages render; refund/shipping text keys `refund_terms`, `return_terms`, `shipping_terms`, `shipping_times`, `payment_processor`, `cancellation_steps`. |
| `turnstile_site_key` | Adds the Cloudflare Turnstile widget to the contact page (the app also needs `TURNSTILE_SECRET`). |
| `output_dir`, `well_known_dir`, `mail_dir`, `base_path`, `footer_into` | Where files go in the app repo. Defaults suit a Spring Boot app. |

Templates never invent facts: any input that is missing renders as a highlighted `[TO COMPLETE: name]` and `avk legal render` lists it.
For an app whose platform status is `live`, `--check` fails while any marker remains.

# Toronto Zoo — permission to archive public bid listings

**Body:** Toronto Zoo (Purchasing & Supply unit)
**Portal:** https://torontozoo.bidsandtenders.ca/
**Granted:** 2026-07-18, by the Toronto Zoo Purchasing & Supply unit (via torontozoo.com/business)
**Gate flipped in:** the commit that adds this file (`config.BIDS_TENDERS_PORTALS` → `toronto-zoo` `enabled: True`).

## What it covers

Read-only, rate-limited archiving of the **publicly visible bid listing metadata** on
torontozoo.bidsandtenders.ca (solicitation number, title, status, dates), for the
toronto-bids public civic archive, with attribution to the Zoo. **Not** covered: user
logins or document downloads — those remain out of scope (the Vendor clickwrap), unchanged
by this grant.

Zoo's conditions (honour these in the implementation):
- **Frequency:** rate-limited, **off-peak** crawling — the proposed nightly pass.
- **Attribution:** open publication with attribution to the Zoo as the data source.
- **Compliance:** immediate cessation of data collection if the Zoo requests it.
- **Point of contact:** the Purchasing & Supply unit will reach out at Alex's email on any
  issue or if the scraping schedule needs adjustment.

## The request that was sent

`docs/letters/2026-07-18-toronto-zoo-portal-permission.md` — read-only, rate-limited
periodic fetch of publicly visible listing metadata; no login, no documents; attribution
offered; immediate cessation on request; the PMMD/Ariba written authorization cited as
precedent.

## The reply, verbatim

> Hello Alex,
>
> Thank you for reaching out and for your team's dedication to maintaining Toronto's civic
> procurement history.
>
> On behalf of the Toronto Zoo's Purchasing & Supply unit, we are pleased to grant
> permission for the toronto-bids project to archive our public bid listing metadata from
> `torontozoo.bidsandtenders.ca`.
>
> We appreciate your alignment with the City's open-by-default policy and your proactive
> approach to managing server traffic. This permission is granted under the parameters you
> outlined:
>
> * Scope: Read-only access restricted to publicly visible metadata (solicitation number,
>   title, status, and dates). No user logins or document downloads.
> * Frequency: Rate-limited, off-peak crawling (i.e., your proposed nightly pass).
> * Compliance: Open publication with attribution, and immediate cessation of data
>   collection if requested by the Zoo.
>
> If our technical or procurement teams note any issues or require adjustments to the
> scraping schedule, we will reach out to you at this email address.
>
> Thank you for your work in keeping our public records accessible.
>
> Best regards,
> Purchasing & Supply
> Toronto Zoo
> torontozoo.com/business

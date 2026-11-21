# Toronto Zoo: permission to archive public bid listings

- **Body:** Toronto Zoo (Purchasing & Supply unit)
- **Portal:** `https://torontozoo.bidsandtenders.ca/`
- **Granted:** 2026-07-18, by the Toronto Zoo Purchasing & Supply unit (`torontozoo.com/business`)
- **Enabled in:** `config.BIDS_TENDERS_PORTALS["toronto-zoo"]`

## Scope and conditions

Covers read-only, rate-limited archiving of publicly visible bid listing metadata on `torontozoo.bidsandtenders.ca` (solicitation number, title, status, dates) for the Toronto Bids public archive. Excludes user logins and bid document downloads.

Conditions:

- **Frequency:** Rate-limited, off-peak crawling (nightly run).
- **Attribution:** Open publication with attribution to the Toronto Zoo.
- **Compliance:** Stop collection immediately if requested by the Zoo.
- **Point of contact:** Purchasing & Supply will contact the maintainer by email if adjustments are needed.

Request sent: [`docs/letters/2026-07-18-toronto-zoo-portal-permission.md`](../letters/2026-07-18-toronto-zoo-portal-permission.md).

## Verbatim reply

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

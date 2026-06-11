![Apavital](https://raw.githubusercontent.com/pant3ras/ha-apavital/main/logo.webp)

# Apavital for Home Assistant

A custom integration that pulls your water account data from **My Apavital**
(`my.apavital.ro`) into Home Assistant: meter index, official readings, account
balance, and unpaid invoices — for every consumption place on your account.

> Unofficial. Not affiliated with or endorsed by Apavital. It uses the same private
> JSON API that the My Apavital web dashboard calls, authenticated with your own
> browser session cookie.

## What you get

**Per consumption place** (one device each):
- **Water meter index** (m³) — `total_increasing`, so it plugs straight into Home
  Assistant's **Water** dashboard. Sourced from the smart-meter feed, falling back to
  the latest official reading.
- **Last official reading** (m³) — with date, reading type, and meter serial.
- **Last reading date**.

**Account** (one device):
- **Balance** (RON).
- **Unpaid invoices** — count, with total due and the invoice list as attributes.

## Authentication — sign in inside Home Assistant

You log in **directly in Home Assistant**, no browser/cookie copying. When you add the
integration:

1. **Credentials** — enter your My Apavital e-mail and password.
2. **One-time code** — pick **SMS** or **e-mail**; Apavital sends you a code.
3. **Enter the code** — type it in. Done.

That login returns a **token valid for ~1 year**, which the integration stores and uses
for all requests. There is **no session to keep alive** — it just works until the token
expires (about a year) or you change your password, at which point Home Assistant
prompts you to sign in again (same quick e-mail → code flow).

Your password is only used during sign-in and is **not stored** — only the long-lived
token is kept (in Home Assistant's config entry, like any integration credential).

## Installation

### Via HACS (custom repository)
1. HACS → ⋮ → **Custom repositories**.
2. Add this repo URL, category **Integration**.
3. Install **Apavital**, then restart Home Assistant.

### Manual
Copy `custom_components/apavital/` into your HA `config/custom_components/` folder and
restart Home Assistant.

## Notes & limitations
- Polls hourly. The data changes hourly/monthly and the token is long-lived, so
  there's no need to poll more often.
- The **Water meter index** is `total_increasing` in m³ — add it to the Water
  dashboard, or wrap it in a `utility_meter` helper for daily/monthly consumption.
- Read-only. The integration never performs account actions, payments, or changes.

## Credits
Brought to you by **PanTeraS**.

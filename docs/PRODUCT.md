# Apt product notes

Source brief: field record for one regional manager, chat-first, visited units only.

## Decisions locked for this build

1. **MariaDB** for the field record and the PoweredByTop security tables. Same `MYSQL_*` pattern as Family OS and NEW. No SQLite.
2. **Users are optional and email is optional.** The owner can add a field login or a viewer (boss / company) with a username and password. Email is stored only when someone types one. Several people can have no email. Bosses still open weekly and company reports on their login. A signed PDF link is a 15-minute handoff, not a substitute for the login.
3. **Weekly and company reports are in this version.** A company report covers jobs went-for vs completed, units visited, notes, miles, gas / food / other, follow-ups, and last week. Saving the report makes it readable by viewers who have reports turned on. Send marks it sent, notifies those logins, and emails only the ones with an address.

## Still open

- Home city stays blank until she sets it. Odessa is not assumed.
- Map pins use OpenStreetMap and Nominatim.
- Auto-email of the weekly report stays off unless SMTP is configured and she sends.
- Expense amounts are always confirmed. The record is for reimbursement notes, not tax filing.

## Out of scope

Multi-company maintenance software, shop PINs, and floor-plan maps of a complex.

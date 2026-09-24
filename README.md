# Apt — apt.poweredby.top

A field record for a regional manager. She plans trips, confirms the property, logs the units she actually visits, files gas and food, and hands her bosses a weekly or company report.

Data lives in MariaDB, same as the other PoweredBy.top apps. A person can be added with a username and password and no email. Bosses open reports when they sign in. If they have an email, a send can use it. If they do not, the report is still there.

```bash
./start_local.sh
```

Open http://127.0.0.1:8075 and create the owner login. The first screen does not ask for an email.

Say: `I'm going to Woodview Odessa Thursday for AC evals`  
Then: `yes, save it`

PoweredByTop wrapper: `SITE_MODE=apt`, cookie `pbt_apt_session`.

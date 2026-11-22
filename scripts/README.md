# scripts/validate.py

Checks the four files the app downloads before you push them:
`cards/corrections.json`, `lounges/corrections.json`, `status/benefits.json`, `app/data.json`.

## Run it before every "push data"

```
python3 scripts/validate.py
```

- Needs only Python 3. No packages to install.
- It looks for the app repo at `../stackjet-app` (next to this repo). Use `--app-repo PATH` if it is somewhere else.
  Without the app repo it still runs, but skips a few checks (it prints a WARN for each one).
- It compares with `origin/main` for the "pick diff". Use `--base <ref>` to compare with something else.
  Run `git fetch` first so `origin/main` is the version people have now.
- `--quiet` hides the INFO lines.

## How to read the result

- **ERROR**: the app would ignore the file, drop a section or entry, show something wrong,
  or a Free/Pro rule is broken. Fix it before you push. The script exits with 1.
- **WARN**: a person should look at it. You can still push.
- Lines that start with **PICKS** list cards and programs whose change moves a Free user's
  3 picks (a card's `programIDs` or status grants changed, or a program became or stopped
  being `cashback`). Check each one is meant.
- The **notice** banner text is always printed: check it does not promise a Pro feature to Free users.

## What it checks

- All files: valid JSON, no duplicate keys, size limit, `format`, `version: 1`, `updated` (yyyy-MM-dd;
  WARN if the content changed but `updated` did not).
- Free/Pro guard: no key like `pro`, `isPro`, `premium`, `locked`, `plan`, `free`, `entitlement`, `paywall`
  anywhere. Only `profile.badges[].proOnly: true` is allowed.
- Cards: `catalogVersion` not older than the app's card list; every card, benefit, status and earn rule
  has its required fields with the right types; known categories, reset types, benefit and program kinds;
  `credit` benefits have a positive `capMinor`; a `unit` (count-only) credit has a known unit (`esim`), kind `credit`,
  `capMinor` = `passCount` > 0 and no dollar value (no `valueMinor`, `monthOverrides` or `$` in `valueNote`), with a WARN
  that builds older than `unit` read its `capMinor` as money; no duplicate ids; amounts and dates in range; programs known;
  links are HTTPS and on the app's card allowlist; status rank = (tier number) x 10, 0 for the base tier;
  `french` keys are used (WARN if not) and Canadian text has French (WARN).
- Lounges: 3-letter airport codes (WARN if the app's list doesn't have it), known networks, names not empty,
  HTTPS links on the lounge allowlist, hours that look like hours (WARN), ratings.
- Status benefits: `updated` not older than the copy in the app (else the app ignores the file);
  1-12 tiers, 1-20 benefits, `keys` match benefits, `benefitsFR` present with one line per English line;
  tier names match the app's tier list, in order.
- App data: every section with the app's own rules. A section that the app drops whole for one bad entry
  is reported as ERROR on that entry. Status match `dest`/`tier` names must be the app's tier at that rank;
  countries only `US`/`CA`; all links on the right allowlist.

The lists (categories, networks, allowlists, built-in tiers, card fields) are copied from the app's Swift code.
When the app repo is found, the script reads them from the Swift files and prints a WARN if its copy is out of date:
then update the copy at the top of `validate.py` so GitHub Actions uses the new values too.

## GitHub Actions

`.github/workflows/validate.yml` runs the script on every push and pull request (without the app repo).

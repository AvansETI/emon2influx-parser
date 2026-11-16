# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

Python 3.12 service: MQTT (EMON kit P1 telegrams) → parser → InfluxDB 2.x (`dsmr` measurement).
Design: `doc/architecture.md`. Related app (reads the data): `~/p1-meter-app`.

## Language

- Respond to the user in **Dutch**.
- Code, comments, commit messages and documentation in **English**.

## Secrets and privacy

- Never commit secrets. `.env` is git-ignored; `.env.example` lists keys without values.
- Never ask the user to paste a token or password.
- Never write to the production bucket `p1-smartmeters` from development or tests; use a
  separate test bucket and `MODE=dry-run` by default.
- Never store or commit identifiers that lead to a household: EAN/equipment ids, meter
  serials, real signatures or real log files. Test data uses fictitious ids (e.g. `A1B2C3`).

## Ask first

Before `git push`, merges, tags, creating a GitHub repository, deployments, or anything
outside this directory: state what you will do and wait for the user's explicit OK.

## Git

- Commits are authored by the user only. **No `Co-Authored-By` trailer or any other mention
  of Claude** in commits, PRs or tags.
- Conventional Commits.

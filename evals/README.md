# Golden-set evals

End-to-end quality checks for `health-ai-service`. A curated set of cases, each
with a message + expected intent / risk / content invariants. The runner fires
each case as `POST /v1/chat` against a live instance.

## Files
- `cases.json` — 50 cases across 7 rubrics: emergency, symptom_check,
  mental_health, off_topic, lifestyle, injection, medication dosage.
- `run_evals.py` — async httpx runner; exit code = number of failures.

## Running locally

```bash
# 1. Start the stack (or point at an existing dev instance)
docker compose up -d

# 2. Run evals
export EVAL_BASE_URL=http://localhost:8001
export EVAL_SERVICE_TOKEN=test-token   # or whatever SERVICE_TOKEN is set to
python evals/run_evals.py

# Exit code is the number of failing cases (0 = perfect).
```

Each iteration calls OpenAI for real — expect costs proportional to the case
count. Not meant for per-PR CI; the GitHub workflow runs it on a weekly cron
and on manual dispatch.

## Assertion shapes

Inside each case's `expected` block (all keys optional):

- `intent_category`: string or list-of-strings; the classifier's category must match one.
- `risk_level`: same shape; matches against `intent.risk_level` in the response.
- `answer_must_contain_any_of`: list of phrases; at least one must appear (case-insensitive) in the answer.
- `answer_must_not_contain`: list of phrases; none may appear in the answer.

Cases with no constraints beyond the 200 status effectively only test that the
pipeline stayed up for the payload.

## Adding a case

1. Pick a rubric (emergency / symptom_check / mental_health / off_topic /
   lifestyle / injection / medication-dosage).
2. Draft a single message and the minimal set of assertions that would fail
   for an obviously bad answer — don't overfit. A good case has one or two
   `answer_must_contain_any_of` phrases plus an `intent_category`.
3. Append to `cases.json` with an id shaped `<rubric>_<NNN>_<short_slug>`.
4. Run locally and confirm baseline pass/fail.

### Rule

**Do not tune the system prompt to make a specific case pass.** If a case
fails, look for a class of problem the case represents and fix the root
cause. The golden set is the canary, not the spec.

## Locale split

Roughly balanced RU/EN. Add Kazakh coverage once the `kk` prompts stabilize.

# Knowledge Base — Content Provenance and Licensing

All articles in this directory are **original adaptations** grounded in publicly available medical materials. No content was copy-pasted from source publications; each article is rewritten in the voice of this project, with claims attributable to the cited source.

## Content policy — excluded topics

Per project content policy, the following clinical material is **intentionally excluded** from this corpus:

- Sexual health, sexually transmitted infections (STIs/STDs), contraception, lifestyle factors framed around intercourse or substance-using behaviour.
- HPV vaccination and cervical screening (Pap test, HPV-DNA).
- In-pregnancy vaccination schedules and pregnancy-specific vaccine framing in adult immunization articles.
- Mammography framing tied to reproductive risk factors.

Neutral mentions of pregnancy in clinical contexts (folate supplementation for those planning pregnancy, GERD as a trigger in late pregnancy, preeclampsia as a red flag) are retained — they are baseline medical content, not sensitive material.

## Sources used

| Source | License | Used for |
|---|---|---|
| **NIH MedlinePlus** (medlineplus.gov) | Public domain (U.S. federal government work) | Headache, back pain, fatigue, common cold, hay fever, flexibility, GERD, eczema, blood pressure, immunization, checkups |
| **NIH Office of Dietary Supplements** (ods.od.nih.gov) | Public domain | Vitamin D, iron, B12, general supplements |
| **NIH National Institute of Mental Health** (nimh.nih.gov) | Public domain | Stress, anxiety |
| **NIH National Center for Complementary and Integrative Health** (nccih.nih.gov) | Public domain | Meditation and mindfulness |
| **NIH NIDDK** (niddk.nih.gov) | Public domain | Type 2 diabetes, GERD |
| **NIH NIAMS** (niams.nih.gov) | Public domain | Atopic dermatitis (eczema) |
| **CDC** (cdc.gov) | Public domain (U.S. federal government work) | Sleep, common cold, pollen, water/hydration, physical activity, stress, hypertension, diabetes prevention, adult immunization schedule, preventive care |
| **USPSTF Recommendations** (uspreventiveservicestaskforce.org) | Public (U.S. federal task force recommendations) | Adult health screenings |
| **WHO fact sheets** (who.int) | CC BY-NC-SA 3.0 IGO | Headache disorders, physical activity guidelines, anxiety, hypertension, diabetes, adult immunization — used as reference; all text in this corpus is adapted, not reproduced |
| **NICE Guideline NG59 Low back pain and sciatica** (nice.org.uk) | Open Government Licence v3.0 | Back pain (UK clinical guidance) — used as reference |
| **EFSA Scientific Opinions** (efsa.europa.eu) | EU public sector content | Dietary reference values for water, vitamin D |
| **AASM Clinical Practice Guideline for Chronic Insomnia** | Referenced only for technique descriptions (CBT-I, sleep restriction); no copyrighted text used |

## Content attribution table

Every `source_id` below corresponds to one file in `articles/<lang>/` and one entry in `manifest.json`. The corpus covers 21 topics × 3 locales = 63 articles. Machine-translated Kazakh articles are flagged in the manifest with `review_status: "machine_translated"` pending human review by a native speaker.

| source_id | Language | Topic | Primary source |
|---|---|---|---|
| ru-headache | ru | symptoms | WHO Headache disorders + NIH MedlinePlus |
| ru-back-pain | ru | symptoms | NICE NG59 + NIH MedlinePlus |
| ru-fatigue | ru | symptoms | NIH MedlinePlus + CDC |
| ru-insomnia | ru | sleep | CDC + AASM |
| ru-vitamin-d | ru | nutrition | NIH ODS + EFSA |
| ru-iron-b12 | ru | nutrition | NIH ODS + CDC |
| ru-hydration | ru | nutrition | EFSA + CDC |
| ru-cardio-basics | ru | activity | WHO + CDC |
| ru-strength-training | ru | activity | WHO + CDC |
| ru-stretching | ru | activity | NIH MedlinePlus + CDC |
| ru-stress | ru | mental-health | NIH NIMH + CDC |
| ru-anxiety | ru | mental-health | NIH NIMH + WHO |
| ru-meditation | ru | mental-health | NIH NCCIH + CDC |
| ru-seasonal-cold | ru | seasonal | CDC + NIH MedlinePlus |
| ru-seasonal-allergies | ru | seasonal | CDC + NIH MedlinePlus |
| ru-hypertension | ru | cardiovascular | WHO Hypertension + NIH MedlinePlus + CDC |
| ru-diabetes-prevention | ru | diabetes | CDC DPP + NIH NIDDK + WHO |
| ru-heartburn | ru | digestive | NIH NIDDK + NIH MedlinePlus |
| ru-eczema | ru | skin | NIH NIAMS + NIH MedlinePlus |
| ru-adult-vaccinations | ru | vaccination | CDC Adult Immunization + WHO |
| ru-adult-screenings | ru | screening | CDC Preventive Care + USPSTF |
| en-headache | en | symptoms | WHO Headache disorders + NIH MedlinePlus |
| en-back-pain | en | symptoms | NICE NG59 + NIH MedlinePlus |
| en-fatigue | en | symptoms | NIH MedlinePlus + CDC |
| en-insomnia | en | sleep | CDC + AASM |
| en-vitamin-d | en | nutrition | NIH ODS + EFSA |
| en-iron-b12 | en | nutrition | NIH ODS + CDC |
| en-hydration | en | nutrition | EFSA + CDC |
| en-cardio-basics | en | activity | WHO + CDC |
| en-strength-training | en | activity | WHO + CDC |
| en-stretching | en | activity | NIH MedlinePlus + CDC |
| en-stress | en | mental-health | NIH NIMH + CDC |
| en-anxiety | en | mental-health | NIH NIMH + WHO |
| en-meditation | en | mental-health | NIH NCCIH + CDC |
| en-seasonal-cold | en | seasonal | CDC + NIH MedlinePlus |
| en-seasonal-allergies | en | seasonal | CDC + NIH MedlinePlus |
| en-hypertension | en | cardiovascular | WHO Hypertension + NIH MedlinePlus + CDC |
| en-diabetes-prevention | en | diabetes | CDC DPP + NIH NIDDK + WHO |
| en-heartburn | en | digestive | NIH NIDDK + NIH MedlinePlus |
| en-eczema | en | skin | NIH NIAMS + NIH MedlinePlus |
| en-adult-vaccinations | en | vaccination | CDC Adult Immunization + WHO |
| en-adult-screenings | en | screening | CDC Preventive Care + USPSTF |
| kk-headache | kk | symptoms | Translated from ru-headache |
| kk-back-pain | kk | symptoms | Translated from ru-back-pain |
| kk-fatigue | kk | symptoms | Translated from ru-fatigue |
| kk-insomnia | kk | sleep | Translated from ru-insomnia |
| kk-vitamin-d | kk | nutrition | Translated from ru-vitamin-d (NIH ODS + EFSA) |
| kk-iron-b12 | kk | nutrition | Translated from en-iron-b12 (NIH ODS + CDC) |
| kk-hydration | kk | nutrition | Translated from ru-hydration |
| kk-cardio-basics | kk | activity | Translated from ru-cardio-basics |
| kk-strength-training | kk | activity | Translated from ru-strength-training |
| kk-stretching | kk | activity | Translated from ru-stretching |
| kk-stress | kk | mental-health | Translated from ru-stress |
| kk-anxiety | kk | mental-health | Translated from ru-anxiety |
| kk-meditation | kk | mental-health | Translated from en-meditation |
| kk-seasonal-cold | kk | seasonal | Translated from ru-seasonal-cold |
| kk-seasonal-allergies | kk | seasonal | Translated from en-seasonal-allergies |
| kk-hypertension | kk | cardiovascular | Translated from ru-hypertension |
| kk-diabetes-prevention | kk | diabetes | Translated from ru-diabetes-prevention |
| kk-heartburn | kk | digestive | Translated from ru-heartburn |
| kk-eczema | kk | skin | Translated from ru-eczema |
| kk-adult-vaccinations | kk | vaccination | Translated from ru-adult-vaccinations (sanitised) |
| kk-adult-screenings | kk | screening | Translated from ru-adult-screenings (sanitised) |

### Kazakh review status

All 21 `kk-*` articles carry `review_status: "machine_translated"` in `manifest.json`. They have been pivoted from the Russian (or English, where Russian was unavailable) version and cross-checked against the underlying CDC/NIH/WHO source for clinical terminology, but they have **not** yet been validated by a native Kazakh speaker with medical familiarity. Medical content benefits from a native reviewer before landing in the RAG corpus — machine translation without review has created hard-to-catch drift on subtle clinical terminology in previous rounds. When a kk article is fully validated, flip `review_status` to `"reviewed"` in the manifest.

## Usage notes

- Every article in this corpus ends with a plain-language source attribution line.
- The `attribution` block is carried into Qdrant as part of each chunk's metadata, so any RAG retrieval surface can cite the source.
- WHO's CC BY-NC-SA license is respected by **adapting** (not reproducing) their fact sheets; rewritten prose is the authors' own.
- Medical content is advisory only and is not a substitute for clinical judgment. Downstream consumers (chat answers, summaries) surface explicit "consult your doctor" disclaimers via the content filter.
- Corrections and additions are welcome — update both the article file and its manifest entry in the same change.

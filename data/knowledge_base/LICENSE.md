# Knowledge Base — Content Provenance and Licensing

All articles in this directory are **original adaptations** grounded in publicly available medical materials. No content was copy-pasted from source publications; each article is rewritten in the voice of this project, with claims attributable to the cited source.

## Sources used

| Source | License | Used for |
|---|---|---|
| **NIH MedlinePlus** (medlineplus.gov) | Public domain (U.S. federal government work) | Headache, back pain, fatigue, common cold, hay fever, flexibility |
| **NIH Office of Dietary Supplements** (ods.od.nih.gov) | Public domain | Vitamin D, iron, B12, general supplements |
| **NIH National Institute of Mental Health** (nimh.nih.gov) | Public domain | Stress, anxiety |
| **NIH National Center for Complementary and Integrative Health** (nccih.nih.gov) | Public domain | Meditation and mindfulness |
| **CDC** (cdc.gov) | Public domain (U.S. federal government work) | Sleep, common cold, pollen, water/hydration, physical activity, stress |
| **WHO fact sheets** (who.int) | CC BY-NC-SA 3.0 IGO | Headache disorders, physical activity guidelines, anxiety — used as reference; all text in this corpus is adapted, not reproduced |
| **NICE Guideline NG59 Low back pain and sciatica** (nice.org.uk) | Open Government Licence v3.0 | Back pain (UK clinical guidance) — used as reference |
| **EFSA Scientific Opinions** (efsa.europa.eu) | EU public sector content | Dietary reference values for water, vitamin D |
| **AASM Clinical Practice Guideline for Chronic Insomnia** | Referenced only for technique descriptions (CBT-I, sleep restriction); no copyrighted text used |

## Content attribution table

Every `source_id` below corresponds to one file in `articles/<lang>/` and one entry in `manifest.json`. Machine-translated Kazakh articles are flagged in the manifest with `review_status: "machine_translated"` pending human review.

| source_id | Language | Topic | Primary source |
|---|---|---|---|
| ru-headache | ru | symptoms | WHO Headache disorders + NIH MedlinePlus |
| ru-back-pain | ru | symptoms | NICE NG59 + NIH MedlinePlus |
| ru-fatigue | ru | symptoms | NIH MedlinePlus + CDC |
| ru-insomnia | ru | sleep | CDC + AASM |
| ru-vitamin-d | ru | nutrition | NIH ODS + EFSA |
| ru-hydration | ru | nutrition | EFSA + CDC |
| ru-cardio-basics | ru | activity | WHO + CDC |
| ru-stress | ru | mental-health | NIH NIMH + CDC |
| ru-anxiety | ru | mental-health | NIH NIMH + WHO |
| ru-seasonal-cold | ru | seasonal | CDC + NIH MedlinePlus |
| en-back-pain | en | symptoms | NICE NG59 + NIH MedlinePlus |
| en-fatigue | en | symptoms | NIH MedlinePlus + CDC |
| en-insomnia | en | sleep | CDC + AASM |
| en-iron-b12 | en | nutrition | NIH ODS + CDC |
| en-hydration | en | nutrition | EFSA + CDC |
| en-strength-training | en | activity | WHO + CDC |
| en-stretching | en | activity | NIH MedlinePlus + CDC |
| en-anxiety | en | mental-health | NIH NIMH + WHO |
| en-meditation | en | mental-health | NIH NCCIH + CDC |
| en-seasonal-allergies | en | seasonal | CDC + NIH MedlinePlus |
| kk-headache | kk | symptoms | Translated from ru-headache |
| kk-back-pain | kk | symptoms | Translated from ru-back-pain |
| kk-fatigue | kk | symptoms | Translated from ru-fatigue |
| kk-insomnia | kk | sleep | Translated from ru-insomnia |
| kk-vitamins | kk | nutrition | Adapted from multiple NIH ODS fact sheets |
| kk-hydration | kk | nutrition | Translated from ru-hydration |
| kk-stress | kk | mental-health | Translated from ru-stress |
| kk-meditation | kk | mental-health | Translated from en-meditation |
| kk-seasonal-cold | kk | seasonal | Translated from ru-seasonal-cold |
| kk-allergies | kk | seasonal | Translated from en-seasonal-allergies |

## Usage notes

- Every article in this corpus ends with a plain-language source attribution line.
- The `attribution` block is carried into Qdrant as part of each chunk's metadata, so any RAG retrieval surface can cite the source.
- WHO's CC BY-NC-SA license is respected by **adapting** (not reproducing) their fact sheets; rewritten prose is the authors' own.
- Medical content is advisory only and is not a substitute for clinical judgment. Downstream consumers (chat answers, summaries) surface explicit "consult your doctor" disclaimers via the content filter.
- Corrections and additions are welcome — update both the article file and its manifest entry in the same change.

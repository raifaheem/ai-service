from pydantic import BaseModel, ConfigDict, Field


class ArticleAnalyzeRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500, description="Article title (1–500 chars).")
    text: str = Field(
        ...,
        min_length=200,
        max_length=200_000,
        description="Full article text (200–200000 chars). Longer inputs are rejected to bound LLM cost.",
    )
    language: str = Field(default="ru", max_length=8, description="Article language: 'ru', 'en', or 'kk'.")
    source_id: str | None = Field(
        default=None,
        max_length=200,
        description="Optional external source identifier. When omitted, a UUID-based id is generated.",
    )
    index_chunks: bool = Field(
        default=True,
        description="When true, the article is chunked and upserted into Qdrant for later RAG retrieval.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Headache management — clinical guidelines",
                    "text": (
                        "Primary headaches include migraine, tension-type headache, and cluster "
                        "headache. Most adults experience at least one episode per year, and "
                        "self-care with over-the-counter analgesics resolves the majority of "
                        "cases. Red flags warranting urgent evaluation: sudden thunderclap "
                        "onset, new headache after age 50, fever with neck stiffness, or focal "
                        "neurological deficits. Clinicians should ask about triggers, medication "
                        "overuse, and sleep patterns before prescribing prophylaxis."
                    ),
                    "language": "en",
                    "source_id": "who-headache-2024",
                    "index_chunks": True,
                }
            ]
        }
    )


class ArticleAnalysisResponse(BaseModel):
    source_id: str = Field(..., description="Final source id (provided or generated).")
    title: str = Field(..., description="Article title, as provided or derived from filename.")
    language: str = Field(..., description="Article language.")
    indexed_chunks: int = Field(
        default=0, description="Number of chunks written to Qdrant (0 when `index_chunks=false`)."
    )
    extracted_chars: int = Field(default=0, description="Length of the cleaned article text in characters.")
    summary: str = Field(..., description="LLM-generated summary of the article.")
    key_findings: list[str] = Field(..., description="Bullet-list of clinically relevant findings.")
    limitations: list[str] = Field(..., description="Methodological or scope limitations noted in the article.")
    practical_meaning: list[str] = Field(..., description="Takeaways written in plain language for patients.")
    red_flags: list[str] = Field(..., description="Warning signs the article calls out.")
    confidence: str = Field(..., description="Model-reported confidence label ('low', 'medium', 'high').")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "source_id": "who-headache-2024",
                    "title": "Headache management — clinical guidelines",
                    "language": "en",
                    "indexed_chunks": 12,
                    "extracted_chars": 18450,
                    "summary": "Overview of primary and secondary headache disorders with diagnostic criteria and first-line therapies.",
                    "key_findings": [
                        "Triptans remain first-line for acute migraine",
                        "Tension-type headache often responds to NSAIDs",
                    ],
                    "limitations": ["Limited evidence for paediatric populations"],
                    "practical_meaning": ["Track frequency and triggers", "Seek urgent care for 'thunderclap' onset"],
                    "red_flags": ["Sudden, severe headache", "Fever + neck stiffness", "New neurological deficit"],
                    "confidence": "high",
                }
            ]
        }
    )

from pydantic import BaseModel, Field
from typing import Optional, List


class ArticleAnalyzeRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    text: str = Field(..., min_length=200)
    language: str = Field(default="ru")
    source_id: Optional[str] = None
    index_chunks: bool = True


class ArticleAnalysisResponse(BaseModel):
    source_id: str
    title: str
    language: str
    indexed_chunks: int = 0
    extracted_chars: int = 0
    summary: str
    key_findings: List[str]
    limitations: List[str]
    practical_meaning: List[str]
    red_flags: List[str]
    confidence: str
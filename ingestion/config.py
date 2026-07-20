"""
Ingestion service configuration.

Completely independent from app/config.py — reads .env directly.
This means ingestion can be moved to a separate repo by just changing env_file.
"""
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.config import ENV_FILE

class IngestionSettings(BaseSettings):

    model_config = {
        "env_file" : str(ENV_FILE),
        "extra": "ignore",  # Ignore vars from main app (DB_*, REDIS_*, etc.)
        "case_sensitive": True,
    }
    # ------------------------------------------------------------------ #
    # Database  (duplicated from app/config.py — intentional, see plan)   #
    # ------------------------------------------------------------------ #
    DATABASE_URL: str
    MYSQL_HOST : str
    MYSQL_USER: str
    MYSQL_PASSWORD: str
    MYSQL_PORT: str
    MYSQL_DATABASE: str
    # ------------------------------------------------------------------ #
    # Elasticsearch                                                         #
    # ------------------------------------------------------------------ #
    ELASTICSEARCH_INDEX_NAME: str 
    ELASTICSEARCH_USERNAME: str
    ELASTICSEARCH_PASSWORD: str 
    ELASTICSEARCH_SCHEME : str
    ELASTICSEARCH_HOST : str
    ELASTICSEARCH_PORT: str
    # ------------------------------------------------------------------ #
    # Redis                                                                #
    # ------------------------------------------------------------------ #
    REDIS_URL: str 

    # ------------------------------------------------------------------ #
    # Storage                                                              #
    # ------------------------------------------------------------------ #
    STORAGE_BACKEND: str           # "local" | "minio"
    LOCAL_STORAGE_BASE_DIR: str 

    MINIO_ENDPOINT: str 
    MINIO_ACCESS_KEY: str 
    MINIO_SECRET_KEY: str 
    MINIO_BUCKET: str 
    MINIO_SECURE: bool 

    # ------------------------------------------------------------------ #
    # Ingestion                                                            #
    # ------------------------------------------------------------------ #
    MAX_UPLOAD_FILE_SIZE_MB: int 
    INGESTION_VERSION: str 

    # ------------------------------------------------------------------ #
    # OCR                                                                  #
    # ------------------------------------------------------------------ #
    DOTS_OCR_API_BASE: str           # vLLM endpoint; empty = skip dots.ocr
    OLLAMA_BASE_URL: str 
    GEMMA_OCR_MODEL: str 
    
    # ------------------------------------------------------------------ #
    # Celery                                                               #
    # ------------------------------------------------------------------ #
    CELERY_BROKER_URL: str          # defaults to REDIS_URL at runtime
    CELERY_RESULT_BACKEND: str       # defaults to REDIS_URL at runtime
    CELERY_WORKER_CONCURRENCY: int    # one GPU-heavy task at a time

    # ------------------------------------------------------------------ #
    # Chunking                                                             #
    # ------------------------------------------------------------------ #
    CHUNKING_STRATEGY: str        # auto|heading|semantic|sentence|fixed|hierarchical
    CHUNK_MIN_TOKENS: int 
    CHUNK_MAX_TOKENS: int 
    CHUNK_OVERLAP_TOKENS: int 

    # ------------------------------------------------------------------ #
    # Quality / Dedup                                                       #
    # ------------------------------------------------------------------ #
    DEDUP_SIMILARITY_THRESHOLD: float
    MIN_CHUNK_WORD_COUNT: int 
    LANGUAGE_DETECTION_CONFIDENCE_THRESHOLD: float

    # ------------------------------------------------------------------ #
    # Retriever (main app toggle — read here so ingestion can pass it)    #
    # ------------------------------------------------------------------ #
    USE_RRF: bool                  # toggle ES RRF hybrid search

    # ------------------------------------------------------------------ #
    # Service                                                              #
    # ------------------------------------------------------------------ #
    INGESTION_HOST: str 
    INGESTION_PORT: int 
    DEBUG: bool 

    # # ── Local Model Paths ─────────────────────────────────────────────────────
    # MODELS_DIR: Path = Path(__file__).parent.parent / "models"
    
    # # Embedding
    # BGE_M3_MODEL_PATH: str | None = None
    # SENTENCE_TRANSFORMER_PATH: str | None = None
    
    # # Docling
    # DOCLING_MODEL_PATH: str | None = None
    
    # # DeepDoc (OCR + Layout)
    # DEEPDOC_MODEL_PATH: str | None = None
    
    # # dots.ocr (served via vLLM)
    # DOTS_OCR_MODEL_PATH: str | None = None
    # DOTS_OCR_VLLM_URL: str = "http://localhost:8000/v1"
    
    # # Gemma OCR (via Ollama)
    # OLLAMA_BASE_URL: str = "http://localhost:11434"
    # GEMMA_OCR_MODEL: str = "gemma2:9b"
    
    # USE_LOCAL_MODELS: bool = True
    
    # def __init__(self, **kwargs):
    #     super().__init__(**kwargs)
        
    #     if self.USE_LOCAL_MODELS:
    #         # BGE-M3
    #         if self.BGE_M3_MODEL_PATH is None:
    #             local = self.MODELS_DIR / "bge-m3"
    #             if local.exists():
    #                 self.BGE_M3_MODEL_PATH = str(local)
            
    #         # Sentence Transformer
    #         if self.SENTENCE_TRANSFORMER_PATH is None:
    #             local = self.MODELS_DIR / "all-MiniLM-L6-v2"
    #             if local.exists():
    #                 self.SENTENCE_TRANSFORMER_PATH = str(local)
            
    #         # Docling
    #         if self.DOCLING_MODEL_PATH is None:
    #             local = self.MODELS_DIR / "docling"
    #             if local.exists():
    #                 self.DOCLING_MODEL_PATH = str(local)
            
    #         # DeepDoc
    #         if self.DEEPDOC_MODEL_PATH is None:
    #             local = self.MODELS_DIR / "deepdoc"
    #             if local.exists():
    #                 self.DEEPDOC_MODEL_PATH = str(local)
            
    #         # dots.ocr
    #         if self.DOTS_OCR_MODEL_PATH is None:
    #             local = self.MODELS_DIR / "dots-ocr-2.0"
    #             if local.exists():
    #                 self.DOTS_OCR_MODEL_PATH = str(local)

    @property
    def elasticsearch_url(self) -> str:
        return f"{self.ELASTICSEARCH_SCHEME}://{self.ELASTICSEARCH_HOST}:{self.ELASTICSEARCH_PORT}"

    @property
    def database_url(self) -> str:
        return f"mysql+asyncmy://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"

from pprint import pprint

@lru_cache(maxsize=1)
def get_settings() -> IngestionSettings:
    settings_obj = IngestionSettings()
    
    # # ---- TEMPORARY DEBUGGING ----
    # print("--- GENERATED DATABASE URL ---")
    # pprint(settings_obj.database_url) 
    # print("----------------------------")
    
    return settings_obj


# Module-level singleton — import this everywhere inside ingestion/
settings = get_settings()
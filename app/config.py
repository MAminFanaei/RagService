from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "RAG Service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "production"
    INDEX_THE_DOCS : bool = True
    LLM_TURNED_ON : bool = True
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Database (MYSQL)
    DATABASE_URL: str
    MYSQL_ROOT_PASSWORD: str 
    MYSQL_DATABASE: str 
    MYSQL_USER: str 
    MYSQL_PASSWORD: str 
    
    
    # Redis
    REDIS_URL: str
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int 
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0
    
    # Elasticsearch
    ELASTICSEARCH_SCHEME: str = "http"
    ELASTICSEARCH_HOST: str = "localhost"
    ELASTICSEARCH_PORT: int = 9200
    ELASTICSEARCH_USERNAME: str = "elastic"
    ELASTICSEARCH_PASSWORD: str
    ELASTICSEARCH_INDEX_NAME: str = "rag_documents"
    
    # OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""
    
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_REDIRECT_URI: str = ""
    
    # LLM
    LLM_API_KEY: str
    LLM_BASE_URL : str
    QUERY_ENHANCER_MODEL_NAME : str
    ANSWER_GENERATOR_MODEL_NAME : str
    
    # RAG Configuration
    EMBEDDING_MODEL_PATH: str 
    RERANKER_MODEL_PATH: str 
    USE_RERANKER : bool = False
    RETRIEVER_OUTPUT_K : int
    DOC_PATH : str = "./docs/main/"
    CHUNK_TOKENS: int 
    CHUNK_OVERLAP: int 
    MIN_CHUNK_LENGTH: int 
    ENHANCER_OUTPUT_TOKEN: int 
    ANSWER_LLM_OUTPUT_TOKEN: int 
    GENERATOR_THINKING_BUDGET: int 
    ENHANCER_THINKING_BUDGET: int 
    
    # Rate Limiting
    DEFAULT_RATE_LIMIT_PER_MINUTE: int 
    DEFAULT_RATE_LIMIT_LOGIN_PER_MINUTE: int 
    DEFAULT_MAX_MESSAGES_PER_DAY: int 
    
    # CORS
    CORS_ORIGINS: List[str] 
    CORS_ALLOW_CREDENTIALS: bool = True
    
    # Admin
    ADMIN_EMAIL: str 
    ADMIN_PASSWORD: str
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    @property
    def elasticsearch_url(self) -> str:
        return f"{self.ELASTICSEARCH_SCHEME}://{self.ELASTICSEARCH_HOST}:{self.ELASTICSEARCH_PORT}"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
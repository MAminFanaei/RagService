from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "RAG Service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    DB_ECHO : bool = False
    ENVIRONMENT: str = "production"
    INDEX_THE_DOCS : bool = True
    LLM_TURNED_ON : bool = True
    USE_RERANKER : bool 
    USE_BM25: bool 
    DEVICE : str
    
    # Server
    HOST: str 
    PORT: int = 8000
    ELASTIC_SEARCH_WORKERS: int
    PASSWORD_HASH_WORKERS: int
    CPU_BOUNDED_WORKERS: int
    
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str 
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Database (MYSQL)
    MYSQL_ROOT_PASSWORD: str 
    MYSQL_DATABASE: str 
    MYSQL_USER: str 
    MYSQL_PASSWORD: str 
    MYSQL_PORT : str
    DATABASE_URL : str
    
    # Redis
    REDIS_HOST: str 
    REDIS_PORT: int 
    REDIS_PASSWORD: str = ""
    REDIS_DB: int
    REDIS_URL : str

    
    # Elasticsearch
    ELASTICSEARCH_SCHEME: str 
    ELASTICSEARCH_HOST: str 
    ELASTICSEARCH_PORT: int 
    ELASTICSEARCH_USERNAME: str 
    ELASTICSEARCH_PASSWORD: str
    ELASTICSEARCH_INDEX_NAME: str 
    
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
    RETRIEVER_OUTPUT_K : int
    DOC_PATH : str 
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

    # CONVERSATION MEMORY SETTINGS
    # Memory behavior
    ENABLE_CONVERSATION_MEMORY: bool = True
    # How many previous messages to include in context
    MEMORY_MAX_MESSAGES: int = 10  # 5 turns (user + assistant each)
    # Max tokens for conversation history (approximate)
    MEMORY_MAX_TOKENS: int = 2000
    # Use Redis for caching active conversations
    MAX_QUESTION_LENGTH: int = 5000  

    # ASYNC & TIMEOUT SETTINGS
    LLM_TIMEOUT_SECONDS: int = 100
    RETRIEVAL_TIMEOUT_SECONDS: int = 30
    TOTAL_QUERY_TIMEOUT_SECONDS: int = 120
    MAX_CONCURRENT_QUERIES: int = 10
    
    # DATABASE POOL SETTINGS
    DB_POOL_SIZE: int 
    DB_MAX_OVERFLOW: int 
    DB_ECHO: bool 
    REDIS_MAX_CONNECTIONS: int 

    # CORS
    CORS_ORIGINS: List[str] 
    CORS_ALLOW_CREDENTIALS: bool 
    
    # Admin
    ADMIN_EMAIL: str 
    ADMIN_PASSWORD: str
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    ENABLE_REGISTRATION: bool = True
    ENABLE_OAUTH_LOGIN: bool = True

    @property
    def elasticsearch_url(self) -> str:
        return f"{self.ELASTICSEARCH_SCHEME}://{self.ELASTICSEARCH_HOST}:{self.ELASTICSEARCH_PORT}"
    
    # @property
    # def redis_url(self) -> str:
    #     return f"{self.REDIS_HOST}://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # @property
    # def database_url(self) -> str:
    #     return f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@mysql:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}" 
    

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
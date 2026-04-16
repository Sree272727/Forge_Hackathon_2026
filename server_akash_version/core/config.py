from enum import Enum
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TESTING = "testing"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Intelligent Excel Services"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    DEBUG: bool = Field(default=False)

    # Server
    HOST: str = Field(default="0.0.0.0")
    PORT: int = Field(default=8000)

    # Database
    DATABASE_URL: str = Field(default="postgresql+asyncpg://postgres:postgres@localhost:5432/intellegent-excel")
    DATABASE_ECHO: bool = Field(default=False)
    DATABASE_POOL_SIZE: int = Field(default=10)
    DATABASE_MAX_OVERFLOW: int = Field(default=5)
    DATABASE_POOL_TIMEOUT: int = Field(default=30)
    DATABASE_POOL_RECYCLE: int = Field(default=1800)
    DATABASE_POOL_PRE_PING: bool = Field(default=True)

    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    
    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = Field(default=True)

    # Cache
    CACHE_ENABLED: bool = Field(default=True)
    CACHE_DEFAULT_TTL: int = Field(default=300)  # 5 minutes
    CACHE_MAX_CONNECTIONS: int = Field(default=50)
    CACHE_RETRY_ON_TIMEOUT: bool = Field(default=True)
    CACHE_SOCKET_TIMEOUT: int = Field(default=5)  # Redis socket timeout in seconds

    # Logging
    LOG_LEVEL: LogLevel = LogLevel.INFO
    LOG_FORMAT: str = Field(default="json")  # json or text

    # OpenTelemetry
    OTEL_SERVICE_NAME: str = Field(default="excel-services")
    OTEL_SERVICE_VERSION: str = Field(default="0.1.0")

    # Jaeger Configuration
    JAEGER_ENABLED: bool = Field(default=True)
    JAEGER_LOGS_ENABLED: bool = Field(default=False)  # Disabled: Jaeger all-in-one doesn't support OTLP logs properly
    JAEGER_AGENT_URL: str = Field(default="http://localhost:4318")
    TRACE_SAMPLING_RATE: float = Field(default=1.0, ge=0.0, le=1.0)

    # Prometheus
    ENABLE_METRICS: bool = Field(default=True)
    PROMETHEUS_MULTIPROC_DIR: str = Field(default="/tmp/prometheus_multiproc_dir")  # nosec B108

    # Security Headers
    SECURITY_HEADERS_ENABLED: bool = Field(default=True)
    X_FRAME_OPTIONS: str = Field(default="DENY")  # DENY, SAMEORIGIN, or ALLOW-FROM uri
    HSTS_ENABLED: bool = Field(default=True)
    HSTS_MAX_AGE: int = Field(default=31536000)  # 1 year
    HSTS_INCLUDE_SUBDOMAINS: bool = Field(default=True)
    HSTS_PRELOAD: bool = Field(default=False)
    REFERRER_POLICY: str = Field(default="strict-origin-when-cross-origin")
    CSP_ENABLED: bool = Field(default=True)
    CSP_DISABLE_IN_DEVELOPMENT: bool = Field(default=False)
    CONTENT_SECURITY_POLICY: Optional[str] = Field(
        default=(
            "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
            "font-src 'self' https:; connect-src 'self' https:; media-src 'self'; "
            "object-src 'none'; child-src 'none'; worker-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'self';"
        )
    )
    PERMISSIONS_POLICY: Optional[str] = Field(
        default="geolocation=(), microphone=(), camera=(), payment=(), usb=(), magnetometer=(), gyroscope=(), speaker=()"
    )
    REMOVE_SERVER_HEADER: bool = Field(default=True)

    # CORS Settings
    CORS_ALLOWED_ORIGINS: Optional[List[str]] = Field(default=None)
    CORS_ALLOW_ALL_ORIGINS: bool = Field(default=False)
    CORS_ALLOW_CREDENTIALS: bool = Field(default=True)
    CORS_ALLOW_METHODS: List[str] = Field(default=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
    CORS_ALLOW_HEADERS: List[str] = Field(default=["*"])
    CORS_EXPOSE_HEADERS: List[str] = Field(default=["X-Correlation-ID", "X-Trace-ID"])
    CORS_MAX_AGE: int = Field(default=86400)  # 24 hours

    # JWT Settings
    JWT_SECRET_KEY: str = Field(default="your-super-secret-jwt-key-change-in-production")
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30)
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7)

    # Google OAuth Settings
    GOOGLE_CLIENT_ID: str = Field(default="")
    GOOGLE_CLIENT_SECRET: str = Field(default="")
    GOOGLE_REDIRECT_URI: str = Field(default="http://localhost:8000/api/v1/auth/google/callback")

    # Data source upload settings
    DATA_SOURCE_UPLOAD_DIR: str = Field(default="./uploads/data_sources")
    DATA_SOURCE_MAX_FILE_SIZE_MB: int = Field(default=25)
    DATA_SOURCE_ALLOWED_EXTENSIONS: List[str] = Field(default=[".xlsx", ".xls", ".xlsm", ".csv"])

    # LLM API Keys for Excel Processing Agents
    GOOGLE_GEMINI_API_KEY: str = Field(default="")
    OPENAI_API_KEY: str = Field(default="")

    # Agent Configuration
    AGENT_LLM_PROVIDER: str = Field(default="gemini")  # "gemini" or "openai"
    AGENT_LLM_MODEL: str = Field(default="gemini-1.5-pro")  # or "gpt-4o" for OpenAI
    AGENT_MAX_ITERATIONS: int = Field(default=10)
    AGENT_TIMEOUT_SECONDS: int = Field(default=120)

    # Qdrant Vector Database Configuration
    QDRANT_HOST: str = Field(default="localhost")
    QDRANT_PORT: int = Field(default=6333)
    QDRANT_GRPC_PORT: int = Field(default=6334)
    QDRANT_API_KEY: Optional[str] = Field(default=None)
    QDRANT_PREFER_GRPC: bool = Field(default=False)  # REST API is simpler and sufficient
    QDRANT_TIMEOUT: int = Field(default=30)

    # Vector Embedding Configuration
    EMBEDDING_PROVIDER: str = Field(default="openai")  # "openai" or "sentence-transformers"
    EMBEDDING_MODEL: str = Field(default="text-embedding-3-small")  # OpenAI model
    EMBEDDING_DIMENSION: int = Field(default=1536)  # Matches text-embedding-3-small
    EMBEDDING_BATCH_SIZE: int = Field(default=100)  # OpenAI supports larger batches

    # Knowledge Base Collection Settings
    KNOWLEDGE_COLLECTION_NAME: str = Field(default="excel_knowledge")
    KNOWLEDGE_CHUNK_SIZE: int = Field(default=500)  # Characters per chunk
    KNOWLEDGE_CHUNK_OVERLAP: int = Field(default=50)  # Overlap between chunks

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=True)


settings = Settings()

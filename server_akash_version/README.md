## Technology Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL with AsyncPG
- **Tracing**: OpenTelemetry
- **Logging**: Structlog
- **Testing**: Pytest
- **Containerization**: Docker + Docker Compose

## Quick Start

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- PostgreSQL (for local development)
- Redis (for local development)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd aipal-backend-services
   ```

2. **Install dependencies**
   ```bash
   make install
   ```

3. **Start services with Docker**
   ```bash
   make up
   ```

4. **Or run locally for development**
   ```bash
   make db-up    # Start only database services
   make dev      # Start development server
   ```

### Development Workflow

```bash
# Install dependencies
make install

# Start development server
make dev

# Run tests
make test

# Run tests with coverage
make test-cov

# Format code
make format

# Run linting
make lint

# Run all quality checks
make check

# View logs
make logs
```

## API Documentation

Once the server is running, visit:
- **API Documentation**: http://localhost:8000/docs
- **Alternative Docs**: http://localhost:8000/redoc


## Deployment

### Docker Compose (Recommended)

```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f aipal-backend

# Stop services
docker-compose down
```


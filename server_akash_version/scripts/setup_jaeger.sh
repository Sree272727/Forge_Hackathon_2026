#!/bin/bash
# Setup script for Jaeger integration

set -e

echo "🚀 Setting up Jaeger integration for AIPAL Backend Services"
echo "============================================================"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    exit 1
fi

echo "✅ Docker is running"

# Start Jaeger service
echo "📦 Starting Jaeger service..."
docker-compose up -d jaeger

# Wait for Jaeger to be ready
echo "⏳ Waiting for Jaeger to be ready..."
timeout=60
counter=0

while [ $counter -lt $timeout ]; do
    if curl -f http://localhost:16686 > /dev/null 2>&1; then
        break
    fi
    echo "   Waiting... ($((counter + 1))s)"
    sleep 1
    counter=$((counter + 1))
done

if [ $counter -eq $timeout ]; then
    echo "❌ Jaeger did not start within $timeout seconds"
    exit 1
fi

echo "✅ Jaeger is ready!"

# Display service information
echo "📊 Jaeger Services Status:"
echo "============================================================"
echo "🌐 Jaeger UI:           http://localhost:16686"
echo "📡 HTTP Collector:      http://localhost:14268"
echo "📡 gRPC Collector:      http://localhost:14250" 
echo "📡 OTLP gRPC Receiver:  http://localhost:4317"
echo "📡 OTLP HTTP Receiver:  http://localhost:4318"
echo "📡 Agent UDP (Thrift):  localhost:6831"
echo "📡 Agent UDP (Binary):  localhost:6832"
echo "============================================================"

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file from .env.example..."
    cp .env.example .env
    echo "✅ .env file created. You may want to customize it."
else
    echo "📝 .env file already exists"
fi

# Check if dependencies are installed
echo "🔍 Checking Python dependencies..."
if ! python -c "import opentelemetry" > /dev/null 2>&1; then
    echo "⚠️  OpenTelemetry dependencies not found. Installing..."
    if [ -f "pyproject.toml" ]; then
        # Using uv if available, pip as fallback
        if command -v uv > /dev/null 2>&1; then
            uv sync
        else
            pip install -e .
        fi
    else
        echo "❌ pyproject.toml not found. Please run this script from the project root."
        exit 1
    fi
fi

echo "✅ Dependencies are installed"

echo ""
echo "🎉 Jaeger integration setup complete!"
echo "============================================================"
echo "Next steps:"
echo "1. Visit Jaeger UI: http://localhost:16686"
echo "2. Run the test script: python scripts/test_jaeger.py"
echo "3. Start your application with Jaeger tracing enabled"
echo "4. Check traces in the Jaeger UI"
echo "============================================================"
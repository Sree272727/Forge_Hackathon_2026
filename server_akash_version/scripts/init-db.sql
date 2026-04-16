-- Initialize AIPAL database
CREATE DATABASE IF NOT EXISTS aipal;

-- Create extensions if needed
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";
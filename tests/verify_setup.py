#!/usr/bin/env python3
"""
Verify RAG Service Setup - Run this before starting Docker
"""

import os
import sys
from pathlib import Path
import json

# Colors for terminal output
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
NC = '\033[0m'

def check_env_file():
    """Check if .env file exists and has required variables"""
    print(f"{YELLOW}[1/6] Checking .env file...{NC}")
    
    if not Path(".env").exists():
        print(f"{RED}✗ .env file not found{NC}")
        print(f"{YELLOW}  Run: cp .env.example .env{NC}")
        return False
    
    required_vars = [
        "SECRET_KEY",
        "ADMIN_PASSWORD",
        "GEMINI_API_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "ELASTICSEARCH_PASSWORD"
    ]
    
    with open(".env", "r") as f:
        env_content = f.read()
    
    missing = []
    for var in required_vars:
        if f"{var}=" not in env_content or f"{var}=your-" in env_content or f"{var}=change-" in env_content:
            missing.append(var)
    
    if missing:
        print(f"{RED}✗ Missing or placeholder values for: {', '.join(missing)}{NC}")
        return False
    
    print(f"{GREEN}✓ .env file configured{NC}")
    return True


def check_documents():
    """Check if documents are available"""
    print(f"\n{YELLOW}[2/6] Checking for documents...{NC}")
    
    docs_path = Path("./docs/main/")
    
    if not docs_path.exists():
        print(f"{RED}✗ docs/main/ directory not found{NC}")
        print(f"{YELLOW}  Create it and add your JSON documents{NC}")
        return False
    
    json_files = list(docs_path.glob("*.json"))
    
    if not json_files:
        print(f"{RED}✗ No JSON files found in docs/main/{NC}")
        print(f"{YELLOW}  Add your chunked documents as JSON files{NC}")
        return False
    
    # Validate JSON structure
    total_chunks = 0
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            if not isinstance(data, list):
                print(f"{RED}✗ {json_file.name} is not a list of chunks{NC}")
                return False
            
            # Check first chunk has required fields
            if data and "chunk_text" not in data[0]:
                print(f"{RED}✗ {json_file.name} chunks missing 'chunk_text' field{NC}")
                return False
            
            total_chunks += len(data)
        except json.JSONDecodeError:
            print(f"{RED}✗ {json_file.name} is not valid JSON{NC}")
            return False
    
    print(f"{GREEN}✓ Found {len(json_files)} document(s) with {total_chunks} chunks{NC}")
    return True


def check_models():
    """Check if embedding models are downloaded"""
    print(f"\n{YELLOW}[3/6] Checking for models...{NC}")
    
    models_path = Path("./models/")
    
    if not models_path.exists():
        print(f"{YELLOW}! models/ directory not found (will be created){NC}")
        models_path.mkdir(parents=True, exist_ok=True)
        print(f"{YELLOW}  Models will be downloaded on first run{NC}")
        return True
    
    # Check if any model files exist
    model_files = list(models_path.glob("**/*"))
    if not model_files:
        print(f"{YELLOW}! No models found (will be downloaded on first run){NC}")
        return True
    
    print(f"{GREEN}✓ Models directory exists with {len(model_files)} files{NC}")
    return True


def check_docker():
    """Check if Docker is running"""
    print(f"\n{YELLOW}[4/6] Checking Docker...{NC}")
    
    import subprocess
    
    try:
        result = subprocess.run(
            ["docker", "ps"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            print(f"{RED}✗ Docker is not running{NC}")
            return False
        
        print(f"{GREEN}✓ Docker is running{NC}")
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print(f"{RED}✗ Docker not found or not responding{NC}")
        return False


def check_python_deps():
    """Check if Python dependencies are installed (for local dev)"""
    print(f"\n{YELLOW}[5/6] Checking Python dependencies...{NC}")
    
    required_packages = [
        "fastapi",
        "sqlalchemy",
        "langchain",
        "sentence_transformers"
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)
    
    if missing:
        print(f"{YELLOW}! Missing packages (only needed for local dev): {', '.join(missing)}{NC}")
        print(f"{YELLOW}  Run: pip install -r requirements.txt{NC}")
        return True  # Not critical for Docker deployment
    
    print(f"{GREEN}✓ Python dependencies installed{NC}")
    return True


def check_directory_structure():
    """Check if all required directories exist"""
    print(f"\n{YELLOW}[6/6] Checking directory structure...{NC}")
    
    required_dirs = [
        "app/api/v1",
        "app/core",
        "app/models",
        "app/schemas",
        "app/services",
        "app/middleware",
        "alembic/versions"
    ]
    
    missing = []
    for dir_path in required_dirs:
        if not Path(dir_path).exists():
            missing.append(dir_path)
    
    if missing:
        print(f"{RED}✗ Missing directories: {', '.join(missing)}{NC}")
        return False
    
    print(f"{GREEN}✓ All directories present{NC}")
    return True


def main():
    print(f"{GREEN}{'='*50}{NC}")
    print(f"{GREEN}RAG Service Setup Verification{NC}")
    print(f"{GREEN}{'='*50}{NC}\n")
    
    checks = [
        check_env_file(),
        check_documents(),
        check_models(),
        check_docker(),
        check_python_deps(),
        check_directory_structure()
    ]
    
    passed = sum(checks)
    total = len(checks)
    
    print(f"\n{GREEN}{'='*50}{NC}")
    print(f"{GREEN}Results: {passed}/{total} checks passed{NC}")
    print(f"{GREEN}{'='*50}{NC}\n")
    
    if passed == total:
        print(f"{GREEN}✓ Ready to deploy!{NC}")
        print(f"\n{YELLOW}Next steps:{NC}")
        print("  1. docker-compose up -d")
        print("  2. Wait 30 seconds for Elasticsearch")
        print("  3. docker-compose exec app alembic upgrade head")
        print("  4. ./test_api.sh")
        return 0
    else:
        print(f"{RED}✗ Fix the issues above before deploying{NC}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
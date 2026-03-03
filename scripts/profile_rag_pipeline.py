# profiling/profile_rag_pipeline.py
"""
RAG Pipeline Profiler

Run this to identify exactly where time is spent in your pipeline.
Usage: python -m profiling.profile_rag_pipeline
"""

import asyncio
import time
import statistics
from typing import Dict, List, Any
from concurrent.futures import ThreadPoolExecutor
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

console = Console()

# ============================================================================
# TIMING UTILITIES
# ============================================================================

class Timer:
    """Context manager for timing operations."""
    
    def __init__(self, name: str):
        self.name = name
        self.start = None
        self.end = None
        self.duration_ms = 0
    
    def __enter__(self):
        self.start = time.perf_counter()
        return self
    
    def __exit__(self, *args):
        self.end = time.perf_counter()
        self.duration_ms = (self.end - self.start) * 1000


class AsyncTimer:
    """Async context manager for timing."""
    
    def __init__(self, name: str):
        self.name = name
        self.start = None
        self.duration_ms = 0
    
    async def __aenter__(self):
        self.start = time.perf_counter()
        return self
    
    async def __aexit__(self, *args):
        self.duration_ms = (time.perf_counter() - self.start) * 1000


# ============================================================================
# INDIVIDUAL COMPONENT TESTS
# ============================================================================

async def profile_password_hashing(iterations: int = 5) -> Dict[str, float]:
    """Profile Argon2 password hashing - SUSPECTED BOTTLENECK."""
    from passlib.context import CryptContext
    
    pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
    times = []
    
    console.print("\n[bold yellow]1. PASSWORD HASHING (Argon2)[/bold yellow]")
    console.print("   This is CPU-intensive and blocks the event loop!")
    
    # Test hashing (registration)
    hash_times = []
    for i in range(iterations):
        with Timer("hash") as t:
            hashed = pwd_context.hash(f"testpassword{i}")
        hash_times.append(t.duration_ms)
    
    # Test verification (login)
    verify_times = []
    test_hash = pwd_context.hash("testpassword")
    for i in range(iterations):
        with Timer("verify") as t:
            pwd_context.verify("testpassword", test_hash)
        verify_times.append(t.duration_ms)
    
    results = {
        "hash_avg_ms": statistics.mean(hash_times),
        "hash_p95_ms": sorted(hash_times)[int(len(hash_times) * 0.95)] if len(hash_times) > 1 else hash_times[0],
        "verify_avg_ms": statistics.mean(verify_times),
        "verify_p95_ms": sorted(verify_times)[int(len(verify_times) * 0.95)] if len(verify_times) > 1 else verify_times[0],
    }
    
    console.print(f"   Hash avg: [red]{results['hash_avg_ms']:.1f}ms[/red]")
    console.print(f"   Verify avg: [red]{results['verify_avg_ms']:.1f}ms[/red]")
    
    if results['verify_avg_ms'] > 100:
        console.print("   [bold red]⚠️  BLOCKING! This runs on EVERY login![/bold red]")
    
    return results


async def profile_embedding_generation(iterations: int = 5) -> Dict[str, float]:
    """Profile HuggingFace embedding generation."""
    from app.config import settings
    
    console.print("\n[bold yellow]2. EMBEDDING GENERATION[/bold yellow]")
    
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        
        with Timer("model_load") as t:
            embeddings = HuggingFaceEmbeddings(
                model_name=settings.EMBEDDING_MODEL_PATH,
                model_kwargs={
                    "device": settings.DEVICE,
                    "local_files_only": True,
                    "trust_remote_code": True,
                },
                encode_kwargs={'normalize_embeddings': True}
            )
        load_time = t.duration_ms
        console.print(f"   Model load: {load_time:.1f}ms")
        
        # Test single embedding
        single_times = []
        test_text = "What is the capital of France and how does the government work?"
        
        for i in range(iterations):
            with Timer("embed") as t:
                _ = embeddings.embed_query(test_text)
            single_times.append(t.duration_ms)
        
        # Test batch embedding
        batch_times = []
        batch_texts = [test_text] * 10
        
        for i in range(iterations):
            with Timer("batch") as t:
                _ = embeddings.embed_documents(batch_texts)
            batch_times.append(t.duration_ms)
        
        results = {
            "model_load_ms": load_time,
            "single_embed_avg_ms": statistics.mean(single_times),
            "batch_10_avg_ms": statistics.mean(batch_times),
            "per_doc_in_batch_ms": statistics.mean(batch_times) / 10,
        }
        
        console.print(f"   Single embed avg: [cyan]{results['single_embed_avg_ms']:.1f}ms[/cyan]")
        console.print(f"   Batch(10) avg: [cyan]{results['batch_10_avg_ms']:.1f}ms[/cyan]")
        console.print(f"   Device: {settings.DEVICE}")
        
        if results['single_embed_avg_ms'] > 50:
            console.print("   [yellow]⚠️  Slow embedding - consider GPU or caching[/yellow]")
        
        return results
        
    except Exception as e:
        console.print(f"   [red]Error: {e}[/red]")
        return {"error": str(e)}


async def profile_elasticsearch(iterations: int = 5) -> Dict[str, float]:
    """Profile Elasticsearch operations."""
    from app.config import settings
    
    console.print("\n[bold yellow]3. ELASTICSEARCH[/bold yellow]")
    
    try:
        from elasticsearch import AsyncElasticsearch
        
        es = AsyncElasticsearch(
            hosts=[f"{settings.ELASTICSEARCH_SCHEME}://{settings.ELASTICSEARCH_HOST}:{settings.ELASTICSEARCH_PORT}"],
            basic_auth=(settings.ELASTICSEARCH_USERNAME, settings.ELASTICSEARCH_PASSWORD)
        )
        
        # Test connection
        async with AsyncTimer("ping") as t:
            await es.ping()
        ping_time = t.duration_ms
        console.print(f"   Ping: {ping_time:.1f}ms")
        
        # Test search (without embedding - just to measure ES latency)
        search_times = []
        for i in range(iterations):
            async with AsyncTimer("search") as t:
                await es.search(
                    index=settings.ELASTICSEARCH_INDEX_NAME,
                    body={"query": {"match_all": {}}, "size": 10}
                )
            search_times.append(t.duration_ms)
        
        await es.close()
        
        results = {
            "ping_ms": ping_time,
            "search_avg_ms": statistics.mean(search_times),
        }
        
        console.print(f"   Search avg: [green]{results['search_avg_ms']:.1f}ms[/green]")
        
        return results
        
    except Exception as e:
        console.print(f"   [red]Error: {e}[/red]")
        return {"error": str(e)}


async def profile_mysql(iterations: int = 10) -> Dict[str, float]:
    """Profile MySQL async operations."""
    console.print("\n[bold yellow]4. MYSQL (Async)[/bold yellow]")
    
    try:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text
        
        # Test connection
        async with AsyncSessionLocal() as session:
            async with AsyncTimer("ping") as t:
                await session.execute(text("SELECT 1"))
            ping_time = t.duration_ms
        
        console.print(f"   Ping: {ping_time:.1f}ms")
        
        # Test simple query
        query_times = []
        for i in range(iterations):
            async with AsyncSessionLocal() as session:
                async with AsyncTimer("query") as t:
                    await session.execute(text("SELECT COUNT(*) FROM users"))
                query_times.append(t.duration_ms)
        
        results = {
            "ping_ms": ping_time,
            "query_avg_ms": statistics.mean(query_times),
        }
        
        console.print(f"   Query avg: [green]{results['query_avg_ms']:.1f}ms[/green]")
        
        return results
        
    except Exception as e:
        console.print(f"   [red]Error: {e}[/red]")
        return {"error": str(e)}


async def profile_redis(iterations: int = 10) -> Dict[str, float]:
    """Profile Redis operations."""
    console.print("\n[bold yellow]5. REDIS[/bold yellow]")
    
    try:
        from app.core.database import get_redis
        
        redis = await get_redis()
        
        # Test ping
        async with AsyncTimer("ping") as t:
            await redis.ping()
        ping_time = t.duration_ms
        
        console.print(f"   Ping: {ping_time:.1f}ms")
        
        # Test get/set
        set_times = []
        get_times = []
        
        for i in range(iterations):
            async with AsyncTimer("set") as t:
                await redis.set(f"test_key_{i}", f"test_value_{i}", ex=10)
            set_times.append(t.duration_ms)
            
            async with AsyncTimer("get") as t:
                await redis.get(f"test_key_{i}")
            get_times.append(t.duration_ms)
        
        results = {
            "ping_ms": ping_time,
            "set_avg_ms": statistics.mean(set_times),
            "get_avg_ms": statistics.mean(get_times),
        }
        
        console.print(f"   Set avg: [green]{results['set_avg_ms']:.1f}ms[/green]")
        console.print(f"   Get avg: [green]{results['get_avg_ms']:.1f}ms[/green]")
        
        return results
        
    except Exception as e:
        console.print(f"   [red]Error: {e}[/red]")
        return {"error": str(e)}


async def profile_llm(iterations: int = 3) -> Dict[str, float]:
    """Profile LLM API calls."""
    from app.config import settings
    
    console.print("\n[bold yellow]6. LLM API[/bold yellow]")
    
    if not settings.LLM_TURNED_ON:
        console.print("   [dim]LLM is disabled in settings[/dim]")
        return {"status": "disabled"}
    
    try:
        from google import genai
        from google.genai import types
        
        client = genai.Client(
            api_key=settings.LLM_API_KEY,
            http_options={"base_url": settings.LLM_BASE_URL}
        )
        
        # Test simple generation
        gen_times = []
        for i in range(iterations):
            async with AsyncTimer("generate") as t:
                response = await client.aio.models.generate_content(
                    model=settings.ANSWER_GENERATOR_MODEL_NAME,
                    config=types.GenerateContentConfig(
                        system_instruction="You are a helpful assistant.",
                        temperature=0.1,
                        max_output_tokens=50
                    ),
                    contents="Say hello in one word.",
                )
            gen_times.append(t.duration_ms)
        
        results = {
            "generate_avg_ms": statistics.mean(gen_times),
            "generate_p95_ms": sorted(gen_times)[int(len(gen_times) * 0.95)] if len(gen_times) > 1 else gen_times[0],
        }
        
        console.print(f"   Generate avg: [cyan]{results['generate_avg_ms']:.1f}ms[/cyan]")
        console.print(f"   Generate p95: [cyan]{results['generate_p95_ms']:.1f}ms[/cyan]")
        
        return results
        
    except Exception as e:
        console.print(f"   [red]Error: {e}[/red]")
        return {"error": str(e)}


async def profile_full_rag_pipeline(iterations: int = 3) -> Dict[str, Any]:
    """Profile the complete RAG pipeline with breakdown."""
    from app.config import settings
    
    console.print("\n[bold yellow]7. FULL RAG PIPELINE BREAKDOWN[/bold yellow]")
    
    try:
        from app.core.rag_engine import create_rag_engine
        
        console.print("   Loading RAG engine...")
        with Timer("engine_load") as t:
            engine = create_rag_engine()
        console.print(f"   Engine loaded: {t.duration_ms:.1f}ms")
        
        test_question = "What are the main features of this system?"
        
        pipeline_times = []
        
        for i in range(iterations):
            console.print(f"   Running query {i+1}/{iterations}...")
            
            async with AsyncTimer("full_pipeline") as t:
                result = await engine.query(
                    question=test_question,
                    conversation_history=""
                )
            pipeline_times.append(t.duration_ms)
            console.print(f"     Query {i+1}: {t.duration_ms:.1f}ms")
        
        results = {
            "engine_load_ms": t.duration_ms,
            "pipeline_avg_ms": statistics.mean(pipeline_times),
            "pipeline_min_ms": min(pipeline_times),
            "pipeline_max_ms": max(pipeline_times),
        }
        
        console.print(f"\n   Pipeline avg: [bold cyan]{results['pipeline_avg_ms']:.1f}ms[/bold cyan]")
        console.print(f"   Pipeline range: {results['pipeline_min_ms']:.1f}ms - {results['pipeline_max_ms']:.1f}ms")
        
        return results
        
    except Exception as e:
        console.print(f"   [red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


async def profile_concurrent_requests(n_concurrent: int = 10) -> Dict[str, float]:
    """Test how well the system handles concurrent requests."""
    console.print(f"\n[bold yellow]8. CONCURRENCY TEST ({n_concurrent} concurrent)[/bold yellow]")
    
    try:
        from app.core.rag_engine import create_rag_engine
        
        engine = create_rag_engine()
        test_question = "What is the main topic?"
        
        # First, test if ES vector search works
        console.print("   Testing ES vector search method availability...")
        try:
            has_async = hasattr(engine.es_store, 'asimilarity_search_by_vector')
            has_sync = hasattr(engine.es_store, 'similarity_search_by_vector')
            console.print(f"   asimilarity_search_by_vector: {'✓' if has_async else '✗'}")
            console.print(f"   similarity_search_by_vector: {'✓' if has_sync else '✗'}")
            
            # Test embedding
            test_embedding = engine.embeddings.embed_query("test")
            console.print(f"   Embedding dim: {len(test_embedding)}")
            
            # Test vector search
            if has_sync:
                results = engine.es_store.similarity_search_by_vector(test_embedding, k=3)
                console.print(f"   Vector search test: {len(results)} results")
        except Exception as e:
            console.print(f"   [red]ES test failed: {type(e).__name__}: {e}[/red]")
        
        async def single_query():
            async with AsyncTimer("query") as t:
                await engine.query(question=test_question, conversation_history="")
            return t.duration_ms
        
        # Sequential baseline
        console.print("   Running sequential baseline...")
        sequential_times = []
        for i in range(3):
            time_ms = await single_query()
            sequential_times.append(time_ms)
        seq_avg = statistics.mean(sequential_times)
        console.print(f"   Sequential avg: {seq_avg:.1f}ms")
        
        # Concurrent test
        console.print(f"   Running {n_concurrent} concurrent queries...")
        async with AsyncTimer("concurrent") as t:
            tasks = [single_query() for _ in range(n_concurrent)]
            concurrent_times = await asyncio.gather(*tasks)
        
        total_time = t.duration_ms
        concurrent_avg = statistics.mean(concurrent_times)
        
        # Calculate efficiency
        expected_if_parallel = seq_avg  # If perfectly parallel, same as single
        expected_if_serial = seq_avg * n_concurrent  # If serial, n times slower
        
        parallelism_ratio = expected_if_serial / total_time
        
        results = {
            "sequential_avg_ms": seq_avg,
            "concurrent_avg_ms": concurrent_avg,
            "total_time_ms": total_time,
            "parallelism_ratio": parallelism_ratio,
            "theoretical_max_parallel": n_concurrent,
        }
        
        console.print(f"   Concurrent avg: {concurrent_avg:.1f}ms")
        console.print(f"   Total time for {n_concurrent}: {total_time:.1f}ms")
        console.print(f"   Parallelism ratio: [bold]{parallelism_ratio:.2f}x[/bold] (max: {n_concurrent}x)")
        
        if parallelism_ratio < 2:
            console.print("   [bold red]⚠️  LOW PARALLELISM - Something is serializing requests![/bold red]")
        elif parallelism_ratio < n_concurrent * 0.5:
            console.print("   [yellow]⚠️  Moderate parallelism - Some bottleneck exists[/yellow]")
        else:
            console.print("   [green]✓ Good parallelism[/green]")
        
        return results
        
    except Exception as e:
        console.print(f"   [red]Error: {e}[/red]")
        return {"error": str(e)}


# ============================================================================
# MAIN PROFILER
# ============================================================================

async def run_full_profile():
    """Run complete profiling suite."""
    console.print("\n" + "="*60)
    console.print("[bold magenta]RAG SERVICE PERFORMANCE PROFILER[/bold magenta]")
    console.print("="*60)
    
    results = {}
    
    # 1. Password hashing (likely culprit for login slowness)
    results["password"] = await profile_password_hashing()
    
    # 2. Embedding generation
    results["embedding"] = await profile_embedding_generation()
    
    # 3. Elasticsearch
    results["elasticsearch"] = await profile_elasticsearch()
    
    # 4. MySQL
    results["mysql"] = await profile_mysql()
    
    # 5. Redis
    results["redis"] = await profile_redis()
    
    # 6. LLM
    results["llm"] = await profile_llm()
    
    # 7. Full pipeline
    results["pipeline"] = await profile_full_rag_pipeline()
    
    # 8. Concurrency test
    results["concurrency"] = await profile_concurrent_requests(10)
    
    # Summary
    console.print("\n" + "="*60)
    console.print("[bold magenta]SUMMARY & RECOMMENDATIONS[/bold magenta]")
    console.print("="*60)
    
    # Identify bottlenecks
    bottlenecks = []
    
    if "password" in results and results["password"].get("verify_avg_ms", 0) > 100:
        bottlenecks.append(("🔴 Argon2 Password Verification", 
                          f"{results['password']['verify_avg_ms']:.0f}ms",
                          "Run in ThreadPoolExecutor"))
    
    if "embedding" in results and results["embedding"].get("single_embed_avg_ms", 0) > 100:
        bottlenecks.append(("🟡 Embedding Generation",
                          f"{results['embedding']['single_embed_avg_ms']:.0f}ms",
                          "Use GPU or batch/cache embeddings"))
    
    if "concurrency" in results and results["concurrency"].get("parallelism_ratio", 0) < 3:
        bottlenecks.append(("🔴 Low Parallelism",
                          f"{results['concurrency'].get('parallelism_ratio', 0):.1f}x",
                          "Check for blocking calls or shared resources"))
    
    if bottlenecks:
        table = Table(title="Identified Bottlenecks")
        table.add_column("Issue", style="red")
        table.add_column("Measured", style="yellow")
        table.add_column("Recommendation", style="green")
        
        for issue, measured, rec in bottlenecks:
            table.add_row(issue, measured, rec)
        
        console.print(table)
    else:
        console.print("[green]No obvious bottlenecks found![/green]")
    
    return results


if __name__ == "__main__":
    # Install rich if not available
    try:
        from rich import print
    except ImportError:
        print("Installing rich for pretty output...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "rich"])
        from rich import print
    
    asyncio.run(run_full_profile())

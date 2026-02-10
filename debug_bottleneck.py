# debug_bottleneck.py
import asyncio
import time
import sys
sys.path.insert(0, '.')

async def test_login_parallel():
    """Test if password hashing is actually parallel."""
    from app.core.security import verify_password_async, get_password_hash
    
    # Create a test hash
    test_hash = get_password_hash("testpassword")
    
    print("=" * 60)
    print("LOGIN PARALLELISM TEST")
    print("=" * 60)
    
    # Sequential test
    start = time.perf_counter()
    for i in range(5):
        await verify_password_async("testpassword", test_hash)
    seq_time = (time.perf_counter() - start) * 1000
    print(f"5 Sequential: {seq_time:.0f}ms ({seq_time/5:.0f}ms each)")
    
    # Parallel test
    start = time.perf_counter()
    await asyncio.gather(*[
        verify_password_async("testpassword", test_hash)
        for _ in range(5)
    ])
    par_time = (time.perf_counter() - start) * 1000
    print(f"5 Parallel: {par_time:.0f}ms")
    print(f"Speedup: {seq_time/par_time:.1f}x (should be ~4-5x)")
    
    if par_time > seq_time * 0.8:
        print("❌ PASSWORD HASHING IS NOT PARALLEL!")
    else:
        print("✓ Password hashing is parallel")


async def test_embedding_parallel():
    """Test if embedding is actually using GPU efficiently."""
    from app.config import settings
    from langchain_huggingface import HuggingFaceEmbeddings
    
    print("\n" + "=" * 60)
    print("EMBEDDING PARALLELISM TEST")
    print("=" * 60)
    
    embeddings = HuggingFaceEmbeddings(
        model_name=settings.EMBEDDING_MODEL_PATH,
        model_kwargs={"device": settings.DEVICE, "local_files_only": True, "trust_remote_code": True},
        encode_kwargs={'normalize_embeddings': True}
    )
    
    test_texts = [f"This is test query number {i}" for i in range(10)]
    
    # Sequential embedding
    start = time.perf_counter()
    for text in test_texts:
        _ = embeddings.embed_query(text)
    seq_time = (time.perf_counter() - start) * 1000
    print(f"10 Sequential embeds: {seq_time:.0f}ms ({seq_time/10:.0f}ms each)")
    
    # BATCH embedding (this should be MUCH faster)
    start = time.perf_counter()
    _ = embeddings.embed_documents(test_texts)  # Batch call
    batch_time = (time.perf_counter() - start) * 1000
    print(f"10 Batch embeds: {batch_time:.0f}ms ({batch_time/10:.0f}ms each)")
    print(f"Batch speedup: {seq_time/batch_time:.1f}x")
    
    if batch_time < seq_time * 0.3:
        print("✓ GPU batching works! Sequential calls waste GPU potential")
        print("→ SOLUTION: Implement request batching")
    else:
        print("⚠️ Batching doesn't help much - different issue")


async def test_user_service_uses_async():
    """Check if UserService actually uses async password."""
    print("\n" + "=" * 60)
    print("USER SERVICE ASYNC CHECK")
    print("=" * 60)
    
    import inspect
    from app.services.user_service import UserService
    
    # Check authenticate method
    auth_source = inspect.getsource(UserService.authenticate)
    
    if "verify_password_async" in auth_source:
        print("✓ authenticate() uses verify_password_async")
    elif "await" in auth_source and "verify_password" in auth_source:
        print("⚠️ authenticate() has await but check which verify_password")
        print("   Source snippet:")
        for line in auth_source.split('\n'):
            if 'verify_password' in line:
                print(f"   {line}")
    else:
        print("❌ authenticate() uses SYNC verify_password - THIS IS THE BUG!")
        print("   Source snippet:")
        for line in auth_source.split('\n'):
            if 'verify_password' in line:
                print(f"   {line}")
    
    # Check create_user method
    create_source = inspect.getsource(UserService.create_user)
    if "get_password_hash_async" in create_source:
        print("✓ create_user() uses get_password_hash_async")
    else:
        print("❌ create_user() uses SYNC get_password_hash")


async def main():
    await test_login_parallel()
    await test_embedding_parallel()
    await test_user_service_uses_async()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("""
If password hashing is NOT parallel: 
  → Fix security.py and user_service.py

If batch embedding is 5x+ faster than sequential:
  → The solution is BATCHED EMBEDDING SERVICE
  → Sequential embed_query() wastes GPU potential
  → We need to batch multiple requests together
    """)


if __name__ == "__main__":
    asyncio.run(main())
# diagnostic.py
import asyncio
import sys
sys.path.insert(0, '.')

async def full_diagnostic():
    print("=" * 60)
    print("FULL SYSTEM DIAGNOSTIC")
    print("=" * 60)
    
    # 1. Check if asyncmy is installed
    print("\n1. ASYNC DRIVER CHECK:")
    try:
        import asyncmy
        # print(f"   ✅ asyncmy installed: {asyncmy.__version__}")
    except ImportError:
        print("   ❌ asyncmy NOT INSTALLED!")
        print("   Run: poetry add asyncmy")
        return  # This is likely the problem!
    
    # 2. Check database URL
    print("\n2. DATABASE URL CHECK:")
    from app.config import settings
    print(f"   Original: {settings.DATABASE_URL[:50]}...")
    
    async_url = settings.DATABASE_URL.replace("mysql+pymysql://", "mysql+asyncmy://")
    print(f"   Async: {async_url[:50]}...")
    
    # 3. Check if async engine is actually async
    print("\n3. ASYNC ENGINE CHECK:")
    try:
        from app.core.database import async_engine, AsyncSessionLocal
        print(f"   Engine type: {type(async_engine).__name__}")
        print(f"   Session type: {AsyncSessionLocal}")
        
        # Try to actually use it
        async with AsyncSessionLocal() as session:
            from sqlalchemy import text
            result = await session.execute(text("SELECT 1"))
            val = result.scalar()
            print(f"   ✅ Async query works! Result: {val}")
    except Exception as e:
        print(f"   ❌ Async engine failed: {e}")
    
    # 4. Check get_db dependency
    print("\n4. GET_DB DEPENDENCY CHECK:")
    try:
        from app.core.database import get_db
        import inspect
        
        if inspect.isasyncgenfunction(get_db):
            print("   ✅ get_db is async generator")
        else:
            print("   ❌ get_db is NOT async! This is the problem!")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 5. Check deps.py imports
    print("\n5. DEPS.PY CHECK:")
    try:
        from app.api import deps
        import inspect
        
        source = inspect.getsource(deps.get_current_user)
        if "AsyncSession" in source:
            print("   ✅ Uses AsyncSession")
        else:
            print("   ❌ NOT using AsyncSession!")
        
        if "await UserService" in source:
            print("   ✅ Awaits UserService calls")
        else:
            print("   ❌ NOT awaiting UserService!")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 6. Test actual login flow timing
    print("\n6. LOGIN FLOW TIMING:")
    import time
    from app.core.database import AsyncSessionLocal
    from app.services.user_service import UserService
    
    async with AsyncSessionLocal() as db:
        # Find a test user
        from sqlalchemy import text
        result = await db.execute(text("SELECT email FROM users LIMIT 1"))
        row = result.first()
        if not row:
            print("   ⚠️ No users in database")
            return
        
        test_email = row[0]
        print(f"   Testing with: {test_email}")
        
        # Time get_by_login
        start = time.perf_counter()
        user = await UserService.get_by_login(db, test_email)
        db_time = (time.perf_counter() - start) * 1000
        print(f"   DB query: {db_time:.1f}ms")
        
        if user and user.hashed_password:
            # Time password verification
            from app.core.security import verify_password_async
            start = time.perf_counter()
            await verify_password_async("wrongpassword", user.hashed_password)
            pwd_time = (time.perf_counter() - start) * 1000
            print(f"   Password verify: {pwd_time:.1f}ms")
        
        print(f"   Total expected per login: {db_time + pwd_time:.1f}ms")
    
    # 7. Test concurrent logins simulation
    print("\n7. CONCURRENT LOGIN SIMULATION (10 requests):")
    
    async def simulate_login(user_email):
        start = time.perf_counter()
        async with AsyncSessionLocal() as db:
            user = await UserService.get_by_login(db, user_email)
            if user and user.hashed_password:
                from app.core.security import verify_password_async
                await verify_password_async("test", user.hashed_password)
        return (time.perf_counter() - start) * 1000
    
    # Get test email
    async with AsyncSessionLocal() as db:
        from sqlalchemy import text
        result = await db.execute(text("SELECT email FROM users LIMIT 1"))
        test_email = result.scalar()
    
    # Sequential
    start = time.perf_counter()
    for _ in range(10):
        await simulate_login(test_email)
    seq_total = (time.perf_counter() - start) * 1000
    
    # Concurrent
    start = time.perf_counter()
    await asyncio.gather(*[simulate_login(test_email) for _ in range(10)])
    conc_total = (time.perf_counter() - start) * 1000
    
    print(f"   10 Sequential: {seq_total:.0f}ms")
    print(f"   10 Concurrent: {conc_total:.0f}ms")
    print(f"   Speedup: {seq_total/conc_total:.1f}x")
    
    if conc_total > seq_total * 0.9:
        print("   ❌ NO CONCURRENCY BENEFIT - Event loop is being blocked!")
    else:
        print("   ✅ Concurrency working!")
    
    print("\n" + "=" * 60)
    print("DIAGNOSIS COMPLETE")
    print("=" * 60)

asyncio.run(full_diagnostic())
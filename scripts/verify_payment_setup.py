"""
Payment Service Setup Verification Script

Usage:
    cd /path/to/your/project
    python scripts/verify_payment_setup.py
"""

import sys
import os
import asyncio

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"  📄 Loaded .env from: {env_path}")
    else:
        print(f"  ⚠️  No .env file found at: {env_path}")
except ImportError:
    print("  ⚠️  python-dotenv not installed")

print(f"  📂 Project root: {PROJECT_ROOT}")


def check_main_settings():
    print("\n🔍 Checking main Settings compatibility...")
    try:
        from app.config import Settings
        model_config = getattr(Settings, "model_config", {})
        extra_setting = None
        if isinstance(model_config, dict):
            extra_setting = model_config.get("extra", None)
        elif hasattr(model_config, "get"):
            extra_setting = model_config.get("extra", None)
        if extra_setting == "forbid":
            print("  ❌ Settings has extra='forbid' — this WILL break!")
            print('     FIX: Change extra="forbid" to extra="ignore" in app/config.py')
            return False
        elif extra_setting in ("ignore", "allow"):
            print(f"  ✅ Settings has extra='{extra_setting}' — compatible")
            return True
        else:
            try:
                Settings()
                print("  ✅ Settings loaded without errors — compatible")
                return True
            except Exception as e:
                if "Extra inputs are not permitted" in str(e):
                    print("  ❌ Settings rejects extra env vars")
                    return False
                print(f"  ⚠️  Settings load error: {e}")
                return True
    except ImportError:
        print("  ⚠️  Could not import app.config.Settings — skipping")
        return True
    except Exception as e:
        if "Extra inputs are not permitted" in str(e):
            print("  ❌ Settings crashes on extra env vars!")
            return False
        print(f"  ⚠️  Unexpected error: {e}")
        return True


def check_imports():
    print("\n🔍 Checking imports...")
    checks = [
        ("Config", "app.payment.config", "payment_settings"),
        ("Constants", "app.payment.core.constants", "PaymentStatus"),
        ("Exceptions", "app.payment.exceptions", "PaymentNotFoundException"),
        ("Locker", "app.payment.core.locker", "DistributedLocker"),
        ("Metrics", "app.payment.core.metrics", "PaymentMetrics"),
        ("Payment Model", "app.payment.models.payment", "Payment"),
        ("Reverse Model", "app.payment.models.reverse", "Reverse"),
        ("Wallet Model", "app.payment.models.wallet", "Wallet"),
        ("Discount Model", "app.payment.models.discount", "DiscountCode"),
        ("SEP Client", "app.payment.services.sep_client", "SEPClient"),
        ("Payment Service", "app.payment.services.payment_service", "PaymentService"),
        ("Reverse Service", "app.payment.services.reverse_service", "ReverseService"),
        ("Wallet Service", "app.payment.services.wallet_service", "WalletService"),
        ("Discount Service", "app.payment.services.discount_service", "DiscountService"),
        ("Double Spend Guard", "app.payment.services.double_spend_guard", "DoubleSpendGuard"),
        ("Payment Router", "app.payment.routers", "payment_router"),
    ]
    all_ok = True
    for name, module, attr in checks:
        try:
            mod = __import__(module, fromlist=[attr])
            getattr(mod, attr)
            print(f"  ✅ {name}: {module}.{attr}")
        except Exception as e:
            print(f"  ❌ {name}: {module}.{attr} — {e}")
            all_ok = False
    return all_ok


def check_config():
    print("\n🔍 Checking configuration...")
    try:
        from app.payment.config import payment_settings as ps
        checks = {
            "SEP_TERMINAL_ID": ps.SEP_TERMINAL_ID,
            "SEP_PAYMENT_URL": ps.SEP_PAYMENT_URL,
            "SEP_VERIFY_URL": ps.SEP_VERIFY_URL,
            "SEP_REVERSE_URL": ps.SEP_REVERSE_URL,
            "PAYMENT_CALLBACK_URL": ps.PAYMENT_CALLBACK_URL,
            "FRONTEND_PAYMENT_RESULT_URL": ps.FRONTEND_PAYMENT_RESULT_URL,
            "MIN_PAYMENT_AMOUNT": ps.MIN_PAYMENT_AMOUNT,
            "MAX_PAYMENT_AMOUNT": ps.MAX_PAYMENT_AMOUNT,
            "PAYMENT_REVERSE_WINDOW_MINUTES": ps.PAYMENT_REVERSE_WINDOW_MINUTES,
        }
        all_ok = True
        for key, value in checks.items():
            if value:
                print(f"  ✅ {key}: {str(value)[:50]}")
            else:
                print(f"  ⚠️  {key}: NOT SET")
                if key in ("SEP_TERMINAL_ID", "PAYMENT_CALLBACK_URL"):
                    all_ok = False
        return all_ok
    except Exception as e:
        print(f"  ❌ Failed to load config: {e}")
        return False


def check_models():
    print("\n🔍 Checking database models...")
    try:
        from app.payment.models.payment import Payment
        from app.payment.models.reverse import Reverse
        from app.payment.models.wallet import Wallet, WalletTransaction
        from app.payment.models.discount import DiscountCode, DiscountUsage
        models = [
            (Payment, "payments", ["id", "user_id", "res_num", "ref_num", "amount", "status"]),
            (Reverse, "reverses", ["id", "payment_id", "ref_num", "status"]),
            (Wallet, "wallets", ["id", "user_id", "balance"]),
            (WalletTransaction, "wallet_transactions", ["id", "wallet_id", "amount", "tx_type"]),
            (DiscountCode, "discount_codes", ["id", "code", "discount_type", "discount_value"]),
            (DiscountUsage, "discount_usages", ["id", "discount_code_id", "user_id"]),
        ]
        all_ok = True
        for model, table_name, required_cols in models:
            actual_table = model.__tablename__
            actual_cols = [c.name for c in model.__table__.columns]
            if actual_table != table_name:
                print(f"  ❌ {model.__name__}: expected '{table_name}', got '{actual_table}'")
                all_ok = False
            else:
                missing = [c for c in required_cols if c not in actual_cols]
                if missing:
                    print(f"  ❌ {model.__name__}: missing columns: {missing}")
                    all_ok = False
                else:
                    print(f"  ✅ {model.__name__}: table='{table_name}', {len(actual_cols)} columns")
        return all_ok
    except Exception as e:
        print(f"  ❌ Failed to check models: {e}")
        return False


def check_routes():
    print("\n🔍 Checking routes...")
    try:
        from app.payment.routers import payment_router
        routes = []
        for route in payment_router.routes:
            if hasattr(route, "methods") and hasattr(route, "path"):
                for method in route.methods:
                    routes.append(f"{method} {route.path}")
        expected = [
            "POST /initiate", "POST /callback", "GET /list",
            "GET /{payment_id}", "POST /{payment_id}/reverse",
            "GET /{payment_id}/reverses", "GET /wallet/balance",
            "GET /wallet/transactions", "POST /discount/create",
            "POST /discount/validate", "GET /metrics",
        ]
        all_ok = True
        for exp in expected:
            method, path = exp.split(" ", 1)
            found = any(r.startswith(method) and path in r for r in routes)
            if found:
                print(f"  ✅ {exp}")
            else:
                print(f"  ❌ {exp} — NOT FOUND")
                all_ok = False
        print(f"\n  Total routes registered: {len(routes)}")
        return all_ok
    except Exception as e:
        print(f"  ❌ Failed to check routes: {e}")
        return False


async def check_redis():
    print("\n🔍 Checking Redis connection...")
    try:
        import redis.asyncio as aioredis
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        client = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        try:
            await client.ping()
            print(f"  ✅ Redis: connected at {redis_url[:30]}...")
            return True
        finally:
            await client.aclose()
    except ImportError:
        print("  ❌ Redis: redis package not installed")
        return False
    except Exception as e:
        print(f"  ❌ Redis: {e}")
        return False


def main():
    print("=" * 60)
    print("  Payment Service Setup Verification")
    print("=" * 60)

    settings_ok = check_main_settings()
    if not settings_ok:
        print("\n" + "=" * 60)
        print("  ⛔ CRITICAL: Fix your main Settings class first!")
        print("=" * 60)
        print('  FIX: Change extra="forbid" to extra="ignore" in app/config.py')
        sys.exit(1)

    results = {
        "Main Settings": settings_ok,
        "Imports": check_imports(),
        "Config": check_config(),
        "Models": check_models(),
        "Routes": check_routes(),
    }

    try:
        results["Redis"] = asyncio.run(check_redis())
    except Exception as e:
        print(f"  ❌ Redis: {e}")
        results["Redis"] = False

    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    all_passed = True
    for name, passed in results.items():
        icon = "✅" if passed else "❌"
        print(f"  {icon} {name}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("  🎉 All checks passed! Payment service is ready.")
        print("  Next: alembic upgrade head → uvicorn app.main:app --reload → /docs")
    else:
        print("  ⚠️  Some checks failed. Fix the issues above.")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
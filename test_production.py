#!/usr/bin/env python3
"""
Quick test script to verify the production setup works correctly.
"""

import os
import sys
import requests
import time
import subprocess
import signal
from threading import Thread

def test_wsgi_import():
    """Test that the WSGI module can be imported correctly."""
    try:
        import wsgi
        print("✅ WSGI module imports successfully")
        return True
    except ImportError as e:
        print(f"❌ WSGI import failed: {e}")
        return False

def test_gunicorn_config():
    """Test that gunicorn configuration is valid."""
    try:
        import gunicorn.config
        config = gunicorn.config.Config()
        config.set('config', 'gunicorn.conf.py')
        print("✅ Gunicorn configuration is valid")
        return True
    except Exception as e:
        print(f"❌ Gunicorn config error: {e}")
        return False

def test_app_startup():
    """Test that the application starts up correctly with gunicorn."""
    print("🚀 Testing application startup with gunicorn...")
    
    # Set test environment variables
    env = os.environ.copy()
    env['PORT'] = '5001'  # Use different port for testing
    env['FLASK_ENV'] = 'production'
    
    try:
        # Start gunicorn process
        process = subprocess.Popen([
            'gunicorn', 
            '--bind', '127.0.0.1:5001',
            '--workers', '1',
            '--timeout', '30',
            '--worker-class', 'eventlet',
            '--access-logfile', '-',
            '--error-logfile', '-',
            'wsgi:application'
        ], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        
        # Wait longer for the server to start and capture output
        print("   Waiting for server to start...")
        time.sleep(5)
        
        # Check if process is still running
        if process.poll() is not None:
            # Process has terminated, get the output
            stdout, _ = process.communicate()
            print(f"❌ Gunicorn process terminated. Output:")
            print(stdout)
            return False
        
        # Test if the server responds
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"   Attempt {attempt + 1}/{max_retries} to connect...")
                response = requests.get('http://127.0.0.1:5001/', timeout=10)
                if response.status_code == 200:
                    print("✅ Application responds correctly on port 5001")
                    success = True
                    break
                else:
                    print(f"   Status code: {response.status_code}")
                    success = False
            except requests.exceptions.RequestException as e:
                print(f"   Connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                success = False
        
        if not success:
            # Get server output for debugging
            stdout, _ = process.communicate(timeout=5)
            print("❌ Server output for debugging:")
            print(stdout)
        
        # Clean up
        try:
            process.terminate()
            process.wait(timeout=5)
        except:
            process.kill()
        
        return success
        
    except FileNotFoundError:
        print("❌ Gunicorn not found. Make sure it's installed: pip install gunicorn")
        return False
    except Exception as e:
        print(f"❌ Failed to start application: {e}")
        return False

def main():
    """Run all tests."""
    print("🔍 Testing Sava Game Production Setup")
    print("=" * 40)
    
    tests = [
        test_wsgi_import,
        test_gunicorn_config,
        test_app_startup
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print("=" * 40)
    print(f"📊 Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Your production setup is ready.")
        print("\nTo start the application in production:")
        print("  ./start_production.sh")
        print("\nOr manually:")
        print("  gunicorn --config gunicorn.conf.py wsgi:application")
        return 0
    else:
        print("❌ Some tests failed. Please check the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
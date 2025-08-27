from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .api.v1 import Endpoints  
import time
import logging


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# the main application instance
app = FastAPI(
    title="NL2SQL Core Engine",
    description="API for the Natural Language to SQL project",
    version="1.0.0", 
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    
    # Log incoming request
    print(f"📥 {request.method} {request.url.path}")
    
    # Process the request
    response = await call_next(request)
    
    # Log completion with timing
    duration = time.time() - start_time
    status_emoji = "✅" if response.status_code < 400 else "❌"
    print(f"{status_emoji} {request.method} {request.url.path} - {duration:.2f}s - Status: {response.status_code}")
    
    return response

# 2. Global Error Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Log the error for debugging
    print(f"🚨 Unhandled error on {request.method} {request.url.path}: {str(exc)}")
    
    # Return clean error response
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "An unexpected error occurred",
            "details": str(exc)[:150],  # Truncate long errors
            "path": str(request.url.path)
        }
    )


# plugin the endpoints router
app.include_router(
    Endpoints.router,
    prefix="/v1" 
)

@app.get("/", tags=["Health Check"])
def health_check():
    """
    health check to confirm the server is running.
    """
    return {"status": "ok", "message": "NL2SQL API is running."}

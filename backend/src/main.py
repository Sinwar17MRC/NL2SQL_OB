from fastapi import FastAPI
from .api.v1 import endpoints  

# the main application instance
app = FastAPI(
    title="NL2SQL Core Engine",
    description="API for the Natural Language to SQL project",
    version="1.0.0", 
)

# plugin the endpoints router
app.include_router(
    endpoints.router,
    prefix="/v1" 
)

@app.get("/", tags=["Health Check"])
def health_check():
    """
    health check to confirm the server is running.
    """
    return {"status": "ok", "message": "NL2SQL API is running."}
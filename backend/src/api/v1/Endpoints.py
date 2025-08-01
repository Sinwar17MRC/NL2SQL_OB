from fastapi import APIRouter, HTTPException, Body
from ..models import DBConnectionRequest, NLQueryRequest, ConnectionTestResponse, QueryDataResponse, SchemaOverviewResponse, SchemaDetailedResponse
from ...services.Schema_manager import SchemaManager
import uuid

# After we develop the AI-agents actual logic, the code here will continue like this:
# from ...core import pipeline



router = APIRouter()


# Store active connections per session
active_connections = {}  # This is a simple in-memory store. In production, i'll use redis.


@router.post("/test-connection", response_model=ConnectionTestResponse, tags=["1. Database Connection"])
def test_db_connection(request: DBConnectionRequest):
    """
    Quick connection test to validate credentials.
    Returns minimal info for UI .
    """
    print(f"Testing connection to: {request.db_url[:50]}...")
    
    try:
        schema_manager = SchemaManager(request.db_url)
        schema_manager.test_connection()
        
        # Generate session ID and store connection
        connection_id = str(uuid.uuid4())
        active_connections[connection_id] = schema_manager
        
        # Extract database name from URL  
        db_name = schema_manager.engine.url.database or "Unknown"
        
        return ConnectionTestResponse(
            status="success",
            message="Database connection established",
            database_name=db_name,
            connection_id=connection_id
        )
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/schema/overview", response_model=SchemaOverviewResponse,  tags=["2. Database Introspection"])
def get_schema_overview(connection_id: str):
    """
    Lightweight schema for sidebar - just table names and primary keys.
    Called when user connects to the database and is in the chatting interface .
    """
    if connection_id not in active_connections:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    schema_manager = active_connections[connection_id]
    
    try:
        # Get overview: top 2 schemas with their top 3 tables each
        overview_data = schema_manager.get_schema_overview()
        db_name = schema_manager.engine.url.database or "Unknown"
        
        return SchemaOverviewResponse(
            database_name=db_name,
            table_count=len(overview_data["tables"]), 
            tables=overview_data["tables"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/schema/detailed", response_model=SchemaDetailedResponse,  tags=["2. Database Introspection"])
def get_detailed_schema(connection_id: str):
    """
    Full detailed schema - for the detailed tab feature or LLM context.
    Only called when user clicks detailed view or when LLM needs context.
    """
    if connection_id not in active_connections:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    schema_manager = active_connections[connection_id]
    
    try:
        detailed_schema = schema_manager.get_detailed_schema()
        return SchemaDetailedResponse(schema=detailed_schema)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/query", response_model=QueryDataResponse,  tags=["3. Chat Queries"])
def process_nl_query(request: NLQueryRequest):
    """
    Chat queries - uses existing connection session.
    """
    if request.connection_id not in active_connections:
        raise HTTPException(status_code=404, detail="Connection session expired")
    
    schema_manager = active_connections[request.connection_id]
    
    try:
        print(f"Processing query: '{request.question}'")
        
        # Get schema context for LLM 
        schema_context = schema_manager.get_detailed_schema()
        
        # The LLM pipeline will be implemented here
        # result = pipeline.run_query_pipeline(request.question, schema_context)
        
        # mock data using real schema
        mock_result = {
            "original_question": request.question,
            "generated_sql": f"SELECT * FROM {schema_context['tables'][0]['name']} LIMIT 10;",
            "data": [{"status": "Using persistent connection"}]
        }
        
        return QueryDataResponse(**mock_result)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/disconnect", tags=["1. Database Connection"])
def disconnect_database(connection_id: str):
    """
    Clean disconnect - disposes connection and removes from session.
    Called when user logs out or switches databases.
    """
    if connection_id in active_connections:
        active_connections[connection_id].dispose_engine()
        del active_connections[connection_id]
        return {"status": "disconnected"}
    
    return {"status": "connection not found"}
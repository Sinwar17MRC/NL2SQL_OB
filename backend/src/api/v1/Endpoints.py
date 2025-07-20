from fastapi import APIRouter, HTTPException, Body
from ..models import DBConnectionRequest, NLQueryRequest, ConnectionTestResponse, QueryDataResponse

# After we develop the AI-agents actual logic, the code here will start like this:
# from ..services import schema_manager
# from ..core import pipeline



router = APIRouter()



@router.post(
    "/test-connection",
    response_model=ConnectionTestResponse,
    tags=["1. Database Connection"]
)
def test_db_connection(request: DBConnectionRequest):
    """
    Tests the database connection using the provided connection string.
    """
    
    print(f"Received request to test connection for: {request.db_url[:50]}...") # Log first 50 chars
    
    try:
        # Here I'll implement the full logic later like :
        # schema_data = schema_manager.test_connection_and_get_schema(request.db_url)
        
        # For now, I'll return a sample response to test the API structure.
        mock_schema_data = {
            "tables": [
                {"name": "Sales.Customers", "columns": ["CustomerID", "FirstName"]},
                {"name": "Sales.Orders", "columns": ["OrderID", "OrderDate"]}
            ]
        }
        
        return ConnectionTestResponse(schema=mock_schema_data)

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")


@router.post(
    "/query",
    response_model=QueryDataResponse,
    tags=["2. Core Query Pipeline"]
)
def process_nl_query(request: NLQueryRequest):

    """
    Receives the user's question and DB credentials,
    and returns the generated SQL and the resulting data.
    """


    print(f"Received query: '{request.question}'")
    
    try:
        # Sample logic to run the query pipeline : 
        # result = pipeline.run_query_pipeline(
        #     question=request.question,
        #     db_url=request.db_connection.db_url
        # )
        
        
        mock_result = {
            "original_question": request.question,
            "generated_sql": "SELECT CustomerID, FirstName FROM Sales.Customers LIMIT 10; -- (mocked)",
            "data": [
                {"CustomerID": 1, "FirstName": "John"},
                {"CustomerID": 2, "FirstName": "Jane"}
            ]
        }
        
        return QueryDataResponse(**mock_result)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred in the pipeline: {str(e)}")
from pydantic import BaseModel, Field
from typing import List, Dict, Any

# REQUEST MODELS
class DBConnectionRequest(BaseModel):
    """
    A model to verify db connection request.
    FastAPI will ensure the incoming JSON has a 'db_url' key and its value is a string.
    """
    db_url: str = Field(
        ...,  # required field
        example="mssql+pyodbc://user:pass@host:port/db?driver=...",
        description="Full SQLAlchemy connection string for the database."
    )

class NLQueryRequest(BaseModel):
    """
    The model to verify the user's question and database connection details.
    FastAPI will ensure the incoming JSON has a 'question' key and its value is a string.
    It also includes the database connection details.
    """
    question: str = Field(
        ...,
        example="Show me the top 5 customers by total sales.",
        description="The nl question from the user."
    )
    db_connection: DBConnectionRequest


# RESPONSE MODELS 

class ConnectionTestResponse(BaseModel):
    """
    Verify the response sent back after a successful connection test.
    """
    status: str = "success"
    message: str = "Connection successful and schema retrieved."
    schema: Dict[str, Any] 

class QueryDataResponse(BaseModel):
    """
    Verify response sent back after processing a natural language query.
    This includes the original question, generated SQL, and the resulting data.
    """
    original_question: str
    generated_sql: str
    data: List[Dict[str, Any]] 
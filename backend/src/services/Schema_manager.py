from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from typing import Dict, List, Any

class SchemaManager:
    """
    A class to manage database connections and schema introspection.
    It is initialized with a db_url and provides separate methods
    to test the connection, get table names, and get the full detailed schema.
    """
    def __init__(self, db_url: str):
        try:
            self.db_url = db_url
            self.engine = create_engine(db_url)
            self.inspector = inspect(self.engine)
        except Exception as e:
            raise ConnectionError(f"Failed to create SQLAlchemy engine. Check URL format. Error: {e}")

    def test_connection(self) -> bool:
        """
        Tests if a live connection can be made to the database.
        Returns True if successful, raises ConnectionError otherwise.
        """
        try:
            with self.engine.connect() as connection:
                print(f"SUCCESS: Connection to {self.engine.url.host} verified.")
                return True
        except SQLAlchemyError as e:
            print(f"ERROR: SQLAlchemy connection failed: {e}")
            raise ConnectionError(f"Database connection failed. Check URL/credentials. Error: {e}")

    def get_table_names(self) -> List[str]:
        """
        Retrieves a simple list of all table names in the database schema.
        This is useful for displaying a list to the user in the UI.
        """
        try:
            return self.inspector.get_table_names()
        except SQLAlchemyError as e:
            raise ConnectionError(f"Failed to retrieve table names. Check permissions. Error: {e}")

    def get_detailed_schema(self) -> Dict[str, Any]:
        """
        This method provides a detailed introspection of the entire database schema.
        This is the rich context needed for the AI agents.
        """
        try:
            schema_info = {"tables": []}
            table_names = self.get_table_names()

            for table_name in table_names:
                columns = []
                for col in self.inspector.get_columns(table_name):
                    columns.append({"name": col['name'], "type": str(col['type'])})

                pk_constraint = self.inspector.get_pk_constraint(table_name)
                fks = self.inspector.get_foreign_keys(table_name)
                
                # Formatting the display of foreign keys for a cleaner JSON output
                foreign_keys = [
                    {
                        "constrained_columns": fk['constrained_columns'],
                        "referred_table": fk['referred_table'],
                        "referred_columns": fk['referred_columns']
                    } for fk in fks
                ]


                # Handling exceptions(no primary key) :
                primary_key = []
                if pk_constraint:
                    primary_key = pk_constraint.get('constrained_columns', [])

                schema_info["tables"].append({
                    "name": table_name,
                    "columns": columns,
                    "primary_key": primary_key if primary_key else None,  
                    "foreign_keys": foreign_keys
                })

            return schema_info
        except SQLAlchemyError as e:
            raise ConnectionError(f"Failed to build detailed schema. Check permissions. Error: {e}")

    def dispose_engine(self):
        " cleaning up the engine connection's pool"
        if self.engine:
            self.engine.dispose()
            print("INFO: SQLAlchemy engine disposed.")
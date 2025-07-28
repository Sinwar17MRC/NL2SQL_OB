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

    def _get_table_row_count(self, schema_name: str, table_name: str) -> int:
        """Helper to get row count for a table."""
        try:
            with self.engine.connect() as connection:
                if 'mssql' in str(self.engine.url):
                    query = text(f"""
                    SELECT SUM(rows) as row_count
                    FROM sys.partitions 
                    WHERE object_id = OBJECT_ID('{schema_name}.{table_name}') 
                    AND index_id IN (0,1)
                    """)
                else:
                    query = text(f'SELECT COUNT(*) as row_count FROM "{schema_name}"."{table_name}"')
                
                result = connection.execute(query)
                row_count = result.fetchone()[0]
                return int(row_count) if row_count else 0
        except Exception:
            return 0
    
    def get_schema_overview(self) -> Dict[str, Any]:
        """
        Get overview showing only the TOP 2 SCHEMAS by total row count.
        Each schema shows its top 3 most dense tables. (UI conception purpose)
        """
        try:
            schema_names = self.inspector.get_schema_names()
            schema_summaries = []  # Will store schema info with total row counts
            
            # Step 1: For each schema, get top 3 tables and calculate total rows
            for schema_name in schema_names:
                # Skip system schemas
                if schema_name.lower() in ['information_schema', 'sys', 'guest']:
                    continue
                    
                table_names = self.inspector.get_table_names(schema=schema_name)
                schema_tables = []
                
                print(f"Processing schema '{schema_name}' with {len(table_names)} tables")
                
                # Get info for all tables in this schema
                for table_name in table_names:
                    try:
                        pk_constraint = self.inspector.get_pk_constraint(table_name, schema=schema_name)
                        primary_keys = pk_constraint.get('constrained_columns', []) if pk_constraint else None
                        column_count = len(self.inspector.get_columns(table_name, schema=schema_name))
                        row_count = self._get_table_row_count(schema_name, table_name)
                        
                        schema_tables.append({
                            "name": f"{schema_name}.{table_name}",
                            "schema": schema_name,
                            "table_name": table_name,
                            "primary_keys": primary_keys,
                            "column_count": column_count,
                            "row_count": row_count
                        })
                        
                    except Exception as e:
                        print(f"Warning: Could not process table '{schema_name}.{table_name}': {e}")
                        continue
                
                # Sort tables by row count and take top 3
                schema_tables.sort(key=lambda x: x['row_count'], reverse=True)
                top_3_tables = schema_tables[:3]
                
                # Calculate total row count for this schema's top 3 tables
                total_rows = sum(table['row_count'] for table in top_3_tables)
                
                schema_summaries.append({
                    "schema_name": schema_name,
                    "top_tables": top_3_tables,
                    "total_row_count": total_rows
                })
                
                print(f"Schema '{schema_name}': {len(top_3_tables)} tables, {total_rows:,} total rows")
            
            # Step 2: Sort schemas by total row count and take top 2
            schema_summaries.sort(key=lambda x: x['total_row_count'], reverse=True)
            top_2_schemas = schema_summaries[:2]
            
            # Step 3: Collect all tables from the top 2 schemas
            final_tables = []
            for schema_summary in top_2_schemas:
                final_tables.extend(schema_summary['top_tables'])
                print(f"Selected schema '{schema_summary['schema_name']}' with {schema_summary['total_row_count']:,} total rows")
            
            print(f"Overview: Returning {len(final_tables)} tables from {len(top_2_schemas)} schemas")
            return {"tables": final_tables}
            
        except SQLAlchemyError as e:
            raise ConnectionError(f"Failed to build schema overview. Error: {e}")
        
    def get_detailed_schema(self) -> Dict[str, Any]:
        """
        This method provides a detailed introspection of the entire database schema.
        This is the rich context needed for the AI agents.
        """
        try:
            all_tables = []
            schema_names = self.inspector.get_schema_names()
            
            for schema_name in schema_names:
                # Skip system schemas
                if schema_name.lower() in ['information_schema', 'sys', 'guest']:
                    continue
                    
                table_names = self.inspector.get_table_names(schema=schema_name)
                print(f"Getting detailed info for schema '{schema_name}' with {len(table_names)} tables")
                
                for table_name in table_names:
                    try:
                        # Get full column details
                        columns = []
                        for col in self.inspector.get_columns(table_name, schema=schema_name):
                            columns.append({"name": col['name'], "type": str(col['type'])})

                        # Get primary key
                        pk_constraint = self.inspector.get_pk_constraint(table_name, schema=schema_name)
                        primary_key = pk_constraint.get('constrained_columns', []) if pk_constraint else []

                        # Get foreign keys
                        fks = self.inspector.get_foreign_keys(table_name, schema=schema_name)
                        foreign_keys = [
                            {
                                "constrained_columns": fk['constrained_columns'],
                                "referred_table": fk['referred_table'],
                                "referred_columns": fk['referred_columns']
                            } for fk in fks
                        ]

                        all_tables.append({
                            "name": f"{schema_name}.{table_name}",
                            "schema": schema_name,
                            "table_name": table_name,
                            "columns": columns,
                            "primary_key": primary_key if primary_key else None,
                            "foreign_keys": foreign_keys
                        })
                        
                    except Exception as e:
                        print(f"Warning: Could not process table '{schema_name}.{table_name}': {e}")
                        continue

            print(f"Detailed schema: Returning {len(all_tables)} tables from all schemas")
            return {"tables": all_tables}
            
        except SQLAlchemyError as e:
            raise ConnectionError(f"Failed to build detailed schema. Error: {e}")

    def dispose_engine(self):
        " cleaning up the engine connection's pool"
        if self.engine:
            self.engine.dispose()
            print("INFO: SQLAlchemy engine disposed.")
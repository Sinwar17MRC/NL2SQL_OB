class QueryExecutor:
    def execute_query(self, sql: str, connection_id: str) -> Dict:
        try:
            schema_manager = active_connections[connection_id]
            
            with schema_manager.engine.connect() as connection:
                result = connection.execute(text(sql))
                data = [dict(row) for row in result.fetchall()]
                
                return {
                    "generated_sql": sql,
                    "data": data,
                    "success": True,
                    "row_count": len(data)
                }
                
        except Exception as e:
            return self._categorize_error(e, sql, connection_id)
    
    def _categorize_error(self, error: Exception, sql: str, connection_id: str) -> Dict:
        error_msg = str(error).lower()
        
        # Permission/Access errors
        if any(keyword in error_msg for keyword in 
               ['permission denied', 'access denied', 'insufficient privilege']):
            return {
                "generated_sql": sql,
                "data": [],
                "success": False,
                "error_type": "permission_denied",
                "message": "Access denied. You don't have permission to access the requested data.",
                "suggestion": "Try asking for different information or contact your database administrator."
            }
        
        # Table/Column doesn't exist (LLM hallucination)
        elif any(keyword in error_msg for keyword in 
                 ['invalid object', 'table', 'column', 'not found', 'does not exist']):
            available_tables = self._get_available_table_names(connection_id)
            return {
                "generated_sql": sql,
                "data": [],
                "success": False,
                "error_type": "object_not_found",
                "message": "The requested table or column doesn't exist in your database.",
                "suggestion": f"Available tables: {', '.join(available_tables[:5])}...",
                "available_tables": available_tables
            }
        
        # Syntax errors (malformed SQL)
        elif any(keyword in error_msg for keyword in 
                 ['syntax error', 'incorrect syntax', 'parse error']):
            return {
                "generated_sql": sql,
                "data": [],
                "success": False,
                "error_type": "syntax_error",
                "message": "The generated SQL has syntax errors.",
                "suggestion": "Try rephrasing your question in simpler terms."
            }
        
        # General database errors
        else:
            return {
                "generated_sql": sql,
                "data": [],
                "success": False,
                "error_type": "database_error",
                "message": f"Database error: {str(error)[:150]}",
                "suggestion": "Try a simpler query or check your database connection."
            }
    
    def _get_available_table_names(self, connection_id: str) -> List[str]:
        """Get actual available table names to suggest alternatives."""
        try:
            schema_manager = active_connections[connection_id]
            schema = schema_manager.get_detailed_schema()
            return [table['name'] for table in schema['tables']]
        except:
            return []
"""
RAG Service for Natural Language to SQL (NL2SQL) Application

This module provides a RAGService class that manages a vector knowledge base
built from database schemas. It supports both indexing (onboarding phase) and
searching (live query phase) operations using sentence-transformers and ChromaDB.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings
from langchain.embeddings import HuggingFaceEmbeddings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RAGService:
    """
    A service for managing vector-based retrieval of database schema information.
    
    This class handles the creation and querying of a vector index built from
    database schema information. It uses sentence-transformers for embeddings
    and ChromaDB for vector storage, with each database connection isolated
    in its own collection.
    
    Attributes:
        connection_id (str): Unique identifier for the database connection
        db_path (str): Path to the persistent ChromaDB storage
        embedding_model (SentenceTransformer): Pre-loaded embedding model
        client (chromadb.PersistentClient): ChromaDB client instance
        collection (chromadb.Collection): ChromaDB collection for this connection
    """
    
    def __init__(self, connection_id: str, db_path: str = "./rag_db") -> None:
        """
        Initialize the RAG service with embedding model and vector database.
        
        Args:
            connection_id: Unique identifier for the database connection
            db_path: Path to store the persistent ChromaDB database
            
        Raises:
            ValueError: If connection_id is empty or invalid
            RuntimeError: If model or database initialization fails
        """
        if not connection_id or not connection_id.strip():
            raise ValueError("connection_id cannot be empty or None")
            
        self.connection_id = connection_id.strip()
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        self.db_path = os.path.join(project_root, 'rag_db')
        
        # Initialize embedding model
        logger.info("Loading embedding model 'intfloat/e5-base-multilingual'...")
        try:
            self.embedding_model = HuggingFaceEmbeddings(
                model_name="intfloat/e5-base-multilingual"
            )
            logger.info("Successfully loaded embedding model")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise RuntimeError(f"Failed to initialize embedding model: {e}")
        
        # Initialize ChromaDB client
        logger.info(f"Initializing ChromaDB persistent client at '{db_path}'...")
        try:
            # Ensure the database directory exists
            os.makedirs(db_path, exist_ok=True)
            
            self.client = chromadb.PersistentClient(
                path=db_path,
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
            
            # Get or create collection for this connection
            self.collection = self.client.get_or_create_collection(
                name=self.connection_id,
                metadata={"description": f"Schema index for connection {self.connection_id}"}
            )
            
            logger.info(f"Successfully initialized collection '{self.connection_id}'")
            
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")
            raise RuntimeError(f"Failed to initialize vector database: {e}")
    
    def _build_rag_document_from_schema(self, table_schema: Dict[str, Any]) -> str:
        """
        Build a rich document string from a table schema for embedding.
        
        This method creates a comprehensive, semantically rich document that
        prioritizes the most important information (table name, description)
        at the beginning for better retrieval performance.
        
        Args:
            table_schema: Dictionary containing enriched table schema information
            
        Returns:
            Formatted document string ready for embedding
            
        Raises:
            ValueError: If table_schema is missing required fields
        """
        if not isinstance(table_schema, dict):
            raise ValueError("table_schema must be a dictionary")
        
        # Extract key information with defaults
        table_name = table_schema.get('table_name', 'Unknown')
        schema_name = table_schema.get('schema', '')
        full_table_id = f"{schema_name}.{table_name}"
        
        # Start with most important information
        document_parts = []
        
        # Table identification (highest priority)
        if schema_name:
            document_parts.append(f"Table: {full_table_id}")
        else:
            document_parts.append(f"Table: {table_name}")
        
        # LLM-generated description (very high priority)
        if table_schema.get('llm_description'):
            document_parts.append(f"Description: {table_schema['llm_description']}")
        
        # Business purpose and domain context
        if table_schema.get('business_hints'):
            document_parts.append(f"Business hints: {table_schema['business_hints']}")
        
        # Column information 
        columns = table_schema.get('columns', [])
        primary_key = table_schema.get('primary_key', [])
        if columns:
            column_lines = []
            for col in columns:
                col_name = col.get('name', 'unknown')
                col_type = col.get('type', 'unknown')
                pk_marker = " (Primary Key)" if col_name in primary_key else ""
                notes = f" [Note: {col['notes']}]" if col.get('notes') else ""
                column_lines.append(f"  - {col_name} ({col_type}){pk_marker}{notes}")
            
            document_parts.append("Columns:\n" + "\n".join(column_lines))
        
        # Relationships and constraints
        relationships_info = table_schema.get('relationships_info', [])
        if relationships_info:
            relationship_lines = []
            for rel in relationships_info:
                target = f"{rel.get('target_schema', '')}.{rel.get('target_table', '')}"
                rel_type = rel.get('relationship_type', 'general')
                relationship_lines.append(f"  - Connects to {target} via {rel.get('via_column')} (Type: {rel_type})")
            
            document_parts.append("Relationships:\n" + "\n".join(relationship_lines))
            
        if table_schema.get('row_count') is not None:
            document_parts.append(f"Approximate Rows: {table_schema['row_count']}")
        
        # Sample data context 
        sample_data = table_schema.get('sample_data', [])
        if sample_data:
            sample_data_lines = []
            for i, row in enumerate(sample_data):
                row_str = ", ".join([f"{k}='{v}'" for k, v in row.items()])
                sample_data_lines.append(f"  - Row {i+1}: {{ {row_str} }}")
            document_parts.append("Sample Data:\n" + "\n".join(sample_data_lines))
        # Join the parts with clear separators
        document = '\n'.join(document_parts)
        
        logger.debug(f"Built document for table '{full_table_id}' ({len(document)} characters)")
        return document
    
    def create_index(self, final_enriched_schema: Dict[str, Any]) -> None:
        """
        Create a vector index from the enriched database schema.
        
        Args:
            final_enriched_schema: The final dictionary prepared by SchemaManager
                                 
        Raises:
            ValueError: If schema is empty or malformed
        """
        if not final_enriched_schema or "tables" not in final_enriched_schema:
            raise ValueError("final_enriched_schema is invalid or missing 'tables' key")
        
        tables_list = final_enriched_schema.get('tables', [])
        
        if not tables_list:
            logger.warning("No tables found in schema - nothing to index")
            return
            
        logger.info(f"Starting index creation for {len(tables_list)} tables...")

        # --- Implement batch processing for efficiency ---
        documents = []
        metadatas = []
        ids = []

        for table_data in tables_list:
            # The full_table_id is the unique identifier for our vector store
            full_table_id = f"{table_data.get('schema', '')}.{table_data.get('table_name', '')}"
            
            # Build the rich document for embedding
            doc = self._build_rag_document_from_schema(table_data)
            documents.append(doc)
            
            # Store essential info in metadata for quick retrieval
            hints_str = ", ".join(table_schema.get('business_hints', []))
            
            metadata = {
                'full_table_id': full_table_id,
                'table_name': table_schema.get('table_name', ''),
                'schema_name': table_schema.get('schema', ''),
                'column_count': len(table_schema.get('columns', [])),
                'row_count': table_schema.get('row_count', 0),
                'business_hints': hints_str # Store as a comma-separated string
            }
            metadatas.append(metadata)
            
            # The ID for each vector must be unique
            ids.append(full_table_id)

        if not documents:
            logger.warning("No documents were generated for indexing.")
            return

        # Add all prepared documents to the collection in a single, efficient batch
        try:
            self.collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            logger.info(f"Successfully indexed {len(documents)} tables in a single batch.")
        except Exception as e:
            logger.error(f"Failed to add documents to ChromaDB collection: {e}")
            raise RuntimeError(f"Indexing process failed: {e}")

    def search(self, user_question: str, k: int = 5) -> List[Dict[str, Any]]:
        """
        Search the vector index for relevant tables based on user question.
        
        This method converts the user's question to an embedding and retrieves
        the most semantically similar table schemas from the vector database.
        
        Args:
            user_question: Natural language question from the user
            k: Number of results to retrieve (default: 5)
            
        Returns:
            List of dictionaries containing retrieved results with keys:
            - id: Table identifier
            - document: Original document text
            - metadata: Table metadata
            - distance: Similarity distance (lower is more similar)
            
        Raises:
            ValueError: If user_question is empty or k is invalid
            RuntimeError: If search operation fails
        """
        if not user_question or not user_question.strip():
            raise ValueError("user_question cannot be empty or None")
        
        if k <= 0:
            raise ValueError("k must be a positive integer")
        
        user_question = user_question.strip()
        logger.info(f"Searching for relevant tables for question: '{user_question[:100]}...'")

        try:
            # Generate query embedding
            logger.debug("Generating query embedding...")
            query_embedding = self.embedding_model.encode(
                [user_question],
                convert_to_numpy=True
            ).tolist()
            
            # Search in ChromaDB
            logger.debug(f"Querying ChromaDB for top {k} results...")
            results = self.collection.query(
                query_embeddings=query_embedding,
                n_results=min(k, self.collection.count()),  # Don't request more than available
                include=['documents', 'metadatas', 'distances']
            )
            
            # Parse and format results
            formatted_results = []
            
            if results['ids'] and results['ids'][0]:  
                ids = results['ids'][0]
                documents = results['documents'][0] if results['documents'] else []
                metadatas = results['metadatas'][0] if results['metadatas'] else []
                distances = results['distances'][0] if results['distances'] else []
                
                for i, doc_id in enumerate(ids):
                    result = {
                        'id': doc_id,
                        'document': documents[i] if i < len(documents) else '',
                        'metadata': metadatas[i] if i < len(metadatas) else {},
                        'distance': distances[i] if i < len(distances) else 1.0
                    }
                    formatted_results.append(result)
            
            logger.info(f"Search completed. Found {len(formatted_results)} relevant tables")
            
            # Log top results 
            for i, result in enumerate(formatted_results[:2]):
                table_name = result['metadata'].get('full_table_id', result['id'])
                distance = result['distance']
                logger.debug(f"Result {i+1}: {table_name} (distance: {distance:.4f})")
            
            return formatted_results
            
        except Exception as e:
            logger.error(f"Search operation failed: {e}")
            raise RuntimeError(f"Search failed: {e}")
    
    def get_collection_info(self) -> Dict[str, Any]:
        """
        Get information about the current collection.
        
        Returns:
            Dictionary containing collection statistics and metadata
        """
        try:
            count = self.collection.count()
            collection_metadata = self.collection.metadata or {}
            
            return {
                'connection_id': self.connection_id,
                'document_count': count,
                'collection_metadata': collection_metadata,
                'db_path': self.db_path
            }
        except Exception as e:
            logger.error(f"Failed to get collection info: {e}")
            return {
                'connection_id': self.connection_id,
                'error': str(e)
            }
    
    def clear_index(self) -> None:
        """
        Clear all documents from the current collection.
        
        Raises:
            RuntimeError: If clearing operation fails
        """
        try:
            logger.info(f"Clearing index for connection '{self.connection_id}'...")
            
            # Get all existing IDs and delete them
            existing_data = self.collection.get()
            if existing_data['ids']:
                self.collection.delete(ids=existing_data['ids'])
                logger.info(f"Successfully cleared {len(existing_data['ids'])} documents")
            else:
                logger.info("Index was already empty")
                
        except Exception as e:
            logger.error(f"Failed to clear index: {e}")
            raise RuntimeError(f"Failed to clear index: {e}")
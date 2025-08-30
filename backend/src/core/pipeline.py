import logging
import json
from typing import Dict, Any, List

from ..services.Schema_manager import SchemaManager
from ..services.clustering_service import ClusteringService
from ..services.llm_services import LLMService
from ..services.rag_service import RAGService
# We will also need a Redis client for persisting the cluster map
# we will import it here later

logger = logging.getLogger(__name__)

# The Onboarding Pipeline 

async def run_db_onboarding_pipeline(connection_id: str, db_url: str) -> Dict[str, Any]:
    """
    Orchestrates the entire end-to-end process of introspecting, analyzing,
    and generating semantic metadata for a new database connection.
    """
    logger.info(f"Starting DB onboarding pipeline for connection_id: {connection_id}")
    
    try:
        # 1. INITIALIZE SERVICES
        schema_manager = SchemaManager(db_url)
        llm_service = LLMService()

        # 2. GET MASTER SCHEMA DATA 
        all_tables_enhanced_schema = schema_manager.get_enhanced_schema_for_rag()
        if not all_tables_enhanced_schema.get('tables'):
            logger.warning(f"Onboarding failed for {connection_id}: No processable tables found.")
            return {"status": "failed", "reason": "No tables found"}

        all_tables_map = {t['name']: t for t in all_tables_enhanced_schema['tables']}

        # 3. STRUCTURAL ANALYSIS (Clustering)
        clustering_service = ClusteringService(all_tables_enhanced_schema['tables'])
        clusters_metadata = clustering_service.cluster_tables(refinement_threshold=15)

        # 4. SEMANTIC ENRICHMENT (Generate LLM Descriptions)
        multi_table_clusters = [c for c in clusters_metadata if not c['is_singleton']]
        singleton_clusters = [c for c in clusters_metadata if c['is_singleton']]
        final_table_descriptions = {}

        # A) Process multi-table clusters
        for cluster_meta in multi_table_clusters:
            cluster_schema_data = [all_tables_map[tid] for tid in cluster_meta['tables']]
            descriptions_map = await llm_service.generate_descriptions_for_cluster(
                cluster_schema_data=cluster_schema_data,
                metadata=cluster_meta
            )
            final_table_descriptions.update(descriptions_map)

        # B) Process singleton clusters in batches
        singleton_table_ids = [c['tables'][0] for c in singleton_clusters]
        SINGLETON_BATCH_SIZE = 10
        
        for i in range(0, len(singleton_table_ids), SINGLETON_BATCH_SIZE):
            batch_ids = singleton_table_ids[i:i + SINGLETON_BATCH_SIZE]
            batch_schema_data = [all_tables_map[tid] for tid in batch_ids]
            
            descriptions_map = await llm_service.generate_descriptions_for_singleton_batch(batch_schema_data)
            final_table_descriptions.update(descriptions_map)

        # 5. FINAL SCHEMA ASSEMBLY
        logger.info("Assembling the final, fully enriched schema with LLM descriptions.")
        for table_data in all_tables_enhanced_schema['tables']:
            table_id = table_data['name']
            table_data['llm_description'] = final_table_descriptions.get(table_id, "No description generated.")

        # 6. PREPARE DATA FOR API LAYER
        # Create the cluster map needed for live Graph RAG queries
        cluster_map_for_rag = {
            table_id: cluster['tables'] 
            for cluster in clusters_metadata 
            for table_id in cluster['tables']
        }

        logger.info(f"DB onboarding pipeline completed successfully for {connection_id}.")
        
        # The pipeline's job is to return the finished data products.
        # The API layer will handle the final actions (indexing, saving to Redis).
        return {
            "status": "success",
            "final_schema": all_tables_enhanced_schema,
            "cluster_map": cluster_map_for_rag
        }

    except Exception as e:
        logger.error(f"CRITICAL ERROR in onboarding pipeline for {connection_id}: {e}", exc_info=True)
        return {"status": "failed", "reason": str(e)}

# The Live Query Pipeline 

async def process_user_query(connection_id: str, user_question: str, redis_client) -> Dict[str, Any]:
    """
    Orchestrates the live query process: RAG retrieval, SQL generation, and execution.
    """
    logger.info(f"Processing user query for connection_id: {connection_id}")
    
    try:
        # 1. INITIALIZE SERVICES
        rag_service = RAGService(connection_id)
        llm_service = LLMService()
        # db_executor would be initialized with the user's SchemaManager instance

        # 2. RAG RETRIEVAL (Step 1: Vector Search)
        # We get back a list of dicts: 
        search_results = rag_service.search(user_question, k=5)
        entry_point_table_ids = [result['id'] for result in search_results]

        # 3. GRAPH RAG EXPANSION (Step 2: Neighborhood Lookup)
        cluster_map_json = redis_client.get(f"cluster_map:{connection_id}")
        if not cluster_map_json:
            raise ValueError("Cluster map not found in Redis. Onboarding may have failed or is still processing.")
        cluster_map = json.loads(cluster_map_json)

        final_context_table_ids = set()
        for table_id in entry_point_table_ids:
            neighborhood = cluster_map.get(table_id)
            if neighborhood:
                final_context_table_ids.update(neighborhood)
        
        if not final_context_table_ids:
             # Fallback: if no cluster info, just use the direct search results
            final_context_table_ids.update(entry_point_table_ids)

        logger.info(f"Final RAG context includes tables: {final_context_table_ids}")

        # 4. ASSEMBLE FINAL CONTEXT FOR LLM
        context_schema_str = "Relevant Tables:\n" + "\n".join([f"- {tid}" for tid in final_context_table_ids])

        # 5. SQL GENERATION
        generated_sql = await llm_service.generate_sql_query(user_question, context_schema_str)
        
        # 6. SECURITY GATE & EXECUTION (Future Steps)
        # is_safe = security_gate.validate(generated_sql)
        # if is_safe:
        #     data = db_executor.execute(generated_sql)
        #     return {"status": "success", "sql": generated_sql, "data": data}
        # else:
        #     return {"status": "failed", "reason": "Generated SQL failed security check."}

        return {"status": "success", "sql": generated_sql, "context_tables": list(final_context_table_ids)}

    except Exception as e:
        logger.error(f"CRITICAL ERROR in query pipeline for {connection_id}: {e}", exc_info=True)
        return {"status": "failed", "reason": str(e)}
"""
ClusteringService for grouping database tables based on foreign key relationships.

This module provides functionality to cluster related database tables together
by analyzing their foreign key relationships as an undirected graph. This is used
as an optimization step in Natural Language to SQL (NL2SQL) applications to reduce
the number of LLM calls when generating descriptive metadata.
"""

import logging
from typing import Any, Dict, List, Set, Optional
import networkx as nx

# Get a logger instance for this module. The configuration will be handled
# by the main application entry point (e.g., main.py).
logger = logging.getLogger(__name__)

class ClusteringService:
    """
    A service for clustering database tables based on foreign key relationships.
    
    This class models database schemas as undirected graphs where:
    - Each table is a node (identified by full_table_id)
    - Each foreign key relationship is an edge
    - Clusters are the connected components of the graph
    
    Attributes:
        _tables_data: List of table dictionaries containing schema information.
        _graph: NetworkX undirected graph representing table relationships.
        _clusters: A cached list of the computed clusters to avoid re-computation.
    """
    
    def __init__(self, tables_data: List[Dict[str, Any]]) -> None:
        """
        Initialize the ClusteringService with table data.
        
        Args:
            tables_data: List of dictionaries, each representing a table with:
                - schema_name: Schema name (str)
                - table_name: Table name (str) 
                - full_table_id: Full table identifier (str)
                - foreign_keys: List of foreign key relationships (List[Dict])
                
        Raises:
            ValueError: If tables_data is empty or None.
            TypeError: If tables_data is not a list.
        """
        if not isinstance(tables_data, list):
            raise TypeError("tables_data must be a list")
        
        if not tables_data:
            raise ValueError("tables_data cannot be empty")
        
        self._tables_data = tables_data
        self._graph = nx.Graph()
        
        # Initialize cache for lazy loading ---
        self._clusters: Optional[List[List[str]]] = None
        
        logger.info(f"Initialized ClusteringService with {len(tables_data)} tables")
    
    def _build_relationship_graph(self) -> None:
        """
        Build an undirected graph from table foreign key relationships.
        
        This method is idempotent and safe to call multiple times, but will
        only build the graph if it hasn't been built already.
        """
        if self._graph.number_of_nodes() > 0:
            return  # Graph already built

        logger.info("Building relationship graph...")
        
        # First pass: Add all tables as nodes
        table_ids: Set[str] = set()
        for table in self._tables_data:
            full_table_id = table.get("full_table_id")
            if full_table_id:
                self._graph.add_node(full_table_id)
                table_ids.add(full_table_id)
        
        logger.debug(f"Added {len(table_ids)} table nodes to graph")
        
        # Second pass: Add edges for foreign key relationships
        edges_added = 0
        for table in self._tables_data:
            source_table_id = table.get("full_table_id")
            if not source_table_id:
                continue
                
            foreign_keys = table.get("foreign_keys", [])
            for fk in foreign_keys:
                referred_schema = fk.get("referred_schema", "")
                referred_table = fk.get("referred_table", "")
                
                if referred_schema and referred_table:
                    referred_table_id = f"{referred_schema}.{referred_table}"
                    
                    if referred_table_id not in table_ids:
                        self._graph.add_node(referred_table_id)
                        logger.debug(f"Added referenced table node: {referred_table_id}")
                    
                    self._graph.add_edge(source_table_id, referred_table_id)
                    edges_added += 1
                    logger.debug(f"Added edge: {source_table_id} <-> {referred_table_id}")
        
        logger.info(f"Graph construction complete. Nodes: {self._graph.number_of_nodes()}, "
                    f"Edges: {edges_added}")
    
    def cluster_tables(self) -> List[List[str]]:
        """
        Cluster tables based on their foreign key relationships.
        
        This method builds the relationship graph and finds connected components,
        where each component represents a cluster of related tables. The result
        is cached after the first computation.
        
        Returns:
            List[List[str]]: List of clusters, where each cluster is a sorted list of 
            full_table_id strings. The clusters are sorted by size (descending).
        """
        if self._clusters is not None:
            return self._clusters

        logger.info("Starting table clustering process...")
        self._build_relationship_graph()
        
        connected_components = list(nx.connected_components(self._graph))
        
        clusters = []
        for component in connected_components:
            cluster = sorted(list(component))
            clusters.append(cluster)
            logger.debug(f"Found cluster with {len(cluster)} tables: {cluster}")
        
        clusters.sort(key=len, reverse=True)
        
        self._clusters = clusters
        
        logger.info(f"Clustering complete. Found {len(self._clusters)} clusters.")
        for i, cluster in enumerate(self._clusters, 1):
            logger.info(f"  Cluster {i}: {len(cluster)} tables - {cluster[0]}...")
        
        return self._clusters
    
    def get_cluster_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the clustering results. Uses cached results if available.
        
        Returns:
            Dict containing clustering statistics.
        """
        clusters = self.cluster_tables()
        
        if not clusters:
            return {
                "total_tables": len(self._tables_data),
                "total_clusters": 0,
                "largest_cluster_size": 0,
                "singleton_clusters": 0
            }
        
        cluster_sizes = [len(cluster) for cluster in clusters]
        
        return {
            "total_tables": len(self._tables_data),
            "total_clusters": len(clusters),
            "largest_cluster_size": max(cluster_sizes),
            "singleton_clusters": sum(1 for size in cluster_sizes if size == 1)
        }


# Example usage  for easy testing.
'''
if __name__ == "__main__":
    # --- ADDED: A basic logging config for standalone testing ---
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    sample_tables = [
        # ... (sample data) ...
    ]
    
    service = ClusteringService(sample_tables)

    # First call - will compute
    print("\\n--- First Call ---")
    clusters_first_run = service.cluster_tables()
    print(f"Clusters: {clusters_first_run}")

    # Second call - will be instant from cache
    print("\\n--- Second Call (from cache) ---")
    clusters_second_run = service.cluster_tables()
    print(f"Clusters: {clusters_second_run}")
    
    stats = service.get_cluster_statistics()
    print(f"\\nStatistics: {stats}")
'''
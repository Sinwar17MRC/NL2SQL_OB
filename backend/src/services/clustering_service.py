"""
ClusteringService for grouping database tables based on foreign key relationships.

This module provides functionality to cluster related database tables together
by analyzing their foreign key relationships as an undirected graph. This is used
as an optimization step in Natural Language to SQL (NL2SQL) applications to reduce
the number of LLM calls when generating descriptive metadata.
"""

import hashlib
import logging
import itertools
from difflib import SequenceMatcher
import re
import os
from typing import Any, Dict, List, Optional
import networkx as nx
from networkx.algorithms import community

logger = logging.getLogger(__name__)

# A similarity function for column names
def text_similarity(s1: str, s2: str, weights: dict = None) -> float:
    """
    Calculate text similarity using a weighted combination of multiple techniques:
    1. Word-level similarity (Jaccard index on token sets)
    2. Character sequence similarity (SequenceMatcher ratio)
    3. Common prefix length
    4. Acronym similarity
    
    Args:
        s1 (str): The first string to compare.
        s2 (str): The second string to compare.
        weights (dict, optional): A dictionary to override the default weights.
                                  Defaults to {'word': 0.4, 'char': 0.3, 
                                  'prefix': 0.2, 'acronym': 0.1}.

    Returns:
        float: A composite similarity score between 0 and 1.
    """
    # --- Default weights can be overridden for testing ---
    if weights is None:
        weights = {
            'word': 0.4,
            'char': 0.3,
            'prefix': 0.2,
            'acronym': 0.1
        }

    def tokenize(s: str) -> list:
        # Split on common delimiters and camelCase
        s = re.sub(r'([a-z])([A-Z])', r'\1 \2', s)
        return re.split(r'[_\s-]+', s.lower())
    
    def get_acronym(tokens: list) -> str:
        return ''.join(word[0] for word in tokens if word)
    
    # Pre-tokenize for efficiency
    tokens1 = tokenize(s1)
    tokens2 = tokenize(s2)

    # 1. Word-level similarity (Jaccard Index = A(intersection)B/AUB)
    words1 = set(tokens1)
    words2 = set(tokens2)
    word_sim = len(words1.intersection(words2)) / max(len(words1.union(words2)), 1)
    
    # 2. Character sequence similarity
    s1_lower, s2_lower = s1.lower(), s2.lower()
    char_sim = SequenceMatcher(None, s1_lower, s2_lower).ratio()
    
    # 3. Prefix/Suffix similarity
    prefix_len = len(os.path.commonprefix([s1_lower, s2_lower]))
    # Normalize by the length of the longer string
    prefix_sim = prefix_len / max(len(s1), len(s2), 1)
    
    # 4. Acronym similarity
    acr1 = get_acronym(tokens1)
    acr2 = get_acronym(tokens2)
    acronym_sim = SequenceMatcher(None, acr1, acr2).ratio() if acr1 and acr2 else 0.0
    
    # Weighted combination
    final_score = (
        weights['word'] * word_sim +
        weights['char'] * char_sim +
        weights['prefix'] * prefix_sim +
        weights['acronym'] * acronym_sim
    )
    
    return final_score

class ClusteringService:
    """
    An advanced service for clustering database tables using a weighted graph model.
    It supports automatic refinement of large clusters via community detection and
    automated tuning of algorithm parameters using quality metrics.
    """
    def __init__(self, tables_data: List[Dict[str, Any]]) -> None:
        if not isinstance(tables_data, list) or not tables_data:
            raise ValueError("tables_data must be a non-empty list")
        
        self._tables_data = tables_data
        self._table_map = {t['full_table_id']: t for t in tables_data}
        self._graph: Optional[nx.Graph] = None
        self._cache={}

        sorted_table_ids = sorted([t['full_table_id'] for t in tables_data])
        schema_string = ",".join(sorted_table_ids)
        self._schema_fingerprint = hashlib.md5(schema_string.encode()).hexdigest()
        
        logger.info(f"Initialized ClusteringService for schema with fingerprint: {self._schema_fingerprint}")
    
    def _build_weighted_graph(self) -> None:
        """
        Builds a weighted, undirected graph from table relationships.
        The edge weight represents the "strength" of the relationship.
        """
        if self._graph is not None:
            return

        logger.info("Building weighted relationship graph...")
        self._graph = nx.Graph()
        
        for table in self._tables_data:
            self._graph.add_node(table['full_table_id'])

        for source_table in self._tables_data:
            source_id = source_table['full_table_id']
            for fk in source_table.get('foreign_keys', []):
                target_schema = fk.get('referred_schema', source_table.get('schema_name'))
                target_name = fk.get('referred_table')
                if not target_name: continue
                
                target_id = f"{target_schema}.{target_name}"
                target_table = self._table_map.get(target_id)
                if not target_table: continue

                # --- Edge Weight Calculation ---
                weight = 1.0
                
                # 1. Semantic Strength (Business Hints)
                source_hints = set(source_table.get('business_hints', []))
                target_hints = set(target_table.get('business_hints', []))
                if source_hints.intersection(target_hints):
                    weight += 0.5
                
                # 2. Naming Convention Strength
                fk_col = fk['constrained_columns'][0]
                pk_col = (target_table.get('primary_key') or ['id'])[0]
                weight += 0.3 * text_similarity(fk_col, pk_col)
                
                # 3. Cross-Schema Penalty
                if source_table.get('schema_name') != target_schema:
                    weight -= 0.4
                
                self._graph.add_edge(source_id, target_id, weight=max(0.1, weight)) # Ensure weight is positive

        logger.info(f"Weighted graph built. Nodes: {self._graph.number_of_nodes()}, Edges: {self._graph.number_of_edges()}")

    def find_best_community_partition(self, component: set) -> List[List[str]]:
        """
        Tests multiple resolution values for the Louvain algorithm and selects
        the partition with the highest modularity score.
        """
        subgraph = self._graph.subgraph(component)
        best_partition = None
        best_modularity = -1
        
        # Test a range of resolution values to find the best one
        resolutions_to_test = [0.8, 1.0, 1.2, 1.5]
        logger.info(f"  -> Finding best partition for component of size {len(component)}...")
        
        for res in resolutions_to_test:
            partition = community.louvain_communities(subgraph, resolution=res, weight='weight')
            modularity = community.modularity(subgraph, partition, weight='weight')
            logger.debug(f"  - Resolution {res}: Modularity={modularity:.4f}, Communities={len(partition)}")
            
            if modularity > best_modularity:
                best_modularity = modularity
                best_partition = partition
        
        logger.info(f"  -> Best partition found with modularity {best_modularity:.4f}, resulting in {len(best_partition)} communities.")
        return [sorted(list(p)) for p in best_partition]

    def cluster_tables(self, refinement_threshold: int = 10) -> List[Dict[str, Any]]:
        """
        Clusters tables and generates rich metadata for each cluster.
        Automatically refines large clusters using quality-tuned community detection.
        
        Args:
            refinement_threshold: The size above which a cluster is considered "large".
        
        Returns:
            A list of dictionaries, where each dict represents a cluster and its metadata.
        """
        cache_key = f"{self._schema_fingerprint}_thresh_{refinement_threshold}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        self._build_weighted_graph()
        
        final_groups_metadata = []
        components = list(nx.connected_components(self._graph))
        
        for i, component in enumerate(components):
            parent_id = str(i+1)
            component_subgraph = self._graph.subgraph(component)
            
            if len(component) > refinement_threshold:
                # Refine this large component
                refined_communities = self.find_best_community_partition(component)
                for j, comm in enumerate(refined_communities):
                    comm_subgraph = self._graph.subgraph(comm)
                    cluster_id = f"{parent_id}-{j+1}"
                    metadata = self._get_rich_cluster_metadata(subgraph=comm_subgraph, cluster_id=cluster_id, parent_component_id=parent_id, is_refined=True)
                    final_groups_metadata.append(metadata)
            else:
                # Keep this small component as a single cluster
                metadata = self._get_rich_cluster_metadata(subgraph=component_subgraph, cluster_id=parent_id, parent_component_id=parent_id, is_refined=False)
                final_groups_metadata.append(metadata)

        final_groups_metadata.sort(key=lambda x: x['size'], reverse=True)
        self._cache[cache_key] = final_groups_metadata
        return final_groups_metadata

    def _get_rich_cluster_metadata(self, subgraph: nx.Graph, cluster_id: str, parent_component_id: str, is_refined: bool) -> Dict[str, Any]:
        """Analyzes a subgraph (representing a cluster) to extract rich metadata."""
        nodes = list(subgraph.nodes)
        
        # Centrality helps find the "most important" table in a cluster
        try:
            centrality = nx.degree_centrality(subgraph)
            central_table = max(centrality, key=centrality.get)
        except ValueError:
            central_table = nodes[0] if nodes else None
        
        density = nx.density(subgraph)

        density_label = "Low"
        if density >= 0.75:
            density_label = "Very High (Clique-like)"
        elif density >= 0.5:
            density_label = "High (Tightly Coupled)"
        elif density >= 0.25:
            density_label = "Medium (Related)"

        return {
            "cluster_id": cluster_id,
            "parent_component_id": parent_component_id,
            "size": len(nodes),
            "tables": sorted(nodes),
            "density_value": round(density, 2),
            "density_label": density_label,
            "central_table": central_table,
            "is_singleton": len(nodes) == 1,
            "was_refined": is_refined
        }